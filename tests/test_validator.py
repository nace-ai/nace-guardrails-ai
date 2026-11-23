from importlib.metadata import version
from types import SimpleNamespace

import pytest
from guardrails import Guard
from nace_sdk import Noul, RetryPolicy, Score, WorkspaceFileSource, image_input

from drex_policy import DrexPolicy
from drex_policy.main import CLIENT_INFO, DrexPolicyError

from .fakes import FakeClient, mock_client, noul, score

TOXIC = Noul(instructions="Is this text toxic?")


def guard(client: FakeClient, model: str | None = None) -> Guard:
    return Guard().use(
        DrexPolicy(
            question=TOXIC, fail_at=0.5, model=model, client=client, on_fail="exception"
        )
    )


def test_pass():
    client = noul(0.1)
    result = guard(client).validate("hello")
    assert result.validation_passed is True
    assert result.validated_output == "hello"
    assert client.calls == [("hello", {"policy": TOXIC}, "drex-max-v1.1", None)]


def test_fail():
    with pytest.raises(Exception) as exc_info:
        guard(noul(0.9)).validate("you idiot")
    assert str(exc_info.value) == (
        "Validation failed for field with errors: Drex P(yes)=0.9 >= fail_at=0.5"
    )


def test_fail_when_no():
    validator = DrexPolicy(
        question=TOXIC, fail_at=0.5, fail_when="no", client=noul(0.1)
    )
    result = validator.validate("hello", {})
    assert result.outcome == "fail"
    assert result.error_message == "Drex P(yes)=0.1 < fail_at=0.5"


def test_fail_keeps_answer_in_metadata():
    validator = DrexPolicy(question=TOXIC, fail_at=0.5, client=noul(0.9))
    result = validator.validate("you idiot", {})
    assert result.outcome == "fail"
    assert result.metadata == {
        "drex_model": "drex-max-v1.1",
        "drex_response_model": "drex-max-v1.1",
        "drex_usage": {"input_tokens": 4, "image_tokens": 0, "output_tokens": 1},
        "drex_request_id": None,
        "drex_evaluation_time_ms": None,
        "drex_measured": 0.9,
        "drex_fail_at": 0.5,
        "drex_fail_when": "yes",
        "drex_question_id": "policy",
        "drex_answer": {"type": "noul", "noul": 0.9},
    }


def test_score_at_or_above_and_below():
    question = Score(instructions="How toxic?", criteria=["none", "low", "high"])
    high = DrexPolicy(question=question, fail_at=1.5, client=score(2.0))
    assert high.validate("x", {}).outcome == "fail"
    below = DrexPolicy(
        question=question, fail_at=1.5, fail_when="below", client=score(2.0)
    )
    assert below.validate("x", {}).outcome == "pass"


def test_drex_latest_is_forwarded_unchanged():
    client = noul(0.1)
    guard(client, model="drex-latest").validate("hello")
    assert client.calls[0][2] == "drex-latest"


def test_images_go_to_drex_max():
    client = noul(0.1)
    validator = DrexPolicy(question=TOXIC, fail_at=0.5, model="drex-max", client=client)
    validator.validate("caption", {"images": ["data:image/png;base64,AAAA"]})
    assert client.calls[0][2:] == ("drex-max", ["data:image/png;base64,AAAA"])


def test_images_without_drex_max_are_refused():
    model = "drex-v2.0"
    client = noul(0.1)
    validator = DrexPolicy(question=TOXIC, fail_at=0.5, model=model, client=client)
    with pytest.raises(DrexPolicyError, match="drex-max-v1.1"):
        validator.validate("caption", {"images": ["data:image/png;base64,AAAA"]})
    assert client.calls == []


def test_more_than_eight_images_are_refused():
    client = noul(0.1)
    validator = DrexPolicy(question=TOXIC, fail_at=0.5, model="drex-max", client=client)
    with pytest.raises(DrexPolicyError, match="at most 8 images"):
        validator.validate("caption", {"images": ["data:image/png;base64,AAAA"] * 9})
    assert client.calls == []


def test_omitted_instructions_stay_none():
    client = noul(0.1)
    DrexPolicy(
        question=Noul(criteria={"true": "Toxic text"}), fail_at=0.5, client=client
    ).validate("hello", {})
    sent = client.calls[0][1]["policy"]
    assert sent.instructions is None
    assert sent.model_dump(exclude_none=True) == {
        "type": "noul",
        "criteria": {"true": "Toxic text"},
    }


def test_built_client_settings(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("drex_policy.main.NaceClient", Recorder)
    DrexPolicy(question=TOXIC, fail_at=0.5)._get_client()
    assert seen["client_info"] == CLIENT_INFO == f"drex-policy/{version('drex-policy')}"
    assert seen["timeout"] == 60
    assert seen["retry"] == RetryPolicy(timeout=180)
    assert "model" not in seen


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fail_at": 1.5},
        {"fail_at": 0.5, "fail_when": "below"},
        {"fail_at": 0.5, "question_id": ""},
        {"fail_at": 0.5, "model": ""},
    ],
)
def test_bad_config(kwargs):
    with pytest.raises(ValueError):
        DrexPolicy(question=TOXIC, **kwargs)


@pytest.mark.parametrize("threshold", [0.0, 0.5, 1.0])
@pytest.mark.parametrize(("direction", "expected"), [("yes", "fail"), ("no", "pass")])
def test_noul_boundary(threshold, direction, expected):
    validator = DrexPolicy(TOXIC, threshold, direction, client=noul(threshold))
    assert validator.validate("x").outcome == expected


@pytest.mark.parametrize("value", [float("nan"), -0.01, 1.01, float("inf")])
def test_unusable_probability(value):
    with pytest.raises(DrexPolicyError):
        DrexPolicy(TOXIC, 0.5, client=noul(value)).validate("x")


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_threshold(threshold):
    with pytest.raises(ValueError):
        DrexPolicy(TOXIC, threshold)


@pytest.mark.parametrize(
    "model", [None, "drex-latest", "drex-max-v1.1", "drex-max-finance", "drex-max"]
)
def test_image_models_forward_images_in_order(model):
    client = noul(0.1)
    images = [
        "AAAA",
        image_input(WorkspaceFileSource(workspace_id="ws_test", file_id="file_test")),
    ]
    DrexPolicy(TOXIC, 0.5, model=model, client=client).validate("x", {"images": images})
    assert client.calls == [("x", {"policy": TOXIC}, model or "drex-max-v1.1", images)]


def test_http_image_is_refused_before_call():
    client = noul(0.1)
    with pytest.raises(DrexPolicyError, match=r"image_input\(\)"):
        DrexPolicy(TOXIC, 0.5, client=client).validate(
            "x", {"images": ["https://example.com/image.png"]}
        )
    assert client.calls == []


@pytest.mark.parametrize("question", [Noul(), Noul(criteria={"true": None})])
def test_empty_noul_is_refused_at_construction(question):
    with pytest.raises(ValueError, match="instructions or criteria"):
        DrexPolicy(question, 0.5)


@pytest.mark.parametrize("question", [TOXIC, Score(criteria=["none", "high"])])
def test_guard_serialization_round_trip(question, monkeypatch):
    import httpx

    def handler(request):
        return httpx.Response(503, json={})

    monkeypatch.setenv("GUARDRAILS_RUN_SYNC", "true")
    monkeypatch.setattr(
        "drex_policy.main.NaceClient",
        lambda **kwargs: mock_client(
            handler, **(kwargs | {"retry": RetryPolicy(max_retries=0)})
        ),
    )
    guard = Guard().use(
        DrexPolicy(
            question, 0.5, question_id="check", on_error="fail", fix_value="safe"
        )
    )
    serialized = guard.to_dict()
    restored = Guard.from_dict(serialized)
    assert restored.to_dict() == serialized
    properties = serialized["validators"][0]["kwargs"]
    assert properties["on_error"] == "fail"
    assert properties["fix_value"] == "safe"
    try:
        with pytest.raises(Exception, match="Drex could not decide"):
            restored.validate("x")
        result = restored.history.last.validator_logs[0].validation_result
        assert result.outcome == "fail"
        assert result.fix_value == "safe"
        assert result.metadata["drex_error"]["status"] == 503
    finally:
        restored._validator_map["$"][0].close()


def test_original_constructor_arguments():
    client = noul(0.2)
    validator = DrexPolicy(TOXIC, 0.5, "no", "scope", "drex-v2.0", client, "noop")
    result = validator.validate("x")
    assert result.error_message == "Drex P(yes)=0.2 < fail_at=0.5 [scope]"
    assert client.calls == [("x", {"scope": TOXIC}, "drex-v2.0", None)]


@pytest.mark.parametrize("question", [TOXIC, Score(criteria=["none", "low", "high"])])
@pytest.mark.parametrize("fail_at", [0, 3])
def test_sdk_answer_is_preserved(question, fail_at):
    if isinstance(question, Noul) and fail_at == 3:
        fail_at = 1
    client = noul(0.43456789) if isinstance(question, Noul) else score(1.7654321)
    response = client.system_one("x", {"policy": question}, model="response-model")
    client = SimpleNamespace(system_one=lambda *args, **kwargs: response)
    result = DrexPolicy(
        question, fail_at, model="requested-model", client=client
    ).validate("x")
    assert result.outcome == ("fail" if fail_at == 0 else "pass")
    assert result.metadata["drex_answer"] == response.answers["policy"].model_dump(
        mode="json", exclude_none=True
    )
    assert result.metadata["drex_response_model"] == "response-model"
    assert result.metadata["drex_request_id"] is None


def test_http_response_metadata_and_attribution(monkeypatch):
    import httpx

    from .fakes import payload

    def handler(request):
        assert request.headers["x-nace-client"] == CLIENT_INFO
        # nace-sdk 0.7.0 first appended client_info to User-Agent, per its changelog.
        sdk_version = tuple(map(int, version("nace-sdk").split(".")))
        expected_agent = f"nace-sdk/{version('nace-sdk')}"
        if sdk_version >= (0, 7, 0):
            expected_agent += f" {CLIENT_INFO}"
        assert request.headers["user-agent"] == expected_agent
        return httpx.Response(
            200,
            headers={"x-request-id": "req-test"},
            json=payload("drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.01}})
            | {"evaluation_time_ms": 12.5},
        )

    monkeypatch.setattr(
        "drex_policy.main.NaceClient", lambda **kwargs: mock_client(handler, **kwargs)
    )
    validator = DrexPolicy(TOXIC, 0.5, model="drex-latest")
    result = validator.validate("x")
    validator.close()
    assert result.metadata["drex_model"] == "drex-latest"
    assert result.metadata["drex_response_model"] == "drex-max-v1.1"
    assert result.metadata["drex_request_id"] == "req-test"
    assert result.metadata["drex_evaluation_time_ms"] == 12.5
    assert result.metadata["drex_usage"]["input_tokens"] == 4


@pytest.mark.parametrize("measured", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_score_cannot_pass(measured):
    question = Score(criteria=["low", "high"])
    with pytest.raises(DrexPolicyError):
        DrexPolicy(question, 0.5, client=score(measured)).validate("x")


def test_score_threshold_is_inclusive_only_at_or_above():
    question = Score(criteria=["low", "high"])
    client = score(0.5)
    assert DrexPolicy(question, 0.5, client=client).validate("x").outcome == "fail"
    assert (
        DrexPolicy(question, 0.5, "below", client=client).validate("x").outcome
        == "pass"
    )


@pytest.mark.parametrize("image", [{}, "data:image/png,AAAA", "data:image/png;base64,"])
def test_bad_image_forms_are_refused_before_call(image):
    client = noul(0.1)
    with pytest.raises(DrexPolicyError):
        DrexPolicy(TOXIC, 0.5, client=client).validate("x", {"images": [image]})
    assert client.calls == []


@pytest.mark.parametrize(
    "request_id",
    ["invalid id[server text]", "x" * 129, "", "req-good:1.2_test", "x" * 128],
)
def test_success_request_id_is_sanitized(request_id):
    import httpx

    from .fakes import payload

    def handler(request):
        return httpx.Response(
            200,
            json=payload("drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}})
            | {"request_id": request_id},
        )

    with mock_client(handler) as client:
        result = DrexPolicy(TOXIC, 0.5, client=client).validate("x")
    assert result.metadata["drex_request_id"] == (
        request_id if request_id in {"req-good:1.2_test", "x" * 128} else None
    )


@pytest.mark.parametrize(
    "image",
    [
        {"type": "workspace_file"},
        {"type": "workspace_file", "workspace_id": "ws", "file_id": object()},
        {
            "type": "workspace_file",
            "workspace_id": "ws",
            "file_id": "file",
            "extra": object(),
        },
        {"workspace_id": "ws", "file_id": "file"},
    ],
)
def test_malformed_workspace_image_is_refused_before_system_one(image):
    import httpx

    from .fakes import payload

    calls, requests = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=payload("drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}}),
        )

    with mock_client(handler) as client:
        original = client.system_one

        def tracked(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        client.system_one = tracked
        with pytest.raises(DrexPolicyError):
            DrexPolicy(TOXIC, 0.5, client=client).validate("x", {"images": [image]})
    assert calls == []
    assert requests == []


@pytest.mark.parametrize(
    "source", ["bytes", "path", "data-url", "uploaded-file", "workspace-file"]
)
def test_sdk_image_input_forms_remain_supported(source, tmp_path):
    import base64
    import json

    import httpx
    from nace_sdk import UploadedFile

    from .fakes import payload

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAsTj2FAAAAABJRU5ErkJggg=="
    )
    path = tmp_path / "image.png"
    path.write_bytes(png)
    image = {
        "bytes": image_input(png),
        "path": image_input(path),
        "data-url": "data:image/png;base64," + base64.b64encode(png).decode(),
        "uploaded-file": image_input(UploadedFile(workspace_id="ws", file_id="file")),
        "workspace-file": image_input(
            WorkspaceFileSource(workspace_id="ws", file_id="file")
        ),
    }[source]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=payload("drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}}),
        )

    with mock_client(handler) as client:
        result = DrexPolicy(TOXIC, 0.5, client=client).validate(
            "x", {"images": [image]}
        )
    assert result.outcome == "pass"
    assert [request["images"] for request in requests] == [[image]]


@pytest.mark.parametrize("prefix", ["", "data:image/png;base64,"])
@pytest.mark.parametrize(
    "payload",
    ["!!!", "AAA", "A===", "AA=A", "AAA===", "AAAA\n", "AAAA AAAA", "", "é==="],
)
def test_invalid_base64_payload_is_refused_before_system_one(prefix, payload):
    client = noul(0.1)
    with pytest.raises(DrexPolicyError):
        DrexPolicy(TOXIC, 0.5, client=client).validate(
            "x", {"images": [prefix + payload]}
        )
    assert client.calls == []


@pytest.mark.parametrize("prefix", ["", "data:image/png;base64,"])
@pytest.mark.parametrize("payload", ["AAAA", "AA==", "AAA=", "AA+/"])
def test_valid_base64_forms_preserve_the_image(prefix, payload):
    client = noul(0.1)
    image = prefix + payload
    assert (
        DrexPolicy(TOXIC, 0.5, client=client).validate("x", {"images": [image]}).outcome
        == "pass"
    )
    assert client.calls[0][3] == [image]

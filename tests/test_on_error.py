import asyncio
from types import SimpleNamespace

import httpx
import pytest
from guardrails import AsyncGuard, Guard
from guardrails.errors import ValidationError
from nace_sdk import (
    NaceAPIConnectionError,
    NaceAPIError,
    NaceAPIResponseValidationError,
    NaceAPITimeoutError,
    Noul,
    NoulAnswer,
    RetryPolicy,
)

from drex_policy import DrexPolicy, DrexPolicyError

from .fakes import mock_client, noul, payload, score

QUESTION = Noul(instructions="Is this unsafe?")
MODES = ["raise", "fail", "pass"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("case", "error", "retryable"),
    [
        (401, NaceAPIError, False),
        (402, NaceAPIError, False),
        (403, NaceAPIError, False),
        (404, NaceAPIError, False),
        (422, NaceAPIError, False),
        (408, NaceAPIError, True),
        (429, NaceAPIError, True),
        (500, NaceAPIError, True),
        (529, NaceAPIError, True),
        ("timeout", NaceAPITimeoutError, True),
        ("connection", NaceAPIConnectionError, True),
        ("invalid JSON", NaceAPIResponseValidationError, False),
    ],
)
def test_sdk_errors(case, error, retryable, mode, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        if case == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if case == "connection":
            raise httpx.ConnectError("disconnected", request=request)
        headers = {"x-request-id": "req-error", "retry-after-ms": "0"}
        if case == "invalid JSON":
            return httpx.Response(200, headers=headers, content=b"{")
        return httpx.Response(
            case, headers=headers, json={"error": {"message": "error"}}
        )

    with mock_client(
        handler, retry=RetryPolicy(max_retries=1, backoff_initial=0)
    ) as client:
        validator = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode)
        caller_error = case in {401, 402, 403, 404, 422}
        unusable = case == "invalid JSON"
        if mode == "raise" or caller_error or (unusable and mode == "pass"):
            with pytest.raises(DrexPolicyError if unusable else error):
                validator.validate("x")
        else:
            result = validator.validate("x")
            assert result.outcome == mode
            assert result.metadata["drex_model"] == "drex-max-v1.1"
            assert result.metadata["drex_question_id"] == "policy"
            assert "drex_answer" not in result.metadata
            details = result.metadata["drex_error"]
            assert set(details) == {"type", "status", "request_id", "message"}
            assert details["status"] == (
                case
                if isinstance(case, int)
                else 200
                if case == "invalid JSON"
                else None
            )
            assert details["request_id"] == (
                None if case in {"timeout", "connection"} else "req-error"
            )
            if mode == "fail":
                assert (
                    result.error_message == f"Drex could not decide: {details['type']}"
                )
            else:
                assert any(record.name == "drex_policy" for record in caplog.records)
    assert len(calls) == (2 if retryable else 1)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "case", ["missing", "wrong kind", "wrong number", "nonfinite", "range"]
)
def test_unusable_answers_follow_on_error(case, mode):
    client = {
        "missing": SimpleNamespace(
            system_one=lambda *a, **kw: SimpleNamespace(answers={})
        ),
        "wrong kind": score(1.5),
        "wrong number": SimpleNamespace(
            system_one=lambda *a, **kw: SimpleNamespace(
                answers={"policy": NoulAnswer.model_construct(noul=None)}
            )
        ),
        "nonfinite": noul(float("inf")),
        "range": noul(-0.01),
    }[case]
    validator = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode)
    if mode in {"raise", "pass"}:
        with pytest.raises(DrexPolicyError):
            validator.validate("x")
    else:
        result = validator.validate("x")
        assert result.outcome == mode
        assert "drex_error" in result.metadata
        assert "drex_answer" not in result.metadata


@pytest.mark.parametrize("mode", MODES)
def test_input_errors_propagate_without_a_request(mode):
    client = noul(0.1)
    validator = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode)
    with pytest.raises(TypeError):
        validator.validate(1)
    with pytest.raises(DrexPolicyError):
        validator.validate("x", {"images": ["https://example.com/image.png"]})
    assert client.calls == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "signal", [asyncio.CancelledError, KeyboardInterrupt, SystemExit]
)
def test_stop_signals_propagate(signal, mode):
    def stop(*args, **kwargs):
        raise signal

    client = SimpleNamespace(system_one=stop)
    with pytest.raises(signal):
        DrexPolicy(QUESTION, 0.5, client=client, on_error=mode).validate("x")


def test_sdk_retry_can_return_a_real_answer():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after-ms": "0"}, json={})
        return httpx.Response(
            200,
            json=payload("drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}}),
        )

    with mock_client(handler) as client:
        result = DrexPolicy(QUESTION, 0.5, client=client).validate("x")
    assert len(calls) == 2
    assert result.metadata["drex_answer"]["noul"] == 0.1
    assert "drex_error" not in result.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mode", MODES)
async def test_guard_error_policy(asynchronous, mode):
    def handler(request):
        return httpx.Response(500, json={})

    async def async_handler(request):
        return handler(request)

    client = mock_client(
        async_handler if asynchronous else handler,
        asynchronous=asynchronous,
        retry=RetryPolicy(max_retries=0),
    )
    guard = AsyncGuard() if asynchronous else Guard()
    clients = {"async_client" if asynchronous else "client": client}
    guard.use(DrexPolicy(QUESTION, 0.5, on_fail="exception", on_error=mode, **clients))
    expected = {"raise": NaceAPIError, "fail": ValidationError, "pass": None}[mode]
    try:
        if expected is None:
            result = await guard.validate("x") if asynchronous else guard.validate("x")
            assert result.validation_passed is True
        else:
            with pytest.raises(expected):
                if asynchronous:
                    await guard.validate("x")
                else:
                    guard.validate("x")
    finally:
        if asynchronous:
            await client.aclose()
        else:
            client.close()


@pytest.mark.parametrize("outcome", ["pass", "fail", "error-fail", "error-pass"])
def test_results_preserve_caller_metadata_without_mutation(outcome):
    def handler(request):
        return httpx.Response(503, json={})

    with mock_client(handler, retry=RetryPolicy(max_retries=0)) as error_client:
        validator = DrexPolicy(
            QUESTION,
            0.5,
            client=error_client
            if outcome.startswith("error")
            else noul(0.9 if outcome == "fail" else 0.1),
            on_error=outcome.removeprefix("error-")
            if outcome.startswith("error")
            else "raise",
        )
        metadata = {"images": ["AAAA"], "other_validator": {"limit": 3}}
        result = validator.validate("x", metadata)
    assert result.outcome == outcome.removeprefix("error-")
    assert result.metadata["images"] == ["AAAA"]
    assert result.metadata["other_validator"] == {"limit": 3}
    assert result.metadata is not metadata
    assert metadata == {"images": ["AAAA"], "other_validator": {"limit": 3}}


@pytest.mark.parametrize("mode", ["fail", "pass"])
def test_error_result_never_copies_the_server_message(mode, caplog):
    marker = "SENSITIVE_PROXY_ECHO_MARKER"

    def handler(request):
        return httpx.Response(
            503,
            headers={"x-request-id": "req-test"},
            json={"error": {"message": marker}},
        )

    with mock_client(handler, retry=RetryPolicy(max_retries=0)) as client:
        result = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode).validate("x")
    assert marker not in str(result.metadata)
    assert marker not in getattr(result, "error_message", "")
    assert all(marker not in record.getMessage() for record in caplog.records)
    assert result.metadata["drex_error"] == {
        "type": "NaceInternalServerError",
        "status": 503,
        "request_id": "req-test",
        "message": "NaceInternalServerError (HTTP 503)",
    }


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("answer", [None, {"type": "noul", "noul": None}])
def test_sdk_rejected_answer_cannot_pass(answer, mode):
    def handler(request):
        return httpx.Response(200, json=payload("drex-max-v1.1", {"policy": answer}))

    with mock_client(handler, retry=RetryPolicy(max_retries=0)) as client:
        policy = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode)
        if mode == "fail":
            result = policy.validate("x")
            assert result.outcome == "fail"
            assert result.metadata["drex_error"]["type"] == "DrexPolicyError"
        else:
            with pytest.raises(DrexPolicyError):
                policy.validate("x")


@pytest.mark.parametrize("mode", MODES)
def test_invalid_usage_cannot_mask_an_unusable_answer(mode):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "drex-max-v1.1",
                "usage": [],
                "answers": {"policy": {"type": "noul", "noul": None}},
            },
        )

    with mock_client(handler, retry=RetryPolicy(max_retries=0)) as client:
        policy = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode)
        if mode == "fail":
            result = policy.validate("x")
            assert result.outcome == "fail"
            assert result.metadata["drex_error"]["type"] == "DrexPolicyError"
        else:
            with pytest.raises(DrexPolicyError):
                policy.validate("x")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "status", [None, 200, 201, 204, 302, 400, 401, 408, 429, 500, 529, 599]
)
@pytest.mark.parametrize("field", ["usage", "answers.policy.noul"])
def test_response_validation_classification_depends_on_status(mode, status, field):
    error = NaceAPIResponseValidationError(status, {}, httpx.Headers(), field)

    def invalid_response(*args, **kwargs):
        raise error

    policy = DrexPolicy(
        QUESTION,
        0.5,
        client=SimpleNamespace(system_one=invalid_response),
        on_error=mode,
    )
    available = status in {408, 429, 500, 529, 599}
    if mode == "raise" or (mode == "pass" and not available):
        with pytest.raises(
            NaceAPIResponseValidationError if available else DrexPolicyError
        ):
            policy.validate("x")
    else:
        result = policy.validate("x")
        assert result.outcome == mode
        assert result.metadata["drex_error"]["type"] == (
            "NaceAPIResponseValidationError" if available else "DrexPolicyError"
        )


@pytest.mark.parametrize("mode", ["fail", "pass"])
@pytest.mark.parametrize(
    "request_id",
    [
        "SENSITIVE-PROXY-ECHO[server text]",
        "x" * 129,
        "",
        "bad id",
        "é",
        "req-good:1.2_test",
        "x" * 128,
    ],
)
def test_error_request_id_is_sanitized(mode, request_id, caplog):
    def handler(request):
        return httpx.Response(
            503, json={"request_id": request_id, "error": {"message": "server body"}}
        )

    with mock_client(handler, retry=RetryPolicy(max_retries=0)) as client:
        result = DrexPolicy(QUESTION, 0.5, client=client, on_error=mode).validate("x")
    assert result.metadata["drex_error"]["request_id"] == (
        request_id if request_id in {"req-good:1.2_test", "x" * 128} else None
    )
    assert "SENSITIVE-PROXY-ECHO" not in str(result.metadata)
    assert all(
        "SENSITIVE-PROXY-ECHO" not in record.getMessage() for record in caplog.records
    )

import asyncio
import base64
import json
import operator

import httpx
from hypothesis import example, given, settings
from hypothesis import strategies as st
from nace_sdk import (
    NaceAPIConnectionError,
    NaceAPIError,
    NaceAPIResponseValidationError,
    NaceError,
    NoulAnswer,
    ScoreAnswer,
    SystemOneResponse,
    WorkspaceFileSource,
    image_input,
)
from support import (
    MODES,
    NOUL,
    REQUESTED_MODEL,
    RESPONSE_MODEL,
    SCORE,
    Clients,
    Recording,
    build,
    cleanup_sdk,
    client_for,
    guard_for,
    guarded,
    http_reply,
    require,
    response,
    result_at,
    wire_response,
)

from drex_policy import DrexPolicyError, main
from tests.fakes import payload

generated = settings(
    max_examples=80,
    derandomize=True,
    database=None,
    deadline=None,
    report_multiple_bugs=False,
)
finite = st.floats(allow_nan=False, allow_infinity=False)
prob = st.floats(0, 1)
nonfinite = st.sampled_from((float("nan"), float("inf"), float("-inf")))
directions = ("yes", "no", "at_or_above", "below")
routes = st.sampled_from(("sync", "guard", "native", "executor"))
workspace = WorkspaceFileSource(workspace_id="workspace", file_id="file")


def assert_decision(actual, expected, **inputs):
    require(actual == expected, {**inputs, "actual": actual, "expected": expected})


@generated
@given(first=prob, second=prob, score_a=finite, score_b=finite)
@example(first=0.0, second=1.0, score_a=-1.0, score_b=1.0)
def p4(first, second, score_a, score_b):
    for kind, a, b, ascending, descending in (
        ("noul", first, second, "yes", "no"),
        ("score", score_a, score_b, "at_or_above", "below"),
    ):
        lower, higher = sorted((a, b))
        for mode, compare in ((ascending, operator.ge), (descending, operator.lt)):
            for measured, threshold in ((a, b), (b, a), (a, a), (b, b)):
                actual = main._decide(kind, measured, threshold, mode)
                assert_decision(
                    actual,
                    compare(measured, threshold),
                    kind=kind,
                    measured=measured,
                    threshold=threshold,
                    fail_when=mode,
                )
            for threshold in (a, b):
                low = main._decide(kind, lower, threshold, mode)
                high = main._decide(kind, higher, threshold, mode)
                require(
                    low <= high if mode == ascending else low >= high,
                    (kind, mode, lower, higher, threshold, low, high),
                )


def check_result(route, validator, expected, error=None, metadata=None):
    async def run():
        guard = guard_for(route, validator)
        if error is not None:
            try:
                outcome = await guarded(route, guard, "sample", metadata)
            except error:
                return
            require(False, f"expected {error.__name__}, got Guard outcome {outcome}")
        await guarded(route, guard, "sample", metadata)
        actual = result_at(guard)
        require(
            actual.outcome == expected, f"{route}: expected {expected}, got {actual}"
        )
        return actual

    return asyncio.run(run())


@generated
@given(
    route=routes,
    mode=st.sampled_from(MODES),
    kind=st.sampled_from(("noul", "score")),
    measured=st.one_of(
        prob, finite, nonfinite, st.none(), st.booleans(), st.text(max_size=8)
    ),
    threshold=prob,
    reverse=st.booleans(),
    shape=st.sampled_from(("answer", "missing", "wrong kind", "wrong id")),
)
@example(
    route="sync",
    mode="pass",
    kind="score",
    measured=float("nan"),
    threshold=0.5,
    reverse=False,
    shape="answer",
)
def p2_values(route, mode, kind, measured, threshold, reverse, shape):
    cls, field, question, high, low = (
        (NoulAnswer, "noul", NOUL, "yes", "no")
        if kind == "noul"
        else (ScoreAnswer, "score", SCORE, "at_or_above", "below")
    )
    answer = cls.model_construct(
        **{
            field: measured,
            "confidence": 0.8,
            "legend": ("low", "high"),
            "probabilities": {0: 0.2},
        }
    )
    if shape == "wrong kind":
        answer = (
            ScoreAnswer(
                score=0.3, confidence=0.8, legend=("low",), probabilities={0: 1.0}
            )
            if kind == "noul"
            else NoulAnswer(noul=0.2)
        )
    fake_response = response(answer, "other" if shape == "wrong id" else "policy")
    if shape == "missing":
        fake_response.answers.clear()
    fake = Recording(lambda *_: fake_response)
    validator = build(
        route,
        fake,
        question=question,
        threshold=threshold,
        fail_when=low if reverse else high,
        on_error=mode,
        on_fail="noop",
    )
    usable = shape == "answer" and type(measured) in (int, float)
    if usable:
        usable = float("-inf") < measured < float("inf") and (
            kind == "score" or 0 <= measured <= 1
        )
    if not usable:
        if mode == "fail":
            result = check_result(route, validator, "fail")
            require(result.metadata["drex_error"]["type"] == "DrexPolicyError", result)
        else:
            check_result(route, validator, None, DrexPolicyError)
        return
    crosses = (operator.lt if reverse else operator.ge)(measured, threshold)
    result = check_result(route, validator, "fail" if crosses else "pass")
    require("drex_error" not in result.metadata, result)


HTTP_CASES = (
    400,
    401,
    402,
    403,
    404,
    408,
    422,
    429,
    499,
    500,
    529,
    599,
    "timeout",
    "connection",
    "unreadable",
    "missing",
    "wrong",
    "bad value",
)


@generated
@given(route=routes, mode=st.sampled_from(MODES), case=st.sampled_from(HTTP_CASES))
@example(route="sync", mode="pass", case=401)
def p2_wire(route, mode, case):
    async def run():
        def handler(request):
            if case == "missing":
                return wire_response({})
            if case == "wrong":
                return wire_response({"policy": {"type": "noul", "noul": None}})
            if case == "bad value":
                return wire_response({"policy": {"type": "noul", "noul": 2.0}})
            return http_reply(case, request)

        native = route == "native"
        sdk = client_for(handler, native)
        unusable = case in {"missing", "wrong", "bad value", "unreadable"}
        availability = case in {408, 429, "timeout", "connection"} or (
            isinstance(case, int) and 500 <= case < 600
        )
        expected = mode if (availability or unusable and mode == "fail") else "raise"
        error = (
            DrexPolicyError
            if unusable
            else NaceAPIConnectionError
            if case in {"timeout", "connection"}
            else NaceAPIResponseValidationError
            if case == "unreadable"
            else NaceAPIError
        )
        try:
            validator = build(
                route,
                Clients(sdk.system_one, sdk.system_one),
                on_error=mode,
                on_fail="noop",
            )
            guard = guard_for(route, validator)
            if expected == "raise":
                try:
                    result = await guarded(route, guard, "sample")
                except error as raised:
                    category = (
                        "unusable"
                        if unusable
                        else "availability"
                        if availability
                        else "caller"
                    )
                    require(
                        main._classify_error(raised) == category,
                        (case, category, raised),
                    )
                else:
                    require(False, (route, mode, case, "expected exception", result))
            else:
                await guarded(route, guard, "sample")
                result = result_at(guard)
                require(result.outcome == expected, (route, mode, case, result))
                require(
                    "drex_error" in result.metadata
                    and "drex_answer" not in result.metadata,
                    result,
                )
        finally:
            await cleanup_sdk(sdk, native)

    asyncio.run(run())


@generated
@given(
    route=routes,
    usage=st.sampled_from((None, [], "invalid")),
    answers=st.sampled_from(({}, {"policy": {"type": "noul", "noul": None}})),
)
@example(route="sync", usage=[], answers={"policy": {"type": "noul", "noul": None}})
def p2_masked_answer(route, usage, answers):
    # Invalid ancillary metadata must not hide a recognizable unusable answer.
    body = {"model": RESPONSE_MODEL, "usage": usage, "answers": answers}

    async def run():
        native = route == "native"
        sdk = client_for(lambda request: httpx.Response(200, json=body), native)
        try:
            policy = build(
                route, Clients(sdk.system_one, sdk.system_one), on_error="pass"
            )
            guard = guard_for(route, policy)
            try:
                result = await guarded(route, guard, "sample")
            except DrexPolicyError:
                return
            require(
                False,
                (route, body, result.validation_passed, result_at(guard).metadata),
            )
        finally:
            await cleanup_sdk(sdk, native)

    asyncio.run(run())


@generated
@given(
    route=routes,
    kind=st.sampled_from(("noul", "score")),
    confidence=prob,
    value=prob,
    scores=st.lists(prob, min_size=1, max_size=5),
    text=st.text(max_size=25),
    fail=st.booleans(),
    omit_conf=st.booleans(),
)
@example(
    route="sync",
    kind="score",
    confidence=0.57391,
    value=0.12345,
    scores=[0.12345, 0.87655],
    text="label",
    fail=True,
    omit_conf=False,
)
def p5(route, kind, confidence, value, scores, text, fail, omit_conf):
    if kind == "noul":
        answer = NoulAnswer(noul=value, confidence=None if omit_conf else confidence)
        expected = {"type": "noul", "noul": value}
        if not omit_conf:
            expected["confidence"] = confidence
        question = NOUL
    else:
        probabilities = dict(enumerate(scores))
        legend = tuple(f"{i} {text}" for i in probabilities)
        answer = ScoreAnswer(
            score=value,
            confidence=confidence,
            probabilities=probabilities,
            legend=legend,
        )
        expected = {
            "type": "score",
            "score": value,
            "confidence": confidence,
            "legend": list(legend),
            "probabilities": {str(i): p for i, p in probabilities.items()},
        }
        question = SCORE
    # The request alias and the resolved model are intentionally different.
    sdk_response = SystemOneResponse.model_validate_json(
        json.dumps(
            payload(RESPONSE_MODEL, {"sample-id": answer.model_dump(mode="json")})
        )
    )
    fake = Recording(lambda *_: sdk_response)
    validator = build(
        route,
        fake,
        question=question,
        threshold=value if fail else 1.0,
        fail_when=None if fail or value < 1 else ("no" if kind == "noul" else "below"),
        question_id="sample-id",
        model=REQUESTED_MODEL,
        on_fail="noop",
    )
    metadata = check_result(route, validator, "fail" if fail else "pass").metadata
    require(metadata["drex_answer"] == expected, (expected, metadata["drex_answer"]))
    require(metadata["drex_response_model"] == RESPONSE_MODEL, metadata)
    require(metadata["drex_model"] == REQUESTED_MODEL, metadata)


base64_image = st.binary(min_size=1, max_size=25).map(
    lambda b: base64.b64encode(b).decode()
)
image = st.one_of(
    base64_image,
    base64_image.map(lambda b: f"data:image/png;base64,{b}"),
    st.just(image_input(b"\x89PNG\r\n\x1a\n synthetic")),
    st.just(image_input(workspace)),
    st.just(workspace),
)
models = st.sampled_from(
    (
        None,
        "drex-max",
        "drex-max-v1.1",
        "drex-max-finance",
        "drex-latest",
        "drex-v2.0",
        "other-alias",
    )
)


@generated
@given(
    route=routes,
    model=models,
    images=st.lists(image, max_size=10),
    as_tuple=st.booleans(),
)
@example(route="sync", model="drex-v2.0", images=["AAAA"], as_tuple=False)
def p7_valid(route, model, images, as_tuple):
    async def run():
        wire = []

        def handler(request):
            wire.append(json.loads(request.content))
            return wire_response({"policy": {"type": "noul", "noul": 0.1}})

        native = route == "native"
        sdk = client_for(handler, native)
        fake = Recording(
            lambda value, questions, model, images: sdk.system_one(
                value, questions, model=model, images=images
            )
        )
        validator = build(route, fake, model=model)
        refused = len(images) > 8 or (images and model == "drex-v2.0")
        metadata = {"images": tuple(images) if as_tuple else images}
        guard = guard_for(route, validator)
        try:
            if refused:
                try:
                    outcome = await guarded(route, guard, "sample", metadata)
                except DrexPolicyError:
                    require(not fake.calls and not wire, (fake.calls, wire))
                else:
                    require(False, (model, images, outcome, fake.calls, wire))
            else:
                await guarded(route, guard, "sample", metadata)
                expected = [
                    image_input(item) if isinstance(item, WorkspaceFileSource) else item
                    for item in images
                ]
                require(
                    len(wire) == 1 and wire[0].get("images", []) == expected,
                    (expected, wire),
                )
                require(
                    fake.calls
                    == [
                        ("sample", ("policy",), model or RESPONSE_MODEL, images or None)
                    ],
                    fake.calls,
                )
                require(wire[0]["model"] == (model or RESPONSE_MODEL), wire)
        finally:
            await cleanup_sdk(sdk, native)

    asyncio.run(run())


@generated
@given(
    route=routes,
    bad=st.sampled_from(
        (
            "https://example.invalid/image",
            3,
            b"AAAA",
            {},
            "data:image/png;base64,",
            {"type": "workspace_file"},
            "data:image/png;base64,!!!",
        )
    ),
)
@example(route="sync", bad={"type": "workspace_file"})
def p7_bad(route, bad):
    check_invalid_image(route, bad, repr(bad))


def p7_request_encoding():
    check_invalid_image(
        "sync",
        {"type": "workspace_file", "workspace_id": "workspace", "file_id": object()},
        "workspace_file with a non-serializable file_id",
    )


def check_invalid_image(route, bad, description):
    async def run():
        wire = []

        def handler(request):
            wire.append(json.loads(request.content))
            return wire_response({"policy": {"type": "noul", "noul": 0.1}})

        native = route == "native"
        sdk = client_for(handler, native)
        fake = Recording(
            lambda value, questions, model, images: sdk.system_one(
                value, questions, model=model, images=images
            )
        )
        guard = guard_for(route, build(route, fake))
        try:
            try:
                outcome = await guarded(route, guard, "sample", {"images": [bad]})
            except (DrexPolicyError, NaceError) as error:
                require(
                    isinstance(error, DrexPolicyError) and not fake.calls and not wire,
                    (
                        route,
                        description,
                        type(error).__name__,
                        str(error),
                        len(fake.calls),
                        wire,
                    ),
                )
            else:
                require(
                    False,
                    (
                        route,
                        description,
                        len(fake.calls),
                        wire,
                        outcome.validation_passed,
                    ),
                )
        finally:
            await cleanup_sdk(sdk, native)

    asyncio.run(run())


@generated
@given(
    route=routes,
    value=st.integers(),
    custom=st.dictionaries(
        st.text(min_size=1, max_size=12).filter(
            lambda k: k not in {"images", "token"} and not k.startswith("drex_")
        ),
        st.one_of(st.integers(), st.text(max_size=12), st.none()),
        max_size=7,
    ),
    outcome=st.sampled_from(("pass", "fail", "error-pass", "error-fail")),
)
def p9_results(route, value, custom, outcome):
    metadata = {**custom, "images": ["AAAA", "AQID"], "token": {"value": value}}

    def reply(*_):
        if outcome.startswith("error"):
            raise NaceAPIConnectionError("offline")
        return response(NoulAnswer(noul=0.9 if outcome == "fail" else 0.1))

    fake = Recording(reply)
    validator = build(
        route,
        fake,
        on_error=outcome.removeprefix("error-")
        if outcome.startswith("error-")
        else "raise",
        on_fail="noop",
    )
    actual = check_result(
        route, validator, outcome.removeprefix("error-"), metadata=metadata
    )
    expected = {**custom, "images": ["AAAA", "AQID"], "token": {"value": value}}
    require(
        all(
            key in actual.metadata and actual.metadata[key] == item
            for key, item in expected.items()
        ),
        (expected, actual.metadata),
    )


@generated
@given(
    route=routes,
    text=st.text(min_size=1, max_size=40),
    mode=st.sampled_from(("pass", "fail")),
    status=st.one_of(st.sampled_from((408, 429)), st.integers(500, 599)),
    encoding=st.sampled_from(("error", "detail", "text")),
    supplied_header=st.booleans(),
)
def p10_message(route, text, mode, status, encoding, supplied_header):
    check_redaction(route, text, mode, status, encoding, supplied_header, body_id=False)


def p10_body_request_id():
    check_redaction(
        "sync",
        "server text in body request_id",
        "pass",
        503,
        "error",
        False,
        body_id=True,
    )


def check_redaction(route, text, mode, status, encoding, supplied_header, *, body_id):
    marker = f"SENSITIVE-PROXY-ECHO[{text}]"
    body = {"error": {"message": marker}} if encoding == "error" else {"detail": marker}
    if body_id:
        body["request_id"] = marker
    headers = {"X-Request-ID": "req-control"} if supplied_header else None

    def handler(request):
        if encoding == "text":
            return httpx.Response(status, text=marker, headers=headers)
        return http_reply(status, request, body, headers)

    async def run():
        native = route == "native"
        sdk = client_for(handler, native)
        try:
            validator = build(
                route,
                Clients(sdk.system_one, sdk.system_one),
                on_error=mode,
                on_fail="noop",
            )
            guard = guard_for(route, validator)
            await guarded(route, guard, "sample")
            error = result_at(guard).metadata["drex_error"]
            require(marker not in repr(error), (status, body, headers, error))
            require(error["message"] == f"{error['type']} (HTTP {status})", error)
            require(
                error["status"] == status
                and error["request_id"] == ("req-control" if supplied_header else None),
                error,
            )
        finally:
            await cleanup_sdk(sdk, native)

    asyncio.run(run())


def p9_chain():
    async def run():
        for first_outcome in ("pass", "fail", "error-pass", "error-fail"):

            def first_response(*_, first_outcome=first_outcome):
                if first_outcome.startswith("error"):
                    raise NaceAPIConnectionError("offline")
                return response(
                    NoulAnswer(noul=0.9 if first_outcome == "fail" else 0.1)
                )

            first = Recording(first_response)
            second = Recording(lambda *_: response(NoulAnswer(noul=0.1)))
            images = ["AAAA", "AQID"]
            expected = {"images": images, "caller": {"tag": first_outcome}}
            metadata = dict(expected)
            validators = [
                build(
                    "sync",
                    first,
                    on_fail="noop",
                    on_error=first_outcome.removeprefix("error-")
                    if first_outcome.startswith("error-")
                    else "raise",
                ),
                build("sync", second),
            ]
            guard = guard_for("sync", *validators)
            await guarded("sync", guard, "sample", metadata)
            require(
                [first.calls[0][3], second.calls[0][3]] == [images, images],
                (first_outcome, metadata, first.calls, second.calls),
            )
            for index in (0, 1):
                actual = result_at(guard, index).metadata
                require(
                    all(actual.get(key) == value for key, value in expected.items()),
                    (first_outcome, index, expected, actual),
                )
        for route in ("sync", "guard", "native", "executor"):
            fake = Recording(
                lambda value, *_: response(
                    NoulAnswer(noul=0.9 if value == "initial" else 0.1)
                )
            )
            validator = build(route, fake, on_fail="fix_reask", fix_value="replacement")
            guard = guard_for(route, validator)
            outcome = await guarded(route, guard, "initial", metadata)
            require(outcome.validated_output == "replacement", (route, outcome))
            require(
                [(call[0], call[3]) for call in fake.calls]
                == [("initial", images), ("replacement", images)],
                (route, metadata, fake.calls),
            )

    asyncio.run(run())


@generated
@given(
    route=routes,
    request_id=st.one_of(
        st.text(max_size=145),
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-",
            min_size=1,
            max_size=140,
        ),
    ),
    body_id=st.booleans(),
)
@example(route="sync", request_id="unsafe request id", body_id=True)
@example(route="native", request_id="req-safe_123:ok", body_id=False)
def p10_request_ids(route, request_id, body_id):
    allowed = 1 <= len(request_id) <= 128 and all(
        character
        in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
        for character in request_id
    )
    expected = request_id if allowed else None
    require(
        main._request_id(response_with_id(request_id)) == expected,
        (request_id, expected),
    )

    async def run():
        body = {"error": {"message": "SENSITIVE BODY"}}
        headers = None
        if body_id:
            body["request_id"] = request_id
        else:
            # HTTP header values must be representable as ASCII.
            try:
                request_id.encode("ascii")
            except UnicodeEncodeError:
                return
            headers = {"x-request-id": request_id}
        native = route == "native"
        sdk = client_for(
            lambda request: httpx.Response(503, json=body, headers=headers), native
        )
        try:
            guard = guard_for(
                route,
                build(route, Clients(sdk.system_one, sdk.system_one), on_error="pass"),
            )
            await guarded(route, guard, "sample")
            actual = result_at(guard).metadata["drex_error"]
            require(
                actual["request_id"] == expected
                and "SENSITIVE BODY" not in repr(actual),
                (route, request_id, expected, actual),
            )
        finally:
            await cleanup_sdk(sdk, native)

    asyncio.run(run())


def response_with_id(request_id):
    from types import SimpleNamespace

    return SimpleNamespace(request_id=request_id)


@generated
@given(
    route=routes,
    mode=st.sampled_from(MODES),
    field=st.sampled_from(("", "usage", "answers.policy.noul")),
    status=st.sampled_from((200, 201, 400, 401, 408, 422, 429, 499, 500, 529, 599)),
)
def p2_response_validation(route, mode, field, status):
    async def run():
        error = NaceAPIResponseValidationError(status, {}, httpx.Headers(), field)
        available = status in (408, 429) or 500 <= status < 600
        category = "availability" if available else "unusable"
        require(main._classify_error(error) == category, (status, field, category))

        def fail(*_):
            raise error

        policy = build(route, Recording(fail), on_error=mode, on_fail="noop")
        guard = guard_for(route, policy)
        expected = mode if available or mode == "fail" else "raise"
        if expected == "raise":
            try:
                result = await guarded(route, guard, "sample")
            except (DrexPolicyError, NaceAPIResponseValidationError) as raised:
                require(
                    isinstance(
                        raised,
                        NaceAPIResponseValidationError
                        if available
                        else DrexPolicyError,
                    ),
                    (status, field, type(raised).__name__),
                )
            else:
                require(False, (status, field, mode, result))
        else:
            await guarded(route, guard, "sample")
            require(
                result_at(guard).outcome == expected,
                (status, field, mode, result_at(guard)),
            )

    asyncio.run(run())


def p1_original_equals_fix():
    async def run():
        for route in ("sync", "guard", "native", "executor"):
            fake = Recording(lambda *_: response(NoulAnswer(noul=0.9)))
            policy = build(route, fake, on_fail="fix_reask", fix_value="replacement")
            guard = guard_for(route, policy)
            try:
                outcome = await guarded(route, guard, "replacement")
            except DrexPolicyError:
                require(len(fake.calls) == 1, (route, fake.calls))
            else:
                require(False, (route, outcome))

    asyncio.run(run())


@generated
@given(
    route=routes,
    mode=st.sampled_from(MODES),
    case=st.sampled_from(
        ("pass", "fail", "unusable", "nonfinite", "masked", "availability", "401")
    ),
    initial_equals_fix=st.booleans(),
)
@example(route="sync", mode="pass", case="availability", initial_equals_fix=False)
@example(route="native", mode="fail", case="unusable", initial_equals_fix=True)
def p1_replacement_decisions(route, mode, case, initial_equals_fix):
    from replay import reply

    raised_by_client = []

    def answer(value, *_):
        selected = case if value == "replacement" else "fail"
        try:
            return reply(selected)
        except NaceAPIError as error:
            raised_by_client.append(error)
            raise

    fake = Recording(answer)
    policy = build(
        route,
        fake,
        question=SCORE,
        on_error=mode,
        on_fail="fix_reask",
        fix_value="replacement",
    )
    guard = guard_for(route, policy)

    async def run():
        try:
            outcome = await guarded(
                route, guard, "replacement" if initial_equals_fix else "initial"
            )
        except (DrexPolicyError, NaceAPIError) as error:
            if case == "401":
                require(
                    raised_by_client and error is raised_by_client[-1],
                    (route, mode, case, type(error).__name__),
                )
            else:
                require(
                    case != "pass" and isinstance(error, DrexPolicyError),
                    (route, mode, case, type(error).__name__),
                )
        else:
            require(
                case == "pass"
                and outcome.validation_passed is True
                and outcome.validated_output == "replacement",
                (route, mode, case, outcome),
            )
        expected_text = (
            ["replacement"] if initial_equals_fix else ["initial", "replacement"]
        )
        require(
            [call[0] for call in fake.calls] == expected_text,
            (route, mode, case, fake.calls),
        )

    asyncio.run(run())


@generated
@given(route=routes, payload=st.text(max_size=40))
@example(route="sync", payload="!!!")
@example(route="native", payload="AAAA")
@example(route="executor", payload="AAA")
def p7_data_url_payload(route, payload):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    unpadded = payload.rstrip("=")
    padding = len(payload) - len(unpadded)
    allowed = (
        bool(unpadded)
        and len(payload) % 4 == 0
        and padding <= 2
        and all(character in alphabet for character in unpadded)
    )
    fake = Recording(lambda *_: response(NoulAnswer(noul=0.1)))
    policy = build(route, fake)
    images = [f"data:image/png;base64,{payload}"]
    check_result(
        route,
        policy,
        "pass" if allowed else None,
        error=None if allowed else DrexPolicyError,
        metadata={"images": images},
    )
    require(
        [call[3] for call in fake.calls] == ([images] if allowed else []),
        (payload, allowed, fake.calls),
    )

import pytest
from guardrails import AsyncGuard, Guard
from guardrails.errors import ValidationError
from nace_sdk import Noul

from drex_policy import DrexPolicy

from .fakes import FakeAsyncClient, FakeClient

QUESTION = Noul(instructions="Is this unsafe?")


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    ("action", "passed", "output"),
    [
        ("exception", None, None),
        ("noop", False, "unsafe"),
        ("filter", False, None),
        ("refrain", False, None),
        ("reask", False, None),
        ("custom", True, "redacted"),
        ("fix", True, "safe"),
        ("fix_reask", True, "safe"),
    ],
)
async def test_on_fail(asynchronous, action, passed, output):
    custom_calls = []

    def replace(value, failure):
        custom_calls.append((value, failure.error_message))
        return "redacted"

    def answer(value):
        return {"type": "noul", "noul": 0.1 if value == "safe" else 0.9}

    if asynchronous:
        client = FakeAsyncClient(answer)
        guard, clients = AsyncGuard(), {"async_client": client}
    else:
        client = FakeClient(answer)
        guard, clients = Guard(), {"client": client}
    guard.use(
        DrexPolicy(
            QUESTION,
            0.5,
            on_fail=replace if action == "custom" else action,
            **({"fix_value": "safe"} if action in {"fix", "fix_reask"} else {}),
            **clients,
        )
    )

    async def validate():
        if asynchronous:
            return await guard.validate("unsafe")
        return guard.validate("unsafe")

    if action == "exception":
        with pytest.raises(ValidationError, match=r"Drex P\(yes\)=0.9"):
            await validate()
    else:
        result = await validate()
        assert result.validation_passed is passed
        assert result.validated_output == output
        assert (result.reask is not None) == (action == "reask")
    assert [call[0] for call in client.calls] == (
        ["unsafe", "safe"] if action == "fix_reask" else ["unsafe"]
    )
    assert custom_calls == (
        [("unsafe", "Drex P(yes)=0.9 >= fail_at=0.5")] if action == "custom" else []
    )


@pytest.mark.parametrize("action", ["fix", "fix_reask"])
def test_fix_actions_require_replacement(action):
    with pytest.raises(ValueError, match="fix_value"):
        DrexPolicy(QUESTION, 0.5, on_fail=action)


def test_supplied_fix_value_is_always_available_on_failure():
    validator = DrexPolicy(
        QUESTION, 0.5, client=FakeClient({"type": "noul", "noul": 0.9}), fix_value=""
    )
    assert validator.validate("unsafe").fix_value == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("replacement", ["policy-fail", "503", "pass"])
async def test_fix_reask_rejects_a_failed_replacement(
    asynchronous, replacement, monkeypatch
):
    import json

    import httpx
    from nace_sdk import RetryPolicy, Score

    from drex_policy import DrexPolicyError

    from .fakes import mock_client, payload

    calls = []

    def handler(request):
        text = json.loads(request.content)["state"]
        calls.append(text)
        if text == "replacement" and replacement == "503":
            return httpx.Response(503, json={})
        value = 0.1 if text == "replacement" and replacement == "pass" else 0.9
        return httpx.Response(
            200,
            json=payload(
                "drex-max-v1.1",
                {
                    "policy": {
                        "type": "score",
                        "score": value,
                        "confidence": 0.9,
                        "legend": ["safe", "unsafe"],
                        "probabilities": {"0": 1 - value, "1": value},
                    }
                },
            ),
        )

    async def async_handler(request):
        return handler(request)

    if not asynchronous:
        monkeypatch.setenv("GUARDRAILS_RUN_SYNC", "true")
    client = mock_client(
        async_handler if asynchronous else handler,
        asynchronous=asynchronous,
        retry=RetryPolicy(max_retries=0),
    )
    guard = AsyncGuard() if asynchronous else Guard()
    policy = DrexPolicy(
        Score(criteria=["safe", "unsafe"]),
        0.5,
        on_fail="fix_reask",
        fix_value="replacement",
        on_error="fail",
        **{"async_client" if asynchronous else "client": client},
    )
    guard.use(policy)

    async def validate():
        return (
            await guard.validate("initial")
            if asynchronous
            else guard.validate("initial")
        )

    try:
        if replacement == "pass":
            outcome = await validate()
            assert outcome.validated_output == "replacement"
            assert outcome.validation_passed is True
            assert outcome.reask is None
        else:
            with pytest.raises(
                DrexPolicyError,
                match="fix_value does not pass the policy, or could not be checked",
            ):
                await validate()
    finally:
        if asynchronous:
            await client.aclose()
        else:
            client.close()
    assert calls == ["initial", "replacement"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["guard", "async-native", "async-thread"])
@pytest.mark.parametrize("on_error", ["raise", "fail", "pass"])
@pytest.mark.parametrize("reply", ["503", "timeout", "missing-answer", "pass"])
async def test_fix_reask_requires_a_real_passing_decision(
    route, on_error, reply, monkeypatch
):
    import json

    import httpx
    from nace_sdk import RetryPolicy

    from drex_policy import DrexPolicyError

    from .fakes import mock_client, payload

    calls = []

    def handler(request):
        text = json.loads(request.content)["state"]
        calls.append(text)
        if text == "replacement":
            if reply == "503":
                return httpx.Response(503, json={})
            if reply == "timeout":
                raise httpx.ReadTimeout("Unavailable", request=request)
            if reply == "missing-answer":
                return httpx.Response(200, json=payload("drex-max-v1.1", {}))
        probability = 0.1 if text == "replacement" else 0.9
        return httpx.Response(
            200,
            json=payload(
                "drex-max-v1.1", {"policy": {"type": "noul", "noul": probability}}
            ),
        )

    async def async_handler(request):
        return handler(request)

    native = route == "async-native"
    if route == "guard":
        monkeypatch.setenv("GUARDRAILS_RUN_SYNC", "true")
    client = mock_client(
        async_handler if native else handler,
        asynchronous=native,
        retry=RetryPolicy(max_retries=0),
    )
    guard = Guard() if route == "guard" else AsyncGuard()
    guard.use(
        DrexPolicy(
            QUESTION,
            0.5,
            on_fail="fix_reask",
            fix_value="replacement",
            on_error=on_error,
            **{"async_client" if native else "client": client},
        )
    )

    async def validate():
        return (
            guard.validate("initial")
            if route == "guard"
            else await guard.validate("initial")
        )

    try:
        if reply == "pass":
            result = await validate()
            assert result.validation_passed is True
            assert result.validated_output == "replacement"
            assert result.reask is None
        else:
            with pytest.raises(DrexPolicyError):
                await validate()
    finally:
        if native:
            await client.aclose()
        else:
            client.close()
    assert calls == ["initial", "replacement"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["guard", "async-native", "async-thread"])
@pytest.mark.parametrize("on_error", ["raise", "fail", "pass"])
@pytest.mark.parametrize("stop", [401, 402, 422, "cancel"])
async def test_fix_reask_preserves_caller_errors_and_cancellation(
    route, on_error, stop, monkeypatch
):
    import asyncio
    import json

    import httpx
    from nace_sdk import (
        NaceAuthenticationError,
        NaceInsufficientCreditError,
        NaceUnprocessableEntityError,
        RetryPolicy,
    )

    from .fakes import mock_client, payload

    calls = []

    def handler(request):
        text = json.loads(request.content)["state"]
        calls.append(text)
        if text == "replacement":
            if stop == "cancel":
                raise asyncio.CancelledError
            return httpx.Response(stop, json={})
        return httpx.Response(
            200,
            json=payload("drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.9}}),
        )

    async def async_handler(request):
        return handler(request)

    native = route == "async-native"
    if route == "guard":
        monkeypatch.setenv("GUARDRAILS_RUN_SYNC", "true")
    client = mock_client(
        async_handler if native else handler,
        asynchronous=native,
        retry=RetryPolicy(max_retries=0),
    )
    guard = Guard() if route == "guard" else AsyncGuard()
    guard.use(
        DrexPolicy(
            QUESTION,
            0.5,
            on_fail="fix_reask",
            fix_value="replacement",
            on_error=on_error,
            **{"async_client" if native else "client": client},
        )
    )
    expected = {
        401: NaceAuthenticationError,
        402: NaceInsufficientCreditError,
        422: NaceUnprocessableEntityError,
        "cancel": asyncio.CancelledError,
    }[stop]
    try:
        with pytest.raises(expected) as caught:
            if route == "guard":
                guard.validate("initial")
            else:
                await guard.validate("initial")
        assert type(caught.value) is expected
    finally:
        if native:
            await client.aclose()
        else:
            client.close()
    assert calls == ["initial", "replacement"]

import asyncio
import os
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
from guardrails import AsyncGuard, Guard
from nace_sdk import Noul, RetryPolicy, Score

from drex_policy import DrexPolicy
from tests.fakes import mock_client, payload

NOUL = Noul(instructions="Check this sample.")
SCORE = Score(criteria=["low", "high"])
MODES = ("raise", "fail", "pass")
REQUESTED_MODEL = "drex-latest"
RESPONSE_MODEL = "drex-max-v1.1"
WAIT = 5


class Violation(AssertionError):
    pass


def require(condition, details):
    if not condition:
        raise Violation(str(details))


def response(answer, question_id="policy", model=RESPONSE_MODEL):
    return SimpleNamespace(model=model, answers={question_id: answer})


def client_for(handler, native=False):
    async def handle_async(request):
        return handler(request)

    return mock_client(
        handle_async if native else handler,
        asynchronous=native,
        retry=RetryPolicy(max_retries=0),
    )


def http_reply(case, request, body=None, headers=None):
    if case == "timeout":
        raise httpx.ReadTimeout("offline timeout", request=request)
    if case == "connection":
        raise httpx.ConnectError("offline disconnect", request=request)
    if case == "unreadable":
        return httpx.Response(200, content=b"{")
    return httpx.Response(case, json=body or {}, headers=headers)


@dataclass
class Clients:
    sync: object
    native: object


class Recording:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def sync(self, value, questions, *, model=None, images=None):
        self.calls.append((value, tuple(questions), model, images))
        return self.handler(value, questions, model, images)

    async def native(self, value, questions, *, model=None, images=None):
        result = self.sync(value, questions, model=model, images=images)
        if asyncio.iscoroutine(result):
            return await result
        return result


def build(route, clients, *, question=NOUL, threshold=0.5, **options):
    clients = (
        {"async_client": SimpleNamespace(system_one=clients.native)}
        if route == "native"
        else {"client": SimpleNamespace(system_one=clients.sync)}
    )
    return DrexPolicy(question, threshold, **clients, **options)


def guard_for(route, *validators):
    os.environ["GUARDRAILS_RUN_SYNC"] = "true" if route == "sync" else "false"
    return (Guard() if route in {"sync", "guard"} else AsyncGuard()).use(*validators)


async def guarded(route, guard, value, metadata=None):
    if route == "guard":
        return await asyncio.to_thread(guard.validate, value, metadata=metadata)
    if route == "sync":
        return guard.validate(value, metadata=metadata)
    return await guard.validate(value, metadata=metadata)


async def cleanup_sdk(sdk, native):
    if native:
        await sdk.aclose()
    else:
        sdk.close()


def wire_response(answers):
    return httpx.Response(200, json=payload(RESPONSE_MODEL, answers))


def result_at(guard, index=0):
    return guard.history.last.validator_logs[index].validation_result

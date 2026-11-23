import asyncio
import threading
from collections import Counter
from contextlib import ExitStack
from unittest.mock import patch

import httpx
from guardrails.errors import ValidationError
from guardrails.validator_service import (
    AsyncValidatorService,
    SequentialValidatorService,
)
from nace_sdk import (
    NaceAPIError,
    NaceAuthenticationError,
    NaceInternalServerError,
    NoulAnswer,
    RetryPolicy,
    ScoreAnswer,
    SystemOneResponse,
)
from support import (
    RESPONSE_MODEL,
    SCORE,
    WAIT,
    Recording,
    build,
    guard_for,
    guarded,
    require,
    response,
    result_at,
)

from drex_policy import DrexPolicy, DrexPolicyError, main
from tests.fakes import mock_client

INITIAL = "initial"
REPLACEMENT = "replacement"


async def wait_signal(event):
    if isinstance(event, asyncio.Event):
        await asyncio.wait_for(event.wait(), WAIT)
    elif not await asyncio.to_thread(event.wait, WAIT):
        raise TimeoutError("sync replay event was not reached")


class Gate:
    def __init__(self, native):
        self.native = native
        self.entered = asyncio.Event() if native else threading.Event()
        self.released = asyncio.Event() if native else threading.Event()
        self.cancelled = False

    def block(self):
        if self.native:
            return self.block_async()
        self.entered.set()
        if not self.released.wait(WAIT):
            raise TimeoutError("sync replay event was not released")
        return None

    async def block_async(self):
        self.entered.set()
        try:
            await asyncio.wait_for(self.released.wait(), WAIT)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def release(self):
        self.released.set()


def reply(case):
    if case in {"401", "availability"}:
        cls, status = (
            (NaceAuthenticationError, 401)
            if case == "401"
            else (NaceInternalServerError, 503)
        )
        raise cls(status, {}, httpx.Headers())
    if case == "masked":
        return SystemOneResponse.from_http_response(
            httpx.Response(
                200,
                json={
                    "model": RESPONSE_MODEL,
                    "usage": [],
                    "answers": {"policy": {"type": "score", "score": None}},
                },
            )
        )
    if case == "unusable":
        return response(
            ScoreAnswer(
                score=0.1,
                confidence=0.8,
                legend=("low", "high"),
                probabilities={0: 0.9, 1: 0.1},
            ),
            "other",
        )
    value = {"pass": 0.1, "fail": 0.9, "nonfinite": float("nan")}[case]
    return response(
        ScoreAnswer(
            score=value,
            confidence=0.8,
            legend=("low", "high"),
            probabilities={0: 0.9, 1: 0.1},
        )
    )


def validation_case(path, *, duplicate_fail=False):
    states, steps = path
    setup = states[0]
    route, mode, action = (setup[name] for name in ("route", "mode", "action"))
    native = route == "native"
    expected = states[-1]
    cancelled = expected["cancelled"]
    before_start = "CancelBeforeStart" in steps
    executor_pre_call = "CancelExecutorBeforeCall" in steps
    scope = (route, mode, action, setup["reply"], setup["retry"], steps)
    cancel_value = (
        REPLACEMENT
        if "CallReplacement" in steps or "EnterReplacementExecutor" in steps
        else INITIAL
    )
    sdk_gate = Gate(native)
    input_gate = Gate(False)
    corrections = Counter()
    results = {}
    custom_calls = []

    images_fn = main._images

    def observe_images(metadata, model):
        if len(fake.calls) == int(cancel_value == REPLACEMENT):
            input_gate.block()
        return images_fn(metadata, model)

    def handler(value, *_):
        case = setup["retry" if value == REPLACEMENT else "reply"]
        if cancelled and not before_start and value == cancel_value:
            blocked = sdk_gate.block()
            if native:

                async def complete():
                    await blocked
                    return reply(case)

                return complete()
        return reply(case)

    fake = Recording(handler)

    def record(value, invoke):
        try:
            result = invoke()
        except (NaceAPIError, DrexPolicyError):
            results[value] = "raise"
            raise
        results[value] = result.outcome
        return result

    def observe_sync(self, value, metadata=None, cancelled=None):
        return record(value, lambda: sync_validate(self, value, metadata, cancelled))

    async def observe_async(self, value, metadata=None):
        # The executor's worker can finish after the Guard task was cancelled.
        try:
            result = await async_validate(self, value, metadata)
        except (NaceAPIError, DrexPolicyError):
            results[value] = "raise"
            raise
        results[value] = result.outcome
        return result

    def observe_correction(original):
        def observe(self, result, value, validator, *args, **kwargs):
            corrections[value] += 1
            corrected = original(self, result, value, validator, *args, **kwargs)
            if duplicate_fail:
                corrections[value] += 1
                return original(self, result, value, validator, *args, **kwargs)
            return corrected

        return observe

    def custom(value, failure):
        custom_calls.append(value)
        return "corrected"

    async def invoke(validator, metadata):
        guard = guard_for(route, validator)
        if not cancelled:
            try:
                return await guarded(route, guard, INITIAL, metadata)
            except (DrexPolicyError, NaceAPIError, ValidationError) as error:
                return error
        task = asyncio.create_task(guarded(route, guard, INITIAL, metadata))
        try:
            if not before_start:
                await wait_signal(
                    input_gate.entered if executor_pre_call else sdk_gate.entered
                )
            task.cancel()
            try:
                return await asyncio.wait_for(task, WAIT)
            except asyncio.CancelledError as error:
                return error
        finally:
            input_gate.release()
            sdk_gate.release()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    sync_validate = DrexPolicy._validate
    async_validate = DrexPolicy.async_validate
    with ExitStack() as stack:
        for service in (SequentialValidatorService, AsyncValidatorService):
            stack.enter_context(
                patch.object(
                    service,
                    "perform_correction",
                    observe_correction(service.perform_correction),
                )
            )
        stack.enter_context(patch.object(DrexPolicy, "_validate", observe_sync))
        stack.enter_context(patch.object(DrexPolicy, "async_validate", observe_async))
        validator = build(
            route,
            fake,
            question=SCORE,
            on_error=mode,
            on_fail=custom if action == "custom" else action,
            fix_value=REPLACEMENT if action in {"fix", "fix_reask"} else None,
        )
        metadata = {}
        if setup["reply"] == "refused":
            metadata = {"images": ["https://example.invalid/image"]}
        elif executor_pre_call:
            stack.enter_context(patch.object(main, "_images", observe_images))
            metadata = {"images": ["AAAA"]}
        outcome = asyncio.run(invoke(validator, metadata))
    counts = Counter(call[0] for call in fake.calls)
    return (
        scope,
        expected,
        outcome,
        counts,
        corrections,
        results,
        custom_calls,
        sdk_gate,
    )


def validation_violations(path, *, duplicate_fail=False):
    scope, expected, outcome, counts, corrections, results, custom_calls, sdk_gate = (
        validation_case(path, duplicate_fail=duplicate_fail)
    )
    route, mode, action, reply_kind, retry_kind, steps = scope
    details = {
        "path": scope,
        "calls": dict(counts),
        "corrections": dict(corrections),
        "results": results,
        "outcome": (
            {
                "validation_passed": outcome.validation_passed,
                "output": outcome.validated_output,
                "reask": outcome.reask is not None,
            }
            if hasattr(outcome, "validation_passed")
            else repr(outcome)
        ),
    }
    failed = (
        reply_kind == "fail"
        or mode == "fail"
        and reply_kind
        in {
            "availability",
            "unusable",
            "nonfinite",
            "masked",
        }
    )
    if (
        not expected["cancelled"]
        and failed
        and not isinstance(outcome, BaseException)
        and corrections[INITIAL] != 1
    ):
        yield "P1", details
    if (
        not expected["cancelled"]
        and results.get(REPLACEMENT) == "fail"
        and corrections[REPLACEMENT] != 1
    ):
        yield "P1", details
    if (
        not expected["cancelled"]
        and failed
        and not isinstance(outcome, BaseException)
        and expected["retryResult"] != "raise"
        and not correction_matches(
            action, expected["retryResult"], outcome, custom_calls
        )
    ):
        yield "P1", details

    if (
        not expected["cancelled"]
        and action == "fix_reask"
        and counts[REPLACEMENT]
        and retry_kind != "pass"
        and getattr(outcome, "validation_passed", False)
    ):
        yield "P1", details

    if not expected["cancelled"] and (
        results.get(INITIAL) != expected["result"]
        or results.get(REPLACEMENT, "none") != expected["retryResult"]
    ):
        yield "P2", details
    if not expected["cancelled"]:
        for attempt, kind in ((INITIAL, reply_kind), (REPLACEMENT, retry_kind)):
            if results.get(attempt) == "pass" and not (
                kind == "pass" or mode == "pass" and kind == "availability"
            ):
                yield "P2", details

    if expected["cancelled"]:
        active_attempt = (
            REPLACEMENT
            if "CallReplacement" in steps or "EnterReplacementExecutor" in steps
            else INITIAL
        )
        late = "CancelExecutorBeforeCall" in steps and counts[active_attempt] > 0
        transport_swallowed = (
            route == "native" and "CancelInFlight" in steps and not sdk_gate.cancelled
        )
        validator_returned = route == "native" and results.get(active_attempt) in {
            "pass",
            "fail",
        }
        if (
            not isinstance(outcome, asyncio.CancelledError)
            or late
            or transport_swallowed
            or validator_returned
        ):
            yield "P3", details

    if (counts[INITIAL], counts[REPLACEMENT]) != (
        expected["calls"],
        expected["retryCalls"],
    ):
        yield "P6", details
    if (
        not expected["cancelled"]
        and reply_kind == "refused"
        and not isinstance(outcome, DrexPolicyError)
    ):
        yield "P6", details


def correction_matches(action, retry_result, outcome, custom_calls):
    if action == "exception":
        return isinstance(outcome, ValidationError)
    if not hasattr(outcome, "validated_output"):
        return False
    if action == "reask" or retry_result == "fail":
        return outcome.validation_passed is False and outcome.reask is not None
    if action == "custom":
        return custom_calls == [INITIAL] and outcome.validated_output == "corrected"
    if action in {"fix", "fix_reask"}:
        return (
            outcome.validation_passed is True
            and outcome.validated_output == REPLACEMENT
        )
    if action == "noop":
        return (
            outcome.validation_passed is False and outcome.validated_output == INITIAL
        )
    return outcome.validation_passed is False and outcome.validated_output is None


def p3_native_wire():
    async def run():
        for mode in ("raise", "fail", "pass"):
            gate = Gate(True)
            requests = []

            async def handler(request, gate=gate, requests=requests):
                requests.append(request)
                await gate.block()
                raise RuntimeError("The mock request should have been cancelled")

            async with mock_client(
                handler, asynchronous=True, retry=RetryPolicy(max_retries=0)
            ) as client:
                policy = DrexPolicy(
                    SCORE, 0.5, on_fail="noop", on_error=mode, async_client=client
                )
                guard = guard_for("native", policy)
                task = asyncio.create_task(guarded("native", guard, INITIAL))
                try:
                    await wait_signal(gate.entered)
                    task.cancel()
                    try:
                        outcome = await asyncio.wait_for(task, WAIT)
                    except asyncio.CancelledError:
                        require(
                            gate.cancelled and len(requests) == 1,
                            (mode, gate.cancelled, requests),
                        )
                    else:
                        require(False, (mode, outcome))
                finally:
                    gate.release()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def p6_non_string():
    fake = Recording(lambda *_: response(NoulAnswer(noul=0.1)))

    async def run():
        for route in ("sync", "native", "executor"):
            policy = build(route, fake)
            for value in (None, 1, b"bytes", ["text"], {"text": "value"}):
                try:
                    if route == "sync":
                        outcome = policy.validate(value)
                    else:
                        outcome = await policy.async_validate(value)
                except TypeError:
                    require(fake.calls == [], (route, value, fake.calls))
                else:
                    require(False, (route, value, outcome))

    asyncio.run(run())


def concurrency_case(path):
    states, steps = path
    route = states[0]["route"]
    native = route == "native"
    calls = {1: Gate(native), 2: Gate(native)}
    reads = {1: Gate(False), 2: Gate(False)}
    tasks = {}
    schedule = []
    measured = {1: 0.1, 2: 0.9}

    def handler(value, *_):
        attempt = int(value)
        block = calls[attempt].block()
        answer = response(NoulAnswer(noul=measured[attempt]))
        if native:

            async def complete():
                await block
                return answer

            return complete()
        return answer

    policy = build(route, Recording(handler), on_fail="noop")
    guards = {i: guard_for(route, policy) for i in calls}
    source = main._FORMAL_SOURCE.splitlines()
    marker = next(
        i
        for i, line in enumerate(source, 1)
        if line.strip()
        in {"if isinstance(answer, NoulAnswer):", "answer = self._formal_last_answer"}
        and (
            main._FORMAL_MUTANT != "shared_answer" or "self._formal_last_answer" in line
        )
    )

    def trace(frame, event, arg):
        if event == "line" and frame.f_lineno == marker:
            local_response = frame.f_locals["response"]
            attempt = 1 if local_response.answers["policy"].noul == measured[1] else 2
            reads[attempt].block()
        return trace

    def attach(frame, event, arg):
        if frame.f_code is DrexPolicy._result.__code__:
            return trace
        return None

    async def run_guard(i):
        if route == "sync":
            return await asyncio.to_thread(guards[i].validate, str(i))
        return await guarded(route, guards[i], str(i))

    async def run():
        try:
            for index, step in enumerate(steps):
                before, after = states[index]["phase"], states[index + 1]["phase"]
                (attempt,) = [i for i in calls if before[i - 1] != after[i - 1]]
                schedule.append((step, attempt))
                if step == "Call":
                    tasks[attempt] = asyncio.create_task(run_guard(attempt))
                    await wait_signal(calls[attempt].entered)
                elif step == "ReadAnswer":
                    calls[attempt].release()
                    await wait_signal(reads[attempt].entered)
                elif step == "BuildResult":
                    (calls if native else reads)[attempt].release()
                    await asyncio.wait_for(tasks[attempt], WAIT)
                else:
                    raise ValueError(step)
            observed = {
                i: (
                    result_at(guards[i]).metadata["drex_measured"],
                    tasks[i].result().validation_passed,
                    result_at(guards[i]).metadata["drex_response_model"],
                )
                for i in tasks
            }
            expected = {i: (measured[i], i == 1, RESPONSE_MODEL) for i in tasks}
            require(observed == expected, (route, schedule, expected, observed))
        finally:
            for gate in [*calls.values(), *reads.values()]:
                gate.release()
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    previous = threading.gettrace()
    if not native:
        threading.settrace(attach)
    try:
        asyncio.run(run())
    finally:
        threading.settrace(previous)

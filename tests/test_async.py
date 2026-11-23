import asyncio
import json
import threading

import httpx
import pytest
from guardrails import AsyncGuard
from nace_sdk import Noul

from drex_policy import DrexPolicy

from .fakes import mock_client, noul, payload

QUESTION = Noul(instructions="Is this unsafe?")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "fail", "pass"])
async def test_cancellation_stops_the_native_http_call(mode):
    started, stopped = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            stopped.set()
            raise

    async with mock_client(handler, asynchronous=True) as client:
        validator = DrexPolicy(QUESTION, 0.5, async_client=client, on_error=mode)
        task = asyncio.create_task(AsyncGuard().use(validator).validate("x"))
        try:
            await asyncio.wait_for(started.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stopped.is_set()
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_sync_client_keeps_executor_path():
    loop_thread = threading.get_ident()
    client = noul(0.1)
    original = client.system_one

    def check_thread(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        return original(*args, **kwargs)

    client.system_one = check_thread
    result = (
        await AsyncGuard().use(DrexPolicy(QUESTION, 0.5, client=client)).validate("x")
    )
    assert result.validation_passed is True
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_native_calls_overlap_without_sharing_results():
    both_started = asyncio.Event()
    seen = []

    async def handler(request):
        state = json.loads(request.content)["state"]
        seen.append(state)
        if len(seen) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 5)
        return httpx.Response(
            200,
            json=payload(
                "response", {"policy": {"type": "noul", "noul": float(state)}}
            ),
        )

    async with mock_client(handler, asynchronous=True) as client:
        validator = DrexPolicy(QUESTION, 0.5, async_client=client, on_fail="noop")
        guards = [AsyncGuard().use(validator), AsyncGuard().use(validator)]
        results = await asyncio.gather(
            guards[0].validate("0.1"), guards[1].validate("0.9")
        )
    assert sorted(seen) == ["0.1", "0.9"]
    assert [result.validation_passed for result in results] == [True, False]
    assert [
        guard.history.last.validator_logs[0].validation_result.metadata["drex_measured"]
        for guard in guards
    ] == [0.1, 0.9]


@pytest.mark.asyncio
async def test_closes_only_owned_clients(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            self.closed = 0

        def close(self):
            self.closed += 1

        async def aclose(self):
            self.closed += 1

    monkeypatch.setattr("drex_policy.main.NaceClient", Client)
    monkeypatch.setattr("drex_policy.main.AsyncNaceClient", Client)
    validator = DrexPolicy(QUESTION, 0.5)
    sync, asynchronous = validator._get_client(), validator._get_async_client()
    validator.close()
    validator.close()
    await validator.aclose()
    await validator.aclose()
    assert sync.closed == asynchronous.closed == 1

    sync, asynchronous = Client(), Client()
    validator = DrexPolicy(QUESTION, 0.5, client=sync, async_client=asynchronous)
    validator.close()
    await validator.aclose()
    assert sync.closed == asynchronous.closed == 0


@pytest.mark.asyncio
async def test_fix_reask_keeps_images_for_the_replacement():
    from .fakes import FakeAsyncClient

    client = FakeAsyncClient(
        lambda text: {"type": "noul", "noul": 0.1 if text == "safe" else 0.9}
    )
    policy = DrexPolicy(
        QUESTION, 0.5, async_client=client, on_fail="fix_reask", fix_value="safe"
    )
    metadata = {"images": ["AAAA"], "other_validator": "preserved"}
    result = await AsyncGuard().use(policy).validate("unsafe", metadata=metadata)
    assert result.validated_output == "safe"
    assert [(call[0], call[3]) for call in client.calls] == [
        ("unsafe", ["AAAA"]),
        ("safe", ["AAAA"]),
    ]
    assert metadata == {"images": ["AAAA"], "other_validator": "preserved"}


@pytest.mark.parametrize("cleanup", ["close", "aclose"])
def test_owned_async_clients_are_scoped_to_each_loop(monkeypatch, cleanup):
    from nace_sdk import AsyncNaceClient

    created, closed, request_loops = [], [], []

    def factory(**kwargs):
        owner = asyncio.get_running_loop()

        async def handler(request):
            assert asyncio.get_running_loop() is owner
            request_loops.append(owner)
            return httpx.Response(
                200,
                json=payload(
                    "drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}}
                ),
            )

        class Client(AsyncNaceClient):
            async def aclose(self):
                await super().aclose()
                closed.append(self)

        client = Client(
            api_key="nace_sk_test", transport=httpx.MockTransport(handler), **kwargs
        )
        created.append(client)
        return client

    monkeypatch.setattr("drex_policy.main.AsyncNaceClient", factory)
    policy = DrexPolicy(QUESTION, 0.5)

    async def validate():
        guard = AsyncGuard().use(policy)
        assert (await guard.validate("x")).validation_passed is True
        # A second call in the same loop reuses the client.
        assert (await guard.validate("y")).validation_passed is True

    try:
        asyncio.run(validate())
        asyncio.run(validate())
    finally:
        if cleanup == "close":
            policy.close()
        else:
            asyncio.run(policy.aclose())
    assert len(created) == 2
    assert closed == created
    assert request_loops[0] is request_loops[1]
    assert request_loops[2] is request_loops[3]
    assert request_loops[0] is not request_loops[2]


@pytest.mark.asyncio
async def test_async_stream_uses_the_sync_client_in_a_thread():
    from .fakes import FakeAsyncClient

    client = noul(0.1)
    async_client = FakeAsyncClient({"type": "noul", "noul": 0.9})
    main_thread = threading.get_ident()
    threads = []
    original = client.system_one

    def record(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)

    client.system_one = record
    policy = DrexPolicy(QUESTION, 0.5, client=client, async_client=async_client)
    result = await policy.async_validate_stream("A complete sentence. ", {})
    assert result.outcome == "pass"
    assert [call[0].strip() for call in client.calls] == ["A complete sentence."]
    assert threads and all(thread != main_thread for thread in threads)
    assert async_client.calls == []


def test_owned_async_clients_in_two_threads_are_all_released(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from nace_sdk import AsyncNaceClient

    ready = threading.Barrier(2)
    created, closed = [], []

    def factory(**kwargs):
        owner = asyncio.get_running_loop()

        async def handler(request):
            assert asyncio.get_running_loop() is owner
            return httpx.Response(
                200,
                json=payload(
                    "drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}}
                ),
            )

        class Client(AsyncNaceClient):
            async def aclose(self):
                await super().aclose()
                closed.append(self)

        client = Client(
            api_key="nace_sk_test", transport=httpx.MockTransport(handler), **kwargs
        )
        created.append(client)
        return client

    monkeypatch.setattr("drex_policy.main.AsyncNaceClient", factory)
    policy = DrexPolicy(QUESTION, 0.5)

    def worker():
        async def validate():
            ready.wait(timeout=5)
            return await policy.async_validate("x")

        return asyncio.run(validate())

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        assert [result.outcome for result in results] == ["pass", "pass"]
    finally:
        policy.close()
    assert len(created) == 2
    assert len(closed) == 2
    assert {id(client) for client in created} == {id(client) for client in closed}


@pytest.mark.parametrize("cleanup", ["close", "aclose"])
def test_cleanup_uses_a_stopped_owner_loop(monkeypatch, cleanup):
    from nace_sdk import AsyncNaceClient

    closed = []

    def factory(**kwargs):
        owner = asyncio.get_running_loop()

        async def handler(request):
            return httpx.Response(
                200,
                json=payload(
                    "drex-max-v1.1", {"policy": {"type": "noul", "noul": 0.1}}
                ),
            )

        class Client(AsyncNaceClient):
            async def aclose(self):
                assert asyncio.get_running_loop() is owner
                await super().aclose()
                closed.append(self)

        return Client(
            api_key="nace_sk_test", transport=httpx.MockTransport(handler), **kwargs
        )

    monkeypatch.setattr("drex_policy.main.AsyncNaceClient", factory)
    policy = DrexPolicy(QUESTION, 0.5)
    owner = asyncio.new_event_loop()
    try:
        assert owner.run_until_complete(policy.async_validate("x")).outcome == "pass"
        if cleanup == "close":
            policy.close()
        else:
            asyncio.run(policy.aclose())
        assert len(closed) == 1
    finally:
        owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("on_error", ["raise", "fail", "pass"])
@pytest.mark.parametrize("stage", ["initial", "replacement"])
async def test_cancellation_before_thread_request_prevents_system_one(
    monkeypatch, on_error, stage
):
    import drex_policy.main as implementation

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    image_checks = 0
    original_images = implementation._images

    def gated_images(*args, **kwargs):
        nonlocal image_checks
        image_checks += 1
        if image_checks == (1 if stage == "initial" else 2):
            entered.set()
            assert release.wait(5)
        return original_images(*args, **kwargs)

    monkeypatch.setattr(implementation, "_images", gated_images)
    client = noul(0.9)
    policy = DrexPolicy(
        QUESTION,
        0.5,
        client=client,
        on_error=on_error,
        on_fail="fix_reask",
        fix_value="replacement",
    )
    original_validate = policy._validate

    def tracked_validate(*args, **kwargs):
        try:
            return original_validate(*args, **kwargs)
        finally:
            if entered.is_set():
                finished.set()

    monkeypatch.setattr(policy, "_validate", tracked_validate)
    task = asyncio.create_task(
        AsyncGuard().use(policy).validate("initial", metadata={"images": ["AAAA"]})
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert await asyncio.wait_for(asyncio.to_thread(finished.wait, 5), 6)
    assert [call[0] for call in client.calls] == (
        [] if stage == "initial" else ["initial"]
    )

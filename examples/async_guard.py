import asyncio

from _common import arguments, fake_client


async def check(fake):
    from guardrails import AsyncGuard
    from nace_sdk import Noul

    from drex_policy import DrexPolicy

    client = fake_client(fake, asynchronous=True)
    policy = DrexPolicy(
        Noul(instructions="Does this reply ask for a password?"),
        fail_at=0.5,
        async_client=client,
        on_fail="exception",
    )
    try:
        result = (
            await AsyncGuard()
            .use(policy)
            .validate("Open Settings to change your password.")
        )
        print(result.validated_output)
    finally:
        await policy.aclose()
        if client is not None:
            await client.aclose()


if __name__ == "__main__":
    asyncio.run(check(arguments().fake))

from _common import arguments, fake_client


def main():
    args = arguments()

    from guardrails import Guard
    from nace_sdk import Noul

    from drex_policy import DrexPolicy

    client = fake_client(args.fake)
    policy = DrexPolicy(
        Noul(instructions="Is this message outside the scope of banking support?"),
        fail_at=0.5,
        client=client,
        on_fail="exception",
    )
    guard = Guard().use(policy, on="messages")
    try:
        result = guard(
            lambda *args, **kwargs: "You can view your balance in the app.",
            messages=[{"role": "user", "content": "How do I see my account balance?"}],
        )
        print(result.validated_output)
    finally:
        policy.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()

from _common import arguments, fake_client


def main():
    args = arguments()

    from guardrails import Guard
    from nace_sdk import Noul
    from pydantic import BaseModel, Field

    from drex_policy import DrexPolicy

    client = fake_client(args.fake)
    policy = DrexPolicy(
        Noul(instructions="Does the reply mention anything outside billing support?"),
        fail_at=0.5,
        client=client,
        on_fail="exception",
    )

    class Reply(BaseModel):
        answer: str = Field(validators=[policy])

    try:
        guard = Guard.for_pydantic(output_class=Reply)
        result = guard.parse('{"answer": "Find your invoice in Settings > Billing."}')
        print(result.validated_output)
    finally:
        policy.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()

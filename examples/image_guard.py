from _common import arguments, demo_image, fake_client


def main():
    args = arguments()

    from guardrails import Guard
    from nace_sdk import Score, image_input

    from drex_policy import DrexPolicy

    client = fake_client(args.fake)
    policy = DrexPolicy(
        Score(instructions="How unsafe is this image?", criteria=["safe", "unsafe"]),
        fail_at=0.5,
        client=client,
        on_fail="exception",
    )
    try:
        with demo_image() as path:
            result = (
                Guard()
                .use(policy)
                .validate("A plain square.", metadata={"images": [image_input(path)]})
            )
        print(result.validated_output)
    finally:
        policy.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()

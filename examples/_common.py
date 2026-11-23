import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAsTj2FAAAAABJRU5ErkJggg=="


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fake", action="store_true", help="Run offline with synthetic answers"
    )
    args = parser.parse_args()
    if args.fake:
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        os.environ["OTEL_SDK_DISABLED"] = "true"
    elif not os.environ.get("NACE_API_KEY"):
        parser.error("Set NACE_API_KEY or run with --fake.")
    return args


def fake_client(fake, *, asynchronous=False):
    if not fake:
        return None
    import httpx
    from guardrails.settings import settings
    from nace_sdk import AsyncNaceClient, NaceClient

    settings.rc.enable_metrics = False

    def handler(request):
        body = json.loads(request.content)
        answers = {
            name: (
                {"type": "noul", "noul": 0.05}
                if question["type"] == "noul"
                else {
                    "type": "score",
                    "score": 0.1,
                    "confidence": 0.9,
                    "legend": ["safe", "unsafe"],
                    "probabilities": {"0": 0.9, "1": 0.1},
                }
            )
            for name, question in body["questions"].items()
        }
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "answers": answers,
                "usage": {"input_tokens": 9},
            },
        )

    async def async_handler(request):
        return handler(request)

    client = AsyncNaceClient if asynchronous else NaceClient
    return client(
        api_key="nace_sk_test",
        base_url="https://drex.invalid",
        transport=httpx.MockTransport(async_handler if asynchronous else handler),
    )


@contextmanager
def demo_image():
    import base64

    with TemporaryDirectory() as directory:
        path = Path(directory) / "example.png"
        path.write_bytes(base64.b64decode(PNG_BASE64))
        yield path

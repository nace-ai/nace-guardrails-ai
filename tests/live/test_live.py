import base64
import os
from importlib.metadata import version

import httpx
import pytest
from nace_sdk import NaceClient, Noul, Score, image_input

from drex_policy import DrexPolicy
from drex_policy.main import CLIENT_INFO, DrexPolicyError

pytestmark = pytest.mark.live

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAsTj2FAAAAABJRU5ErkJggg=="
)

MODELS = [
    (None, "drex-max-v1.1", ["noul", "score", "image"]),
    ("drex-v2.0", "drex-v2.0", ["noul", "score", "refuse-image"]),
    ("drex-max-v1.1", "drex-max-v1.1", ["noul", "score", "image"]),
    ("drex-latest", "drex-max-v1.1", ["noul", "image"]),
    ("drex-max", "drex-max-v1.1", ["noul", "image"]),
    ("drex-max-finance", "drex-max-finance", ["noul"]),
]


@pytest.fixture
def live_client(monkeypatch):
    key = os.environ.get("NACE_API_KEY")
    if not key:
        pytest.fail("Set NACE_API_KEY to run the live tests.")
    headers = []

    def capture(request):
        headers.append(
            (request.headers.get("x-nace-client"), request.headers.get("user-agent"))
        )

    with httpx.Client(event_hooks={"request": [capture]}) as http_client:
        monkeypatch.setattr(
            "drex_policy.main.NaceClient",
            lambda **kwargs: NaceClient(api_key=key, http_client=http_client, **kwargs),
        )
        yield headers


@pytest.mark.parametrize(
    ("model", "response_model", "kind"),
    [(model, response, kind) for model, response, kinds in MODELS for kind in kinds],
)
def test_live_model(model, response_model, kind, live_client):
    question = (
        Score(instructions="How formal is the message?", criteria=["casual", "formal"])
        if kind == "score"
        else Noul(instructions="Does the message refer to a square?")
    )
    validator = DrexPolicy(question, 0.5, **({"model": model} if model else {}))
    metadata = (
        {"images": [image_input(PNG)]} if kind in {"image", "refuse-image"} else {}
    )
    try:
        if kind == "refuse-image":
            with pytest.raises(DrexPolicyError):
                validator.validate("A plain square.", metadata)
            assert live_client == []
            return
        result = validator.validate("A plain square.", metadata)
        assert result.metadata["drex_response_model"] == response_model
        assert result.metadata["drex_answer"]["type"] == (
            "score" if kind == "score" else "noul"
        )
        assert live_client
        for client_info, user_agent in live_client:
            assert client_info == CLIENT_INFO
            expected_agent = f"nace-sdk/{version('nace-sdk')}"
            # The SDK changelog first documents User-Agent integration in 0.7.0.
            if tuple(map(int, version("nace-sdk").split("."))) >= (0, 7, 0):
                expected_agent += f" {CLIENT_INFO}"
            assert user_agent == expected_agent
    finally:
        validator.close()

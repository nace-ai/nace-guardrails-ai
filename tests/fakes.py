import json

import httpx
from nace_sdk import AsyncNaceClient, NaceClient, SystemOneResponse


def payload(model, answers):
    return {
        "model": model,
        "usage": {"input_tokens": 4, "image_tokens": 0, "output_tokens": 1},
        "answers": answers,
    }


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def system_one(self, state, questions, *, model=None, images=None):
        self.calls.append((state, questions, model, images))
        answer = self.answer(state) if callable(self.answer) else self.answer
        return SystemOneResponse.model_validate_json(
            json.dumps(payload(model, dict.fromkeys(questions, answer)))
        )


class FakeAsyncClient(FakeClient):
    async def system_one(self, state, questions, *, model=None, images=None):
        return super().system_one(state, questions, model=model, images=images)


def noul(probability):
    return FakeClient({"type": "noul", "noul": probability})


def score(value):
    return FakeClient(
        {
            "type": "score",
            "score": value,
            "confidence": 0.8,
            "legend": ["none", "low", "high"],
            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
        }
    )


def mock_client(handler, *, asynchronous=False, **kwargs):
    factory = AsyncNaceClient if asynchronous else NaceClient
    return factory(
        api_key="nace_sk_test",
        base_url="https://drex.invalid",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )

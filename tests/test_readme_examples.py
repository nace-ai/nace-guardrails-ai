"""Run the README Python blocks with the real SDK and a local transport."""

import json
import re
import runpy
from pathlib import Path

import httpx
import pytest
from guardrails.errors import ValidationError

from .fakes import mock_client, payload, score

README = Path(__file__).resolve().parent.parent / "README.md"
BLOCKS = re.findall(r"```python\n(.*?)```", README.read_text(), re.DOTALL)


def test_readme_has_python_blocks():
    assert len(BLOCKS) >= 4


@pytest.mark.parametrize("block", BLOCKS)
def test_readme_block_runs(block, monkeypatch, tmp_path):
    assert "guardrails.hub" not in block
    requests = []
    clients = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        answers = {}
        for name, question in body["questions"].items():
            if question["type"] == "noul":
                probability = 0.9 if "+1 415" in body["state"] else 0.1
                answers[name] = {"type": "noul", "noul": probability}
            else:
                answers[name] = score(0.5).answer
        return httpx.Response(200, json=payload(body["model"], answers))

    async def async_handler(request):
        return handler(request)

    def build(**kwargs):
        client = mock_client(handler, **kwargs)
        clients.append(client)
        return client

    def build_async(**kwargs):
        client = mock_client(async_handler, asynchronous=True, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setenv("GUARDRAILS_RUN_SYNC", "true")
    monkeypatch.setattr("drex_policy.main.NaceClient", build)
    monkeypatch.setattr("drex_policy.main.AsyncNaceClient", build_async)
    script = tmp_path / "example.py"
    script.write_text(block)
    try:
        if "+1 415 555 0100" in block:
            with pytest.raises(ValidationError, match="Validation failed"):
                runpy.run_path(str(script))
            assert len(requests) == 2
        else:
            runpy.run_path(str(script))
            assert len(requests) == (1 if ".use(" in block or ".parse(" in block else 0)
        if "image_input(" in block:
            assert requests[0]["images"][0].startswith("data:image/png;base64,")
    finally:
        for client in clients:
            # The async example closes its client on the loop that used it.
            if hasattr(client, "close"):
                client.close()

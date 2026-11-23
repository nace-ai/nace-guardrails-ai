import os
import socket

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["OTEL_SDK_DISABLED"] = "true"


@pytest.fixture(autouse=True)
def offline(request, monkeypatch):
    from guardrails.settings import settings

    monkeypatch.setattr(settings.rc, "enable_metrics", False)
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.delenv("NACE_API_KEY", raising=False)

    def deny_network(*args, **kwargs):
        raise AssertionError("Offline tests must use a fake client or MockTransport")

    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)

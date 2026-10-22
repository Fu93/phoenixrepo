import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.adapters.meterless import (
    MeterlessRuntime,
    candidate_base_urls,
    message_text,
    normalize_meterless_base_url,
    parse_connect_link,
)
from app.adapters.nvidia import NvidiaDirect, normalize_nvidia_base_url


def test_base_url_normalises_without_doubling_v1():
    assert normalize_meterless_base_url("http://127.0.0.1:8088/v1") == "http://127.0.0.1:8088/v1"
    assert normalize_meterless_base_url("127.0.0.1:8088") == "http://127.0.0.1:8088/v1"
    assert (
        normalize_meterless_base_url("http://127.0.0.1:8088/v1/chat/completions")
        == "http://127.0.0.1:8088/v1"
    )
    assert normalize_meterless_base_url("http://127.0.0.1:8088/healthz") == "http://127.0.0.1:8088/v1"
    assert "/v1/v1" not in normalize_meterless_base_url("http://127.0.0.1:8088/v1/v1")


def test_connect_link_is_not_confused_with_a_base_url():
    parsed = parse_connect_link("http://127.0.0.1:8088/connect#k=super-secret&m=meterless/fast&n=phoenixrepo")
    assert parsed["base_url"] == "http://127.0.0.1:8088/v1"
    assert parsed["api_key"] == "super-secret"
    assert parsed["model"] == "meterless/fast"
    assert parse_connect_link("http://127.0.0.1:8088/v1") is None


def test_placeholder_token_is_rejected_before_http():
    runtime = MeterlessRuntime(token="rt-local")
    with pytest.raises(Exception) as caught:
        runtime.connection_status()
    assert "rt-local" in str(caught.value)
    assert caught.value.status_code == 401


def test_auto_detect_candidates_cover_the_documented_ports():
    urls = candidate_base_urls()
    assert "http://127.0.0.1:8088/v1" in urls
    assert "http://localhost:8090/v1" in urls
    assert "http://[::1]:8787/v1" in urls
    assert any(url.endswith(":11434/v1") for url in urls)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") == "Bearer bad":
            self.send_response(401)
            self.end_headers()
            return
        if self.headers.get("meterless-app") != "phoenixrepo":
            self.send_response(400)
            self.end_headers()
            return
        body = json.dumps({"data": [{"id": "meterless/auto"}, {"id": "other/model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = json.loads(self.rfile.read(length).decode())
        assert self.headers.get("meterless-scope") == "user"
        assert self.headers.get("meterless-app") == "phoenixrepo"
        assert raw["model"] == "meterless/auto"
        body = json.dumps(
            {"choices": [{"message": {"content": [{"type": "text", "text": "ready"}]}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_discovery_and_chat_use_the_runtime_contract():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        bad = MeterlessRuntime(base_url=f"http://127.0.0.1:{port}/v1", token="bad", timeout_seconds=5)
        assert bad.connection_status().state == "unauthorized"
        good = MeterlessRuntime(base_url=f"http://127.0.0.1:{port}/v1", token="real-token", timeout_seconds=5)
        status = good.connection_status()
        assert status.state == "online"
        assert "meterless/auto" in status.models
        assert good.complete([{"role": "user", "content": "ping"}]) == "ready"
    finally:
        server.shutdown()


def test_message_text_keeps_a_plain_string():
    assert message_text({"choices": [{"message": {"content": "ready"}}]}) == "ready"


def test_nvidia_is_secondary_and_omits_meterless_headers():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_POST(self):
            seen["app"] = self.headers.get("meterless-app")
            seen["auth"] = self.headers.get("Authorization")
            body = json.dumps({"choices": [{"message": {"content": "nvidia"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = NvidiaDirect(
            api_key="nv-test",
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            model="nvidia/nemotron-3-super",
            timeout_seconds=5,
        )
        assert client.status()["primary"] is False
        assert client.status()["secondary_dev_fallback"] is True
        assert client.complete([{"role": "user", "content": "ping"}]) == "nvidia"
        assert seen["app"] is None
        assert seen["auth"] == "Bearer nv-test"
        assert normalize_nvidia_base_url("https://integrate.api.nvidia.com/v1") == "https://integrate.api.nvidia.com/v1"
    finally:
        server.shutdown()

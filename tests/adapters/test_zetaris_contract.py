import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.adapters.zetaris import ZetarisClient, assert_single_statement, rows_from_payload
from app.errors import ZetarisError


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        body = json.loads(raw.decode())
        _Handler.seen.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "org": self.headers.get("X-Org-ID"),
                "request_id": self.headers.get("X-Request-ID"),
                "body": body,
            }
        )
        if body["sql"] == "SELECT boom":
            payload = {"error": {"message": "bad sql"}}
            status = 200
        elif self.headers.get("Authorization") == "Bearer wrong-key":
            payload = {"message": "nope"}
            status = 401
        else:
            payload = {"headers": ["one"], "data": [["1"]]}
            status = 200
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture()
def server():
    _Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()


def test_run_sql_matches_the_rest_contract(server):
    port = server.server_address[1]
    client = ZetarisClient(
        rest_url=f"http://127.0.0.1:{port}/api/v1.0",
        org_id="1",
        api_key="test-key-value",
        timeout_seconds=5,
    )
    first = client.run_sql("SELECT 1")
    second = client.run_sql("SELECT 1;")
    assert first.rows == [{"one": "1"}]
    assert first.mode == "zetaris-rest"
    assert first.query_id != second.query_id
    assert _Handler.seen[0]["path"] == "/api/v1.0/sql-editor/sqls/run"
    assert _Handler.seen[0]["authorization"] == "Bearer test-key-value"
    assert _Handler.seen[0]["org"] == "1"
    assert _Handler.seen[0]["request_id"] != _Handler.seen[1]["request_id"]
    assert _Handler.seen[0]["body"]["source"] == "SqlEditor"
    assert _Handler.seen[0]["body"]["queryId"] == first.query_id
    assert "test-key-value" not in json.dumps({"query": first.query_id, "sql": first.sql})


def test_multiple_statements_never_leave_the_process():
    with pytest.raises(ZetarisError):
        assert_single_statement("SELECT 1; SELECT 2")


def test_unauthorized_does_not_echo_the_key(server):
    port = server.server_address[1]
    client = ZetarisClient(f"http://127.0.0.1:{port}/api/v1.0", "1", "wrong-key", timeout_seconds=5)
    with pytest.raises(ZetarisError) as caught:
        client.run_sql("SELECT 1")
    assert "wrong-key" not in str(caught.value)
    assert caught.value.status_code == 401


def test_rows_from_object_data():
    columns, rows = rows_from_payload({"data": [{"repo_id": "sample/generic-qr"}]})
    assert columns == ["repo_id"]
    assert rows[0]["repo_id"] == "sample/generic-qr"

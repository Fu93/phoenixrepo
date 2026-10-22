import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.adapters.zetaris import ZetarisClient, assert_single_statement, rows_from_payload
from app.agents.policy import normalize_profile
from app.errors import ZetarisError

# Shape returned by POST /api/v1.0/sql-editor/sqls/run on the local Docker install:
# headers plus data arrays, every cell a string. Names are matched case-insensitively.
_LIVE_COLUMNS = [
    "repo_id",
    "licence_spdx",
    "days_since_push",
    "similar_repo_count",
    "unique_capability",
    "successor_url",
    "has_tests",
    "has_license_file",
    "ci_status",
    "category",
    "description",
    "data_origin",
]
_LIVE_ROW = [
    "sample/phoenix-ledger",
    "MIT",
    "226",
    "2",
    "offline double-entry ledger with a signed audit trail",
    "",
    "True",
    "True",
    "success",
    "accounting",
    "Offline double-entry ledger",
    "synthetic_labelled",
]


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
    columns, rows = rows_from_payload({"data": [{"Repo_ID": "sample/generic-qr"}]})
    assert columns == ["repo_id"]
    assert rows[0]["repo_id"] == "sample/generic-qr"


def test_live_sql_editor_shape_parses_string_cells():
    lowercase = {"headers": list(_LIVE_COLUMNS), "data": [list(_LIVE_ROW)]}
    mixed = {"headers": [name.upper() for name in _LIVE_COLUMNS], "data": [list(_LIVE_ROW)]}
    for payload in (lowercase, mixed):
        columns, rows = rows_from_payload(payload)
        assert columns == _LIVE_COLUMNS
        assert all(isinstance(value, str) for value in rows[0].values())
        profile = normalize_profile(rows[0])
        assert profile["repo_id"] == "sample/phoenix-ledger"
        assert profile["days_since_push"] == 226
        assert profile["similar_repo_count"] == 2
        assert profile["has_tests"] is True
        assert profile["licence_spdx"] == "MIT"

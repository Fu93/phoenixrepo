"""The attempt row has to describe the run that happened, using a fake GitHub client."""

import base64
from datetime import datetime, timezone

from scripts.collect_revival_attempt import collect_attempt

FORK = "Fu93/hygen"
UPSTREAM = "jondot/hygen"
COLLECTED = datetime(2026, 10, 22, tzinfo=timezone.utc)


def _file(text: str) -> dict:
    return {"encoding": "base64", "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def _repo(name: str) -> dict:
    owner, repo = name.split("/")
    return {"full_name": name, "default_branch": "master", "owner": {"login": owner}, "name": repo}


def _run(*, name: str, path: str, sha: str, head: str) -> dict:
    return {
        "id": 99,
        "name": name,
        "path": path,
        "head_sha": sha,
        "head_branch": "master",
        "head_repository": {"full_name": head},
        "conclusion": "success",
        "status": "completed",
        "event": "push",
        "html_url": f"https://github.com/{head}/actions/runs/99",
    }


class _GitHub:
    def __init__(self, routes: dict):
        self.routes = routes
        self.urls: list[str] = []
        self.calls: list[dict] = []
        self.token = ""

    def get_json(self, url: str, search_retry: bool = True):
        self.urls.append(url)
        payload, status = self.routes[url]
        self.calls.append({"url": url, "status": str(status)})
        return payload, status


def _routes(run: dict, compare_shas: list[str], fork_workflow: str, upstream_workflow: str) -> dict:
    base = "https://api.github.com"
    compare = {
        "status": "ahead" if compare_shas else "identical",
        "ahead_by": len(compare_shas),
        "behind_by": 0,
        "total_commits": len(compare_shas),
        "html_url": f"https://github.com/{UPSTREAM}/compare/master...Fu93:master",
        "base_commit": {"sha": "base-sha"},
        "commits": [{"sha": sha} for sha in compare_shas],
    }
    return {
        f"{base}/repos/{FORK}": (_repo(FORK), 200),
        f"{base}/repos/{FORK}/actions/runs?per_page=30": ({"total_count": 1, "workflow_runs": [run]}, 200),
        f"{base}/repos/{UPSTREAM}": (_repo(UPSTREAM), 200),
        f"{base}/repos/{UPSTREAM}/compare/master...Fu93:master": (compare, 200),
        f"{base}/repos/{UPSTREAM}/issues/450": (
            {
                "number": 450,
                "title": "hygen --version treated as error",
                "body": "Error: please specify a generator.",
                "html_url": f"https://github.com/{UPSTREAM}/issues/450",
            },
            200,
        ),
        f"{base}/repos/{UPSTREAM}/issues?state=open&per_page=30": ([], 200),
        f"{base}/repos/{UPSTREAM}/contents/.github/workflows/ci.yml?ref=master": (_file(upstream_workflow), 200),
        f"{base}/repos/{UPSTREAM}/contents/package.json?ref=master": (
            _file('{"name":"hygen","version":"6.2.11"}'),
            200,
        ),
        f"{base}/repos/{run['head_repository']['full_name']}/contents/{run['path']}?ref={run['head_sha']}": (
            _file(fork_workflow),
            200,
        ),
    }


def _revive(attempts: list[dict]) -> dict:
    return next(row for row in attempts if row["stage"] == "revive")


def test_attempt_workflow_is_the_fork_file_at_the_run_sha():
    run = _run(name="Build", path=".github/workflows/build.yml", sha="abc123", head=FORK)
    upstream = "name: CI\njobs:\n  build:\n    steps:\n      - run: yarn test\n"
    fork = "name: Build\njobs:\n  build:\n    steps:\n      - run: yarn test\n"
    client = _GitHub(_routes(run, ["abc123"], fork, upstream))
    attempts, _sources, _provenance = collect_attempt(
        client, fork=FORK, upstream=UPSTREAM, issue_number=450, collected_at=COLLECTED
    )
    revive = _revive(attempts)
    assert revive["status"] == "success"
    assert revive["commit_sha"] == "abc123"
    assert revive["ci_head_repo"] == FORK
    assert revive["workflow_path"] == ".github/workflows/build.yml"
    assert "yarn test" in revive["workflow_steps"]
    assert revive["workflow_runs_build"] == "true"
    assert revive["compare_shas"] == "abc123"
    fetched = f"https://api.github.com/repos/{FORK}/contents/.github/workflows/build.yml?ref=abc123"
    assert fetched in client.urls
    assert "https://api.github.com/repos/jondot/hygen/contents/.github/workflows/build.yml" not in " ".join(client.urls)


def test_echo_only_workflow_is_not_a_build():
    run = _run(name="CI", path=".github/workflows/ci.yml", sha="abc123", head=FORK)
    upstream = "name: CI\njobs:\n  build:\n    steps:\n      - run: yarn test\n"
    fork = "name: CI\njobs:\n  ci:\n    steps:\n      - run: echo hello\n"
    client = _GitHub(_routes(run, ["abc123"], fork, upstream))
    attempts, _sources, _provenance = collect_attempt(
        client, fork=FORK, upstream=UPSTREAM, issue_number=450, collected_at=COLLECTED
    )
    revive = _revive(attempts)
    assert revive["status"] == "not_attempted"
    assert revive["commit_sha"] == ""
    assert revive["workflow_runs_build"] == "false"
    assert "echo hello" in revive["workflow_steps"]
    assert "yarn test" not in revive["workflow_steps"]
    assert revive["ci_head_repo"] == FORK
    assert f"https://api.github.com/repos/{FORK}/contents/.github/workflows/ci.yml?ref=abc123" in client.urls


def test_a_run_outside_the_compare_list_is_not_accepted():
    run = _run(name="CI", path=".github/workflows/ci.yml", sha="deadbeef", head=FORK)
    client = _GitHub(_routes(run, ["abc123"], "name: CI\n- run: yarn test\n", "name: CI\n- run: yarn test\n"))
    attempts, _sources, _provenance = collect_attempt(
        client, fork=FORK, upstream=UPSTREAM, issue_number=450, collected_at=COLLECTED
    )
    revive = _revive(attempts)
    assert revive["status"] == "not_attempted"
    assert revive["commit_sha"] == ""
    assert revive["compare_shas"] == "abc123"
    assert all("deadbeef" not in url for url in client.urls)
    assert revive["workflow_path"] == ""
    assert revive["workflow_steps"] == ""

"""Collect a small public-GitHub snapshot for PhoenixRepo.

Uses the public REST API. GITHUB_TOKEN is optional and is never written to disk.
Missing API fields stay empty. Curated columns are filled only by the rules in
COLUMN_RULES, which are copied into data/real/provenance.json.

Run from the repo root:

  python scripts/curate_real_repos.py
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.sql_catalog import PROFILE_COLUMNS, SIGNAL_COLUMNS  # noqa: E402

USER_AGENT = "phoenixrepo-curation"
# Chosen so the snapshot can show each verdict for a stated reason.
# golang/dep: README names Go modules. jxnblk/rgx: no SPDX id.
# discreteoptimization/setcover: no test path. jondot/hygen: scheduled CI on master failed.
# 42wim/matterbridge: permissive, stale, not archived, latest Actions run succeeded, no successor URL.
DEFAULT_REPOS = (
    "golang/dep",
    "jxnblk/rgx",
    "discreteoptimization/setcover",
    "jondot/hygen",
    "42wim/matterbridge",
)

API_COLUMNS = (
    "repo_id",
    "full_name",
    "description",
    "licence_spdx",
    "primary_language",
    "stars",
    "forks",
    "archived",
    "open_issues",
    "html_url",
)
CURATED_COLUMNS = (
    "days_since_push",
    "has_readme",
    "has_license_file",
    "has_tests",
    "has_ci",
    "ci_status",
    "similar_repo_count",
    "unique_capability",
    "successor_url",
    "category",
    "data_origin",
)
COLUMN_RULES = {
    "days_since_push": "Whole UTC days between the repository pushed_at timestamp and collected_at. Empty if pushed_at is missing.",
    "has_readme": "true when the git tree (or the root contents listing, if the tree call failed or was truncated) has a root file named README, README.md, README.rst, or README.txt. false when that listing succeeded without one. Empty when both calls failed.",
    "has_license_file": "true when the repo payload license object is non-null or the root listing contains a LICENSE* file. false when both checks succeeded and neither matched. Empty if the repo payload has no license key and the root listing is unavailable.",
    "has_tests": "true when the git tree (or, if that call fails, the root listing) contains a path segment test, tests, spec, specs, or __tests__, or a filename ending in _test.go, _test.py, _spec.rb, .spec.js, .spec.ts, .test.js, or .test.ts. false when the tree or listing succeeded and none matched. Empty when both calls failed. A truncated tree is noted in provenance and only the returned paths are used.",
    "has_ci": "true when the git tree contains a file under .github/workflows/, or (only if the tree call failed or was truncated) GET /contents/.github/workflows returns a non-empty listing, or the Actions runs API returns at least one workflow run. false when no workflow file was found and the runs list is empty. Empty if the deciding calls failed. A .github directory that is not workflows does not count.",
    "ci_status": "Lowercased conclusion of the latest eligible build or test workflow run. Security and analysis scans (CodeQL and similar), runs awaiting approval (action_required), and fork pull requests are not eligible and are never recorded as success. Empty when no eligible run exists, the conclusion is null, or the call failed. Badge images in the README are not copied into this column.",
    "similar_repo_count": "GitHub search total_count for a close-substitute query: up to three distinctive words from the description, same language, searched in name and description. If the description has no usable words, language:<language> <repo name> in:name. Empty when that query cannot be built, the search call failed, or the total was 0. A query built from the repository's own name or description finds at least that repository when search is working, so a zero total is not stored as a census of no substitutes. This is not a topic-size count and it is not a verdict.",
    "unique_capability": "The GitHub description copied verbatim. Empty when description is null or blank. This is not an independent uniqueness judgement.",
    "successor_url": "The first http(s) URL within 400 characters after the phrases 'in favor of', 'replaced by', 'successor', or 'moved to' in the README, skipping badge hosts. Empty when the README has no such phrase or the README call failed. The homepage is not used: it is usually the project's own site.",
    "category": "The first GitHub topic, otherwise primary_language, otherwise empty.",
    "data_origin": "github_rest_api plus the collected_at date. Not synthetic_labelled.",
}
TEST_SEGMENTS = {"test", "tests", "spec", "specs", "__tests__"}
TEST_SUFFIXES = (
    "_test.go",
    "_test.py",
    "_spec.rb",
    ".spec.js",
    ".spec.ts",
    ".test.js",
    ".test.ts",
)
README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bool_text(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def days_between(pushed_at: str, collected_at: datetime) -> str:
    if not pushed_at:
        return ""
    pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    delta = collected_at - pushed.astimezone(timezone.utc)
    return str(max(0, delta.days))


def path_shows_tests(path: str) -> bool:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if any(part.lower() in TEST_SEGMENTS for part in parts):
        return True
    if not parts:
        return False
    name = parts[-1].lower()
    return any(name.endswith(suffix) for suffix in TEST_SUFFIXES)


def root_names(paths: list[str]) -> list[str]:
    return [path for path in paths if "/" not in path.replace("\\", "/")]


def workflows_in_paths(paths: list[str]) -> bool:
    prefix = ".github/workflows/"
    return any(path.replace("\\", "/").lower().startswith(prefix) and len(path) > len(prefix) for path in paths)


def listing_names(payload: object) -> list[str] | None:
    if not isinstance(payload, list):
        return None
    names = []
    for item in payload:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def tree_paths(payload: object) -> tuple[list[str] | None, bool]:
    if not isinstance(payload, dict):
        return None, False
    tree = payload.get("tree")
    if not isinstance(tree, list):
        return None, False
    paths = [str(item.get("path")) for item in tree if isinstance(item, dict) and item.get("path")]
    return paths, bool(payload.get("truncated"))


_SCAN_WORKFLOW = re.compile(
    r"codeql|scorecard|dependency-review|dependabot|semgrep|snyk|trivy|grype|checkov|sonar|ossf|security[-_ ]?scan|code[-_ ]?scanning",
    re.IGNORECASE,
)
_BUILD_WORKFLOW = re.compile(
    r"(?:^|[^a-z0-9])(?:ci|test|tests|build|development|check|checks|coverage|unittest|integration)(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
_SETUP_STEP = re.compile(r"(?i)(?:^use\s|setup-|set up |toolchain|actions/setup|install\s)")
_REAL_CONCLUSIONS = {"success", "failure", "cancelled", "timed_out", "startup_failure"}


def latest_conclusion(payload: object) -> tuple[str | None, bool | None]:
    """Return (conclusion or '', has_run). None has_run means the payload was unusable."""

    conclusion, has_run, _facts = latest_run(payload)
    return conclusion, has_run


def workflow_role(name: str, path: str) -> str:
    """Classify a workflow as a security scan, a build/test workflow, or neither."""

    blob = f"{name} {path}"
    if _SCAN_WORKFLOW.search(blob):
        return "scan"
    if _BUILD_WORKFLOW.search(blob):
        return "build"
    return "other"


def _run_blocks(run: dict, repo_full_name: str) -> list[str]:
    """Reasons a run cannot prove the default branch build state."""

    reasons: list[str] = []
    conclusion = _text(run.get("conclusion")).lower()
    status = _text(run.get("status")).lower()
    if conclusion == "action_required" or status in {"action_required", "waiting", "pending", "requested"}:
        reasons.append("action_required")
    event = _text(run.get("event")).lower()
    head = run.get("head_repository") if isinstance(run.get("head_repository"), dict) else {}
    head_name = _text(head.get("full_name")) if isinstance(head, dict) else ""
    if event in {"pull_request", "pull_request_target"} and head_name and head_name.lower() != repo_full_name.lower():
        reasons.append("fork_pr")
    return reasons


def _run_facts(run: dict) -> dict[str, str]:
    head = run.get("head_repository") if isinstance(run.get("head_repository"), dict) else {}
    return {
        "id": _text(run.get("id")),
        "name": _text(run.get("name")),
        "event": _text(run.get("event")),
        "path": _text(run.get("path")),
        "html_url": _text(run.get("html_url")),
        "head_repo": _text(head.get("full_name")) if isinstance(head, dict) else "",
        "conclusion": _text(run.get("conclusion")).lower(),
    }


def select_build_ci(payload: object, repo_full_name: str) -> tuple[str | None, bool | None, dict[str, str]]:
    """Pick CI evidence from a build or test workflow on this repository.

    Returns (conclusion or '', whether any Actions run exists, citation facts).
    conclusion None means the payload was unusable. An empty conclusion means
    CI is unknown: scans, action_required runs, and fork pull requests do not
    count as success or as a default-branch failure.
    """

    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return None, None, {}
    runs = [run for run in payload["workflow_runs"] if isinstance(run, dict)]
    if not runs:
        return "", False, {}
    eligible: dict | None = None
    latest_build: dict | None = None
    latest_blocks: list[str] = []
    scan_success = ""
    for run in runs:
        role = workflow_role(_text(run.get("name")), _text(run.get("path")))
        if role == "scan":
            if not scan_success and _text(run.get("conclusion")).lower() == "success":
                scan_success = _text(run.get("name")) or _text(run.get("path")) or "security scan"
            continue
        if role != "build":
            continue
        blocks = _run_blocks(run, repo_full_name)
        if latest_build is None:
            latest_build = run
            latest_blocks = blocks
        conclusion = _text(run.get("conclusion")).lower()
        status = _text(run.get("status")).lower()
        finished = conclusion in _REAL_CONCLUSIONS and (not status or status == "completed")
        if not blocks and finished and eligible is None:
            eligible = run
    facts: dict[str, str] = {}
    if eligible is not None:
        facts = _run_facts(eligible)
        facts["excluded"] = ""
        facts["unknown_note"] = ""
        facts["scan_ignored"] = ""
        return facts["conclusion"], True, facts
    if latest_build is not None:
        facts = _run_facts(latest_build)
        facts["excluded"] = ",".join(latest_blocks)
    else:
        facts = {"id": "", "name": "", "event": "", "path": "", "html_url": "", "head_repo": "", "conclusion": "", "excluded": ""}
    note_parts: list[str] = []
    if latest_build is not None:
        cited = facts
        note_parts.append(
            f"Latest build/test workflow {cited['name'] or 'unnamed'} event {cited['event'] or 'unrecorded'} "
            f"conclusion {cited['conclusion'] or 'empty'} head {cited['head_repo'] or 'unrecorded'}."
        )
        if "action_required" in latest_blocks:
            note_parts.append("That run is awaiting approval (action_required), so it is not CI success.")
        if "fork_pr" in latest_blocks:
            note_parts.append(
                "That run is a fork pull request, so it is not the default branch build state and is not CI success."
            )
    if scan_success:
        note_parts.append(
            f"A security or analysis scan ({scan_success}) concluded success and was not counted as CI."
        )
        facts["scan_ignored"] = scan_success
    else:
        facts["scan_ignored"] = ""
    facts["unknown_note"] = " ".join(note_parts)
    return "", True, facts


def failing_setup_step(payload: object) -> str:
    """Return the failed toolchain-setup step name, or empty when tests failed instead."""

    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        return ""
    jobs = [job for job in payload["jobs"] if isinstance(job, dict)]
    failed_jobs = [job for job in jobs if _text(job.get("conclusion")).lower() == "failure"]
    for job in failed_jobs or jobs:
        for step in job.get("steps") or []:
            if not isinstance(step, dict) or _text(step.get("conclusion")).lower() != "failure":
                continue
            name = _text(step.get("name"))
            if name and _SETUP_STEP.search(name):
                return name
    return ""


def latest_run(payload: object) -> tuple[str | None, bool | None, dict[str, str]]:
    """Return conclusion, whether a run exists, and name/event/html_url when present."""

    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return None, None, {}
    runs = payload["workflow_runs"]
    if not runs:
        return "", False, {}
    first = runs[0] if isinstance(runs[0], dict) else {}
    facts = {
        "name": _text(first.get("name")),
        "event": _text(first.get("event")),
        "html_url": _text(first.get("html_url")),
    }
    return _text(first.get("conclusion")).lower(), True, facts


_QUERY_STOP = {
    "a", "an", "the", "and", "or", "for", "with", "from", "that", "this", "into", "your",
    "using", "based", "official", "its", "are", "was", "not",
}


def close_substitute_query(name: str, description: str, language: str) -> str:
    """A name/description search. Topic totals are not used."""

    words: list[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", description or ""):
        if raw.lower() in _QUERY_STOP:
            continue
        words.append(raw)
        if len(words) == 3:
            break
    if language and words:
        return f"language:{language} {' '.join(words)} in:name,description"
    if language and name:
        return f"language:{language} {name} in:name"
    if name:
        return f"{name} in:name"
    return ""


def similar_query(name: str, language: str, topics: list[str]) -> str:
    """Kept for older callers. Topics are ignored on purpose."""

    del topics
    return close_substitute_query(name, "", language)


_SUCCESSOR = re.compile(
    r"(?is)(?:in favor of|replaced by|successor[:\s]|moved to).{0,400}?(https?://[^\s)>\"]+)"
)
_BAD_SUCCESSOR = ("travis-ci", "shields.io", "appveyor", "goreportcard", ".svg", "badge")


def successor_from_readme(text: str) -> tuple[str, str]:
    """Return (url, matched snippet). Empty when the README does not name a URL."""

    if not text:
        return "", ""
    for match in _SUCCESSOR.finditer(text):
        url = match.group(1).rstrip(".,;")
        if any(token in url.lower() for token in _BAD_SUCCESSOR):
            continue
        snippet = " ".join(match.group(0).split())
        return url, snippet[:300]
    return "", ""


def build_profile(
    repo: dict,
    *,
    contents: list[str] | None,
    tree: list[str] | None,
    tree_truncated: bool,
    actions: dict | None,
    workflows_present: bool | None,
    topics: list[str],
    similar_count: str,
    readme: str,
    collected_at: datetime,
) -> dict[str, str]:
    full_name = _text(repo.get("full_name"))
    names = [name.lower() for name in contents] if contents is not None else None
    licence = repo.get("license") if isinstance(repo.get("license"), dict) else None
    spdx = _text(licence.get("spdx_id")) if licence else ""
    if spdx.upper() == "NOASSERTION":
        spdx = _text(licence.get("spdx_id"))
    has_readme = None if names is None else any(name in README_NAMES for name in names)
    license_file = None
    if names is not None:
        license_file = any(name.startswith("license") for name in names)
    if licence:
        has_license = True
    elif license_file is not None:
        has_license = license_file
    else:
        has_license = None
    has_tests = None
    paths = tree if tree is not None else contents
    if paths is not None:
        has_tests = any(path_shows_tests(path) for path in paths)
    conclusion, has_run, run_facts = (
        select_build_ci(actions, full_name) if actions is not None else (None, None, {})
    )
    if has_run is True or workflows_present is True:
        has_ci = True
    elif has_run is False and workflows_present is False:
        has_ci = False
    else:
        has_ci = None
    description = _text(repo.get("description"))
    successor, successor_snippet = successor_from_readme(readme)
    language = _text(repo.get("language"))
    category = topics[0] if topics else language
    row = {
        "repo_id": full_name,
        "full_name": full_name,
        "description": description,
        "licence_spdx": "" if spdx.upper() == "NONE" else spdx,
        "primary_language": language,
        "stars": "" if repo.get("stargazers_count") is None else str(repo.get("stargazers_count")),
        "forks": "" if repo.get("forks_count") is None else str(repo.get("forks_count")),
        "archived": "" if repo.get("archived") is None else _bool_text(bool(repo.get("archived"))),
        "days_since_push": days_between(_text(repo.get("pushed_at")), collected_at),
        "open_issues": "" if repo.get("open_issues_count") is None else str(repo.get("open_issues_count")),
        "has_readme": _bool_text(has_readme),
        "has_license_file": _bool_text(has_license),
        "has_tests": _bool_text(has_tests),
        "has_ci": _bool_text(has_ci),
        "ci_status": "" if conclusion is None else conclusion,
        "similar_repo_count": similar_count,
        "unique_capability": description,
        "successor_url": successor,
        "category": category,
        "html_url": _text(repo.get("html_url")),
        "data_origin": f"github_rest_api:{collected_at.date().isoformat()}",
    }
    row["_tree_truncated"] = "true" if tree_truncated else "false"
    row["_successor_snippet"] = successor_snippet
    row["_ci_run_id"] = run_facts.get("id", "")
    row["_ci_run_name"] = run_facts.get("name", "")
    row["_ci_run_event"] = run_facts.get("event", "")
    row["_ci_run_url"] = run_facts.get("html_url", "")
    row["_ci_run_path"] = run_facts.get("path", "")
    row["_ci_head_repo"] = run_facts.get("head_repo", "")
    row["_ci_excluded"] = run_facts.get("excluded", "")
    row["_ci_unknown_note"] = run_facts.get("unknown_note", "")
    row["_ci_scan_ignored"] = run_facts.get("scan_ignored", "")
    row["_ci_failing_step"] = ""
    return row


def build_signals(row: dict[str, str]) -> list[dict[str, str]]:
    repo_id = row["repo_id"]
    signals: list[dict[str, str]] = []

    def add(signal: str, detail: str, supports: bool) -> None:
        signals.append(
            {
                "repo_id": repo_id,
                "signal": signal,
                "detail": detail,
                "supports_revival": "true" if supports else "false",
            }
        )

    if row["has_tests"] == "true":
        add("tests_present", "A test directory or test filename was present in the Git tree or root listing.", True)
    elif row["has_tests"] == "false":
        add("tests_absent", "The Git tree or root listing succeeded and contained no test directory or test filename.", False)
    run_name = row.get("_ci_run_name", "")
    run_event = row.get("_ci_run_event", "")
    run_url = row.get("_ci_run_url", "")
    cited = f" Workflow {run_name or 'unnamed'} event {run_event or 'unrecorded'}."
    if run_url:
        cited += f" Run {run_url}."
    if row["ci_status"] == "success":
        add("ci_success", f"Latest build or test workflow conclusion was success.{cited}", True)
    elif row["ci_status"] in {"failure", "cancelled", "timed_out", "startup_failure"}:
        step = row.get("_ci_failing_step", "")
        if step:
            add(
                "ci_failure",
                (
                    f"The CI toolchain setup step ({step}) failed, so CI has decayed. "
                    f"This is not a test failure.{cited}"
                ),
                False,
            )
        else:
            add("ci_failure", f"Latest build or test workflow conclusion was {row['ci_status']}.{cited}", False)
    else:
        note = row.get("_ci_unknown_note", "")
        detail = "No eligible build or test workflow conclusion was recorded."
        if note:
            detail += " " + note
        detail += (
            " Security and analysis scans, runs awaiting approval (action_required), and fork pull requests "
            "are not CI success. Missing build/test CI is unknown, not a failure, and it does not by itself block revival."
        )
        add("ci_unknown", detail, False)
    if not row["licence_spdx"]:
        add("licence_missing", "The repository payload had no SPDX licence id.", False)
    if row["archived"] == "true":
        add(
            "archived",
            "GitHub reports archived=true. This records that the owner froze the repository. "
            "Archive status is not a revival vote: the policy does not treat it as a pass or a block.",
            False,
        )
    if row["successor_url"]:
        add("successor_recorded", f"README names a successor URL: {row['successor_url']}.", False)
    if row["similar_repo_count"].isdigit():
        count = int(row["similar_repo_count"])
        if count <= 5:
            band = "low"
        elif count <= 30:
            band = "moderate"
        else:
            band = "high"
        add(
            "alternatives_factor",
            (
                f"Close-substitute search total_count was {count} ({band}). "
                "Uncertainty: this is a name or description search total, not a census of substitutes, "
                "and it does not by itself decide the verdict."
            ),
            band == "low",
        )
    elif not row["similar_repo_count"]:
        add(
            "alternatives_factor",
            "Close-substitute search total was not recorded. The missing count is unknown, not evidence that alternatives do or do not exist.",
            False,
        )
    if not signals:
        add("no_extra_signal", "No rule-based revival signal fired. The profile row is the evidence.", True)
    return signals


class GitHubClient:
    def __init__(self, token: str = "", sleep_seconds: float = 0.4) -> None:
        self.token = token
        self.sleep_seconds = sleep_seconds
        self.calls: list[dict[str, str]] = []

    def get_json(self, url: str, *, search_retry: bool = True) -> tuple[object | None, int]:
        if "/search/" in url:
            time.sleep(7)
        request = urllib.request.Request(url, headers=self._headers())
        status = 0
        remaining = ""
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                status = response.status
                remaining = response.headers.get("X-RateLimit-Remaining", "")
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            remaining = exc.headers.get("X-RateLimit-Remaining", "") if exc.headers else ""
            payload = None
        self.calls.append({"url": url.split("?")[0], "status": str(status), "remaining": remaining})
        time.sleep(self.sleep_seconds)
        if search_retry and "/search/" in url and status in {403, 429}:
            time.sleep(8)
            return self.get_json(url, search_retry=False)
        return payload, status

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def _topics_from_repo(repo: dict) -> tuple[list[str], str]:
    raw = repo.get("topics")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()], "repository.topics"
    return [], "missing"


def collect_repo(client: GitHubClient, full_name: str, collected_at: datetime) -> tuple[dict[str, str], dict, list[dict[str, str]]]:
    owner, name = full_name.split("/", 1)
    base = f"https://api.github.com/repos/{owner}/{name}"
    repo, status = client.get_json(base)
    if status != 200 or not isinstance(repo, dict):
        raise RuntimeError(f"{full_name} repository call returned HTTP {status}")
    branch = _text(repo.get("default_branch")) or "HEAD"
    tree_payload, tree_status = client.get_json(base + f"/git/trees/{urllib.parse.quote(branch)}?recursive=1")
    tree, truncated = tree_paths(tree_payload) if tree_status == 200 else (None, False)
    contents: list[str] | None = None
    contents_status = ""
    workflows_present: bool | None = None
    workflows_status = ""
    if tree is not None and not truncated:
        contents = root_names(tree)
        contents_status = "from-tree"
        workflows_present = workflows_in_paths(tree)
        workflows_status = "from-tree"
    else:
        contents_payload, contents_code = client.get_json(base + "/contents/")
        contents_status = str(contents_code)
        contents = listing_names(contents_payload) if contents_code == 200 else None
        workflows_payload, workflows_code = client.get_json(base + "/contents/.github/workflows")
        workflows_status = str(workflows_code)
        if workflows_code == 200:
            workflows_present = bool(listing_names(workflows_payload))
        elif workflows_code == 404:
            workflows_present = False
    actions_payload, actions_status = client.get_json(base + "/actions/runs?per_page=30")
    actions = actions_payload if actions_status == 200 else None
    readme_payload, readme_status = client.get_json(base + "/readme")
    readme = ""
    if (
        readme_status == 200
        and isinstance(readme_payload, dict)
        and readme_payload.get("encoding") == "base64"
        and isinstance(readme_payload.get("content"), str)
    ):
        readme = base64.b64decode(readme_payload["content"]).decode("utf-8", "replace")
    topics, topics_status = _topics_from_repo(repo)
    query = close_substitute_query(name, _text(repo.get("description")), _text(repo.get("language")))
    similar = ""
    search_status = ""
    if query:
        search_url = "https://api.github.com/search/repositories?q=" + urllib.parse.quote(query) + "&per_page=1"
        search_payload, search_code = client.get_json(search_url)
        if (
            search_code == 200
            and isinstance(search_payload, dict)
            and search_payload.get("total_count") == 0
        ):
            # A query built from this repo's own description should find at least that
            # repo. A zero total is retried once. If it is still zero, it is not stored.
            search_payload, search_code = client.get_json(search_url)
        search_status = str(search_code)
        if search_code == 200 and isinstance(search_payload, dict):
            total = search_payload.get("total_count")
            if isinstance(total, int) and total > 0:
                similar = str(total)
            elif total == 0:
                search_status = f"{search_status}-zero-not-stored"
    row = build_profile(
        repo,
        contents=contents,
        tree=tree,
        tree_truncated=truncated,
        actions=actions if isinstance(actions, dict) else None,
        workflows_present=workflows_present,
        topics=topics,
        readme=readme,
        similar_count=similar,
        collected_at=collected_at,
    )
    jobs_status = ""
    if row["ci_status"] == "failure" and row.get("_ci_run_id"):
        jobs_payload, jobs_code = client.get_json(base + f"/actions/runs/{row['_ci_run_id']}/jobs?per_page=20")
        jobs_status = str(jobs_code)
        if jobs_code == 200:
            row["_ci_failing_step"] = failing_setup_step(jobs_payload)
    row_signals = build_signals(row)
    note = {
        "full_name": full_name,
        "similar_query": query,
        "similar_total_count": similar,
        "search_status": search_status,
        "contents_status": str(contents_status),
        "tree_status": str(tree_status),
        "tree_truncated": row.pop("_tree_truncated"),
        "actions_status": str(actions_status),
        "workflows_status": str(workflows_status),
        "readme_status": str(readme_status),
        "successor_snippet": row.pop("_successor_snippet"),
        "ci_run_id": row.pop("_ci_run_id"),
        "ci_run_name": row.pop("_ci_run_name"),
        "ci_run_event": row.pop("_ci_run_event"),
        "ci_run_url": row.pop("_ci_run_url"),
        "ci_run_path": row.pop("_ci_run_path"),
        "ci_head_repo": row.pop("_ci_head_repo"),
        "ci_excluded": row.pop("_ci_excluded"),
        "ci_unknown_note": row.pop("_ci_unknown_note"),
        "ci_scan_ignored": row.pop("_ci_scan_ignored"),
        "ci_failing_step": row.pop("_ci_failing_step"),
        "jobs_status": jobs_status,
        "topics_status": str(topics_status),
        "topics": topics,
        "homepage": _text(repo.get("homepage")),
        "pushed_at": _text(repo.get("pushed_at")),
    }
    return row, note, row_signals


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def curate(repos: tuple[str, ...], output: Path, client: GitHubClient, collected_at: datetime) -> dict:
    profiles = []
    signals = []
    notes = []
    for full_name in repos:
        row, note, row_signals = collect_repo(client, full_name, collected_at)
        profiles.append(row)
        signals.extend(row_signals)
        notes.append(note)
    write_csv(output / "repo_evidence.csv", PROFILE_COLUMNS, profiles)
    write_csv(output / "revival_signals.csv", SIGNAL_COLUMNS, signals)
    provenance = {
        "collected_at": collected_at.isoformat(),
        "generator": "scripts/curate_real_repos.py",
        "token_used": bool(client.token),
        "token_written": False,
        "endpoints": [
            "GET /repos/{owner}/{repo}",
            "GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
            "GET /repos/{owner}/{repo}/actions/runs?per_page=30",
            "GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=20 (only when the eligible build/test conclusion is failure)",
            "GET /repos/{owner}/{repo}/readme",
            "GET /search/repositories?q={similar_query}&per_page=1",
            "GET /repos/{owner}/{repo}/contents/ (only when the tree call fails or is truncated)",
            "GET /repos/{owner}/{repo}/contents/.github/workflows (only when the tree call fails or is truncated)",
        ],
        "api_columns": list(API_COLUMNS),
        "curated_columns": list(CURATED_COLUMNS),
        "column_rules": COLUMN_RULES,
        "collection_notes": [
            "successor_url is only a URL that the README places next to an explicit replacement phrase. A project homepage is not treated as a successor.",
            "similar_repo_count is a close name or description search total, not a topic-size count. The policy scores it with stated uncertainty and does not cut at 40.",
            "An empty ci_status is unknown. It is not recorded as a failure. Security and analysis scans, action_required runs, and fork pull requests are not CI success.",
            "archived=true is a fact about the owner's freeze. It is not a revival vote.",
        ],
        "repos": notes,
        "calls": client.calls,
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot public GitHub facts into data/real.")
    parser.add_argument("--output", default="data/real")
    parser.add_argument("--repo", action="append", dest="repos")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    collected_at = datetime.now(timezone.utc)
    repos = tuple(args.repos) if args.repos else DEFAULT_REPOS
    output = Path(args.output)
    provenance = curate(repos, output, GitHubClient(token), collected_at)
    print(f"wrote {len(repos)} repos to {output} at {provenance['collected_at']}")
    print("token was not written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

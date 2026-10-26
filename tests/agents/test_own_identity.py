"""A repository's own package, docs, fork, and mirror are not alternatives."""
import json
from pathlib import Path

import httpx

from app.agents.own_identity import build_identity, load_manifests, own_reason
from app.agents.relevance import (
    InfraFallbackStop,
    RelevanceClient,
    RelevanceLLMError,
    filter_relevant_alternatives,
    judge_discovery,
    search_prior_art,
)
from app.tools import Budget, GitHubTool, ToolBox, WebSearchTool
from scripts.rerun_unjudged import is_infra_review

CANDIDATE = {
    "repo_id": "phillipdupuis/pydantic-to-typescript",
    "html_url": "https://github.com/phillipdupuis/pydantic-to-typescript",
    "description": "Generate TypeScript interfaces from pydantic models.",
    "readme_excerpt": "Generate TypeScript interfaces from pydantic models.",
    "manifests": {
        "pyproject.toml": (
            '[project]\nname = "pydantic-to-typescript"\n\n'
            "[project.urls]\n"
            'Homepage = "https://phillipdupuis.github.io/pydantic-to-typescript"\n'
            'Documentation = "https://phillipdupuis.github.io/pydantic-to-typescript/docs"\n'
        ),
    },
}
OWN_PYPI = {
    "alternative_id": "web:https://pypi.org/project/pydantic-to-typescript/",
    "url": "https://pypi.org/project/pydantic-to-typescript/",
    "description": "Generate TypeScript interfaces from pydantic models.",
    "source": "web",
}
OTHER_PYDANTIC = {
    "alternative_id": "web:https://pypi.org/project/pydantic/",
    "url": "https://pypi.org/project/pydantic/",
    "description": "Data validation using Python type hints for the pydantic library.",
    "source": "web",
    "overlap": "full",
    "same_problem": True,
    "stars": 20000,
    "pushed_at": "2026-09-01T00:00:00Z",
    "archived": False,
    "licence_spdx": "MIT",
    "page_kind": "product",
    "excerpt": "Data validation using Python type hints for the pydantic library.",
}
SIMILAR = {
    "alternative_id": "web:https://pypi.org/project/pydantic-to-typescript-converter/",
    "url": "https://pypi.org/project/pydantic-to-typescript-converter/",
    "description": "A different converter that writes its own model layer.",
    "source": "web",
}
PROBLEM = {
    "problem": "Generate TypeScript interfaces from pydantic models.",
    "target_user": "Python developers",
    "category": "developer-tools",
}


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, **kwargs) -> None:
        self.events.append(kwargs)


def test_exact_pypi_page_is_own_identity_and_a_similar_name_is_kept():
    identity = build_identity(CANDIDATE)
    assert own_reason(identity, OWN_PYPI) == "own registry package pydantic-to-typescript"
    assert own_reason(identity, {
        "alternative_id": "web:https://phillipdupuis.github.io/pydantic-to-typescript/docs",
        "url": "https://phillipdupuis.github.io/pydantic-to-typescript/docs",
    })
    assert own_reason(identity, OTHER_PYDANTIC) is None
    assert own_reason(identity, SIMILAR) is None
    assert own_reason(identity, {
        "alternative_id": "phillipdupuis/another-library",
        "url": "https://github.com/phillipdupuis/another-library",
        "description": "An unrelated library by the same person.",
        "fork": False,
    }) is None
    mirror_readme = "Generate TypeScript interfaces from pydantic models. " + ("extra detail " * 8)
    long_candidate = dict(CANDIDATE)
    long_candidate["readme_excerpt"] = mirror_readme
    assert own_reason(build_identity(long_candidate), {
        "alternative_id": "mirror/pydantic-to-typescript",
        "url": "https://github.com/mirror/pydantic-to-typescript",
        "readme": mirror_readme,
        "fork": True,
        "parent": {"full_name": "phillipdupuis/pydantic-to-typescript"},
    })


def test_search_and_the_judge_drop_the_repo_package_before_and_after_the_model(tmp_path: Path):
    pages = [
        (OWN_PYPI["url"], "pydantic-to-typescript", OWN_PYPI["description"]),
        (OTHER_PYDANTIC["url"], "pydantic", OTHER_PYDANTIC["description"]),
        (SIMILAR["url"], "converter", SIMILAR["description"]),
        ("https://github.com/phillipdupuis/pydantic-to-typescript", "repo", "source repository"),
    ]

    def transport(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "mcp.exa.ai" in url:
            text = "".join(f"Title: {title}\nURL: {page}\nHighlights:\n{snippet}\n\n" for page, title, snippet in pages)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}})
        return httpx.Response(404)

    toolbox = ToolBox(
        tools={"web_search": WebSearchTool(transport=httpx.MockTransport(transport))},
        budget=Budget(max_calls=10, max_seconds=30.0),
    )
    found = search_prior_art(CANDIDATE, ["pydantic typescript"], toolbox)
    urls = {row["url"] for row in found}
    assert OWN_PYPI["url"] not in urls
    assert "https://github.com/phillipdupuis/pydantic-to-typescript" not in urls
    assert OTHER_PYDANTIC["url"] in urls
    assert SIMILAR["url"] in urls
    assert any("pydantic-to-typescript" in item["url"] and "own" in item["reason"] for item in found.own_filtered)

    prompts: list[str] = []

    def llm(prompt: str, _sys: str) -> str:
        prompts.append(prompt)
        if "CANDIDATE, ALREADY_EXISTS, or NO_GO" in prompt:
            if "pypi.org/project/pydantic/" in prompt and "pydantic-to-typescript" not in prompt.split("Prior Art")[-1]:
                return json.dumps({
                    "verdict": "ALREADY_EXISTS",
                    "reason": "pydantic already validates the models.",
                    "problem_quote": "Generate TypeScript interfaces from pydantic models.",
                    "alternative_repo": OTHER_PYDANTIC["url"],
                    "alternative_quote": "Data validation using Python type hints",
                })
            return json.dumps({
                "verdict": "ALREADY_EXISTS",
                "reason": "the PyPI page is this package.",
                "problem_quote": "Generate TypeScript interfaces from pydantic models.",
                "alternative_repo": OWN_PYPI["url"],
                "alternative_quote": "Generate TypeScript interfaces from pydantic models.",
            })
        if "[0]" in prompt and "[1]" in prompt:
            return json.dumps({"results": [
                {"index": 0, "same_problem": True, "overlap": "partial", "reason": "related", "quotes": ["Data validation using Python type hints"]},
                {"index": 1, "same_problem": True, "overlap": "partial", "reason": "related", "quotes": ["A different converter that writes its own model layer."]},
            ]})
        quote = "A different converter that writes its own model layer." if "converter" in prompt else "Data validation using Python type hints"
        return json.dumps({
            "same_problem": True,
            "overlap": "partial",
            "reason": "related",
            "quotes": [quote],
        })

    trace = _Trace()
    client = RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=llm)
    kept = filter_relevant_alternatives(
        PROBLEM,
        CANDIDATE["repo_id"],
        [OWN_PYPI, OTHER_PYDANTIC, SIMILAR],
        client=client,
        trace=trace,
        candidate=CANDIDATE,
    )
    assert all("project/pydantic-to-typescript/" not in str(row.get("url")) for row in kept)
    assert any("project/pydantic/" in str(row.get("url")) for row in kept)
    assert any("pydantic-to-typescript-converter" in str(row.get("url")) for row in kept)
    assert all("project/pydantic-to-typescript/" not in prompt for prompt in prompts)
    own_events = [event for event in trace.events if event.get("action") == "own_identity"]
    assert own_events
    blob = json.dumps(own_events)
    assert "web:https://pypi.org/project/pydantic-to-typescript/" in blob

    ignored = judge_discovery(
        CANDIDATE,
        [OWN_PYPI],
        [],
        client=RelevanceClient(cache_dir=tmp_path / "own", mock_handler=llm),
    )
    assert ignored[0] == "CANDIDATE"
    assert ignored[0] != "ALREADY_EXISTS"
    assert "ignored own identity" in ignored[1]

    blocked = judge_discovery(
        CANDIDATE,
        [OTHER_PYDANTIC],
        [],
        client=RelevanceClient(cache_dir=tmp_path / "other", mock_handler=llm),
    )
    assert blocked[0] == "ALREADY_EXISTS"


def test_manifest_file_fetch_reads_package_name(tmp_path: Path):
    import base64

    encoded = base64.b64encode(b'[project]\nname = "widget-core"\n').decode("ascii")

    def transport(request: httpx.Request) -> httpx.Response:
        if "/contents/pyproject.toml" not in str(request.url):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"content": encoded, "encoding": "base64"})

    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=httpx.MockTransport(transport))},
        budget=Budget(max_calls=40, max_seconds=30.0),
    )
    found = load_manifests(toolbox, "acme/widget")
    identity = build_identity({"repo_id": "acme/widget"}, found)
    assert "widget-core" in identity.packages
    assert own_reason(identity, {
        "alternative_id": "web:https://pypi.org/project/widget-core/",
        "url": "https://pypi.org/project/widget-core/",
    })
    assert own_reason(identity, {
        "alternative_id": "web:https://pypi.org/project/widget-core-pro/",
        "url": "https://pypi.org/project/widget-core-pro/",
    }) is None


def test_repeated_http_502_stops_the_per_alternative_fallback(tmp_path: Path):
    calls: list[str] = []

    def llm(prompt: str, _sys: str) -> str:
        calls.append(prompt[:40])
        raise RelevanceLLMError("Nightshift returned HTTP 502")

    rows = [
        {"alternative_id": f"other/tool-{i}", "url": f"https://github.com/other/tool-{i}", "description": f"Tool {i} does a different job.", "source": "github"}
        for i in range(6)
    ]
    client = RelevanceClient(cache_dir=tmp_path / "fall", mock_handler=llm)
    try:
        filter_relevant_alternatives(PROBLEM, "phillipdupuis/pydantic-to-typescript", rows, client=client, candidate=CANDIDATE)
    except InfraFallbackStop as exc:
        reason = str(exc)
    else:
        raise AssertionError("fallback kept calling after repeated HTTP 502")
    assert reason.startswith("infra:")
    assert "HTTP 502" in reason
    assert len(calls) < len(rows)
    assert is_infra_review({"verdict": "NEEDS_REVIEW", "reason": reason})

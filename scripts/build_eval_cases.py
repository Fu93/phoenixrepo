"""Freeze the labelled idea-filter eval set. No network.

Invoice and ytkb rows are copied from data/real. The other cases are the
excerpts collected on 2026-10-22. This script does not call GitHub.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.equivalence import text_hash  # noqa: E402
from app.agents.market_filter import days_between  # noqa: E402

COLLECTED = "2026-10-22T20:30:00+00:00"
ORIGIN = "github_rest_api:2026-10-22T20:30:00Z"
REAL = ROOT / "data" / "real"
OUT = ROOT / "data" / "eval" / "cases.json"


def main() -> int:
    payload = {
        "collected_at": COLLECTED,
        "token_used": False,
        "note": (
            "Labels are human judgements from the linked excerpts. "
            "They are not the phrase-method output. "
            "hygen stays out of data/real/discovery_candidates.csv. "
            "The revival verdict for jondot/hygen is unchanged."
        ),
        "cases": _cases(),
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} cases={len(payload['cases'])}")
    return 0


def _cases() -> list[dict]:
    invoice = _csv_case(
        "tharani-2006/Invoice-generator-application",
        "ALREADY_EXISTS",
        "https://github.com/al1abb/invoify is MIT and was pushed 2026-08-24. "
        "Its stored README says it is a free invoice generator and 'get a real PDF.' "
        "That covers the candidate's invoice and PDF capabilities. "
        "https://github.com/tharani-2006/Invoice-generator-application has 0 stars.",
    )
    ytkb = _csv_case(
        "SergioMendolia/ytkb",
        "CANDIDATE",
        "No stored alternative covers both 'knowledge base' and 'push changes back'. "
        "https://github.com/foliant-docs/foliant/pull/80 is open demand (2 comments, 2 reactions) "
        "and does not push edits back. Foliant itself was pushed 2026-03-29, outside 180 days.",
    )
    return [invoice, ytkb, *_new_cases()]


def _csv_case(repo_id: str, label: str, justification: str) -> dict:
    candidate = _one("discovery_candidates.csv", repo_id)
    return {
        "repo_id": repo_id,
        "label": label,
        "justification": justification,
        "links": [candidate["html_url"]],
        "candidate": candidate,
        "prior_art": _many("prior_art.csv", repo_id),
        "demand": _many("demand_signals.csv", repo_id),
        "evidence": _evidence_from_csv(candidate, _many("prior_art.csv", repo_id)),
    }


def _new_cases() -> list[dict]:
    return [
        _hygen(),
        _wecook(),
        _palette(),
        _splitfree(),
        _vocapop(),
        _provenance(),
        _regex(),
        _thomas(),
        _task_board(),
        _feedly(),
    ]


def _hygen() -> dict:
    readme = (
        "`hygen` is the simple, fast, and scalable code generator that lives _in_ your project.\n"
        "- ✅ Injecting into existing files\n"
        "Hygen can be used to supercharge your workflow with Redux, React Native, Express."
    )
    plop = (
        "Plop is what I like to call a \"micro-generator framework.\" Now, I call it that because it is a small tool "
        "that gives you a simple way to generate code or any other type of flat text files in a consistent way. "
        "Create a plopfile.js at the root of your project. "
        "The `modify` action can be used two ways. You can use a `pattern` property to find/replace text in the file "
        "located at the `path` specified, or you can use a `transform` function to transform the file contents. "
        "The `append` action is a commonly used subset of `modify`. It is used to append data in a file at a particular location."
    )
    candidate = _candidate(
        "jondot/hygen",
        "https://github.com/jondot/hygen",
        "The simple, fast, and scalable code generator that lives in your project.",
        readme,
        "code generator that lives in your project",
        "supercharge your workflow",
        "Injecting into existing files",
        ["src/bin.ts", "src/engine.ts"],
        "2017-08-13T07:38:43Z",
        "2024-07-09T11:40:47Z",
        "5927",
        "MIT",
        ["code generator", "injecting into existing files"],
    )
    prior = [
        _prior(
            candidate,
            "plopjs/plop",
            "https://github.com/plopjs/plop",
            "MIT",
            "7686",
            "2026-10-16T10:34:37Z",
            plop,
            ["code generator", "injecting into existing files"],
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "ALREADY_EXISTS",
        "Human label, not the phrase method. plop (MIT, pushed 2026-10-16, https://github.com/plopjs/plop) "
        "is an in-project generator ('generate code', 'plopfile.js at the root of your project') and its modify "
        "and append actions edit a file at a path. The exact phrase 'injecting into existing files' is not in that excerpt. "
        "This case is not a discovery_candidates row. The revival verdict for hygen is unchanged. "
        "README sha256 8a08e1add4ca7826889c3ccaf61c0b41e088ef0442a70f0370fcb78141d8602a.",
        extra_evidence=[
            {
                "id": "readme:plopjs/plop",
                "url": "https://github.com/plopjs/plop",
                "text": plop,
                "full_sha256": "8284e5541eacf1296aaa5910ed2b1fa6c581c2570604f6f3f9408e3d4fdb0abe",
            }
        ],
    )


def _wecook() -> dict:
    readme = (
        "A self-hosted recipe manager for organizing and sharing your favorite recipes.\n"
        "There already exists many self-hosted recipe managers. However, I thought their UIs looked dated and I was bored so I built my own!\n"
        "Create, edit, and organize recipes with ingredients, steps, and images"
    )
    mealie = (
        "Mealie is a self hosted recipe manager, meal planner and shopping list with a RestAPI backend and a reactive "
        "frontend built in Vue for a pleasant user experience for the whole family. Easily add recipes into your database "
        "by providing the URL and Mealie will automatically import the relevant data, or add a family recipe with the UI editor."
    )
    candidate = _candidate(
        "matt-dz/wecook",
        "https://github.com/matt-dz/wecook",
        "Self-hosted recipe manager",
        readme,
        "A self-hosted recipe manager for organizing and sharing your favorite recipes.",
        "your favorite recipes",
        "Create, edit, and organize recipes with ingredients, steps, and images",
        ["backend/cmd/wecook/main.go", "backend/internal/api/api.go"],
        "2025-11-27T05:57:22Z",
        "2025-12-31T19:48:01Z",
        "0",
        "MIT",
        ["self-hosted", "recipe manager"],
    )
    prior = [
        _prior(
            candidate,
            "mealie-recipes/mealie",
            "https://github.com/mealie-recipes/mealie",
            "AGPL-3.0",
            "13656",
            "2026-10-22T19:23:10Z",
            mealie,
            ["self-hosted", "recipe manager"],
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "ALREADY_EXISTS",
        "https://github.com/mealie-recipes/mealie is AGPL-3.0 and was pushed 2026-10-22. "
        "The stored sentence is 'Mealie is a self hosted recipe manager' and 'add a family recipe with the UI editor.' "
        "The candidate README says alternatives already exist and the difference is a dated UI. "
        "The hyphenated phrase 'self-hosted' is not in the Mealie sentence. "
        "https://github.com/matt-dz/wecook has 0 stars. README sha256 0bd50fd4873e414718de6329ffaa8646b4b5900baf4197c330ec67114d3103d3.",
    )


def _palette() -> dict:
    readme = (
        "A visual tool that generates harmonious color palettes from base colors with live preview and export options. "
        "Helps designers and developers quickly create cohesive color schemes without deep color theory knowledge.\n"
        "Generate complementary, analogous, triadic, and split-complementary palettes\n"
        "Export as TailwindCSS config, CSS variables, or JSON format"
    )
    royal = (
        "Color Palette Pro generates 6 different color palettes (Analogous, Complementary, Split Complementary, Triadic, "
        "Tetradic, and Tints & Shades) in 4 styles across 8 color spaces and formats. "
        "Export options: CSS variables, CSS file download, and palette images"
    )
    candidate = _candidate(
        "bytesbeard/color-palette-generator",
        "https://github.com/bytesbeard/color-palette-generator",
        "A visual tool that generates harmonious color palettes from base colors with live preview and export options",
        readme,
        "generates harmonious color palettes",
        "designers and developers",
        "Export as TailwindCSS config, CSS variables, or JSON format",
        ["src/generator.ts", "src/exporter.ts"],
        "2025-12-30T20:05:01Z",
        "2025-12-30T20:05:05Z",
        "0",
        "",
        ["color palette", "triadic"],
    )
    prior = [
        _prior(
            candidate,
            "royalfig/color-palette-generator",
            "https://github.com/royalfig/color-palette-generator",
            "MIT",
            "150",
            "2026-10-21T11:51:25Z",
            royal,
            ["color palette", "triadic"],
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "NO_GO",
        "https://github.com/royalfig/color-palette-generator is MIT, pushed 2026-10-21, and lists Triadic palettes plus "
        "'Export options: CSS variables, CSS file download, and palette images'. It does not mention TailwindCSS. "
        "The candidate's cited export line requires a TailwindCSS config. That capability is a gap, so this is not "
        "ALREADY_EXISTS. No demand row with comments or reactions was recorded. "
        "https://github.com/bytesbeard/color-palette-generator has 0 stars. "
        "The search '\"color palette\" generator license:mit stars:>50 pushed:>2026-04-22' returned total_count 3. That total is not a pass.",
    )


def _splitfree() -> dict:
    candidate = _candidate(
        "mbhogal/splitfree",
        "https://github.com/mbhogal/splitfree",
        "Free expense splitter for friends/roommates",
        "",
        "expense splitter",
        "friends/roommates",
        "Free expense splitter",
        ["pages/3_➕_Add_Expense.py", "pages/5_💰_Settle_Up.py"],
        "2025-12-31T04:33:25Z",
        "2025-12-31T23:58:40Z",
        "0",
        "",
        ["expense", "friends/roommates"],
    )
    prior = [
        _prior(
            candidate,
            "spiral-project/ihatemoney",
            "https://github.com/spiral-project/ihatemoney",
            "",
            "1395",
            "2026-08-07T14:10:37Z",
            "I hate money is a web application made to ease shared budget management. It keeps track of who bought what, when, and for whom; and helps to settle the bills.",
            ["expense", "friends/roommates"],
        )
    ]
    demand = [
        _demand(
            candidate["repo_id"],
            "budgero-3",
            "github_issue",
            "https://github.com/tombadilo-bombadilo/budgero/issues/3",
            "Shared budgets do not currently provide a way to calculate a fair settlement when members pay shared household expenses from separate accounts and contribute in different proportions.",
            comments="1",
            reactions="2",
        )
    ]
    return _pack(
        candidate,
        prior,
        demand,
        "CANDIDATE",
        "https://github.com/mbhogal/splitfree has 0 stars and no README. The description states an expense splitter for friends/roommates. "
        "https://github.com/spiral-project/ihatemoney settles shared bills but GitHub licence.spdx_id is NOASSERTION (beerware derivative), so it is not a free OSI alternative. "
        "The MIT expense-tracker search (total_count 10) returned personal trackers, not a roommate settlement tool. "
        "https://github.com/tombadilo-bombadilo/budgero/issues/3 is open, 2 reactions, and says shared budgets do not yet calculate a settlement for household expenses. "
        "The code quote 'See who needs to pay whom' is in pages/5_💰_Settle_Up.py and is not in the description, so it is not a core term.",
        extra_evidence=[
            {
                "id": "code:mbhogal/splitfree:settle",
                "url": "https://github.com/mbhogal/splitfree/blob/HEAD/pages/5_%F0%9F%92%B0_Settle_Up.py",
                "text": "See who needs to pay whom to make everything even — based on true equal sharing among all current members.",
            }
        ],
    )


def _vocapop() -> dict:
    readme = (
        "VocaPop is a modern, spaced-repetition flashcard app for learning vocabulary in any language.\n"
        "Spaced Repetition (SM-2) - Science-backed algorithm for long-term memory retention\n"
        "Text-to-Speech - Native TTS integration for pronunciation"
    )
    licence = "Anki is licensed under the GNU Affero General Public License, version 3 or\nlater, with portions contributed by Anki users licensed under the BSD-3\nlicense (see CONTRIBUTORS)."
    candidate = _candidate(
        "shubhamkarande/VocaPop",
        "https://github.com/shubhamkarande/VocaPop",
        "VocaPop – Pop a Card. Learn a Word. Get Fluent. A cross-platform spaced-repetition flashcard app with SM-2 algorithm, TTS, offline-first sync & progress tracking.",
        readme,
        "spaced-repetition flashcard app",
        "learning vocabulary in any language",
        "Spaced Repetition (SM-2)",
        [
            "androidApp/src/main/kotlin/com/vocapop/android/MainActivity.kt",
            "androidApp/src/main/kotlin/com/vocapop/android/ui/decks/DeckManagementScreen.kt",
        ],
        "2025-07-22T17:40:39Z",
        "2025-12-31T12:12:06Z",
        "0",
        "MIT",
        ["flashcard", "vocabulary"],
    )
    prior = [
        _prior(
            candidate,
            "ankitects/anki",
            "https://github.com/ankitects/anki",
            "",
            "32212",
            "2026-10-22T17:38:18Z",
            "Anki is a smart spaced repetition flashcard program\nAnki is a spaced repetition program.",
            ["flashcard", "vocabulary"],
            licence_evidence_id="licence:ankitects/anki",
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "NO_GO",
        "https://github.com/ankitects/anki describes a spaced repetition flashcard program and was pushed 2026-10-22. "
        "GitHub licence.spdx_id is NOASSERTION. The LICENSE file says 'GNU Affero General Public License, version 3'. "
        "The stored Anki text does not say vocabulary or text-to-speech, so those candidate capabilities are gaps. "
        "No demand row with comments>=2 or reactions>=1 was recorded. "
        "https://github.com/shubhamkarande/VocaPop has 0 stars. README sha256 fa26e49f658afcf9309101d44aab8ca1a55e7a7d08590f379d9a52f59ab1e80f.",
        extra_evidence=[
            {
                "id": "licence:ankitects/anki",
                "url": "https://github.com/ankitects/anki/blob/HEAD/LICENSE",
                "text": licence,
                "full_sha256": "bde7d05790a05f1d1776b77a93ac32765310c8e6e578136183c2560475c500ef",
            }
        ],
    )


def _provenance() -> dict:
    readme = (
        "A personal knowledge base that captures the why behind software decisions.\n"
        "Knowledge that walks out the door when people leave\n"
        "processing everything into a queryable graph of decisions, assumptions, and context.\n"
        "Zoom transcripts"
    )
    candidate = _candidate(
        "jpequegn/provenance",
        "https://github.com/jpequegn/provenance",
        "Personal knowledge base that captures the 'why' behind software decisions. Ambient context capture from chat, meetings, and notes.",
        readme,
        "captures the why behind software decisions",
        "when people leave",
        "queryable graph of decisions, assumptions, and context",
        ["api/provo/api/routes/decisions.py", "api/provo/api/routes/fragments.py"],
        "2025-12-27T00:46:10Z",
        "2025-12-30T19:17:34Z",
        "0",
        "",
        ["decisions", "transcripts"],
    )
    prior = [
        _prior(
            candidate,
            "phodal/adr",
            "https://github.com/phodal/adr",
            "MIT",
            "271",
            "2026-07-13T00:57:32Z",
            "Architecture Decision Records in Node.js with Reporter, supported Windows, GNU/Linux, macOS - 轻量级架构决策记录工具",
            ["decisions", "transcripts"],
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "NO_GO",
        "https://github.com/phodal/adr is MIT, pushed 2026-07-13, and stores architecture decision records. "
        "That text does not mention Zoom transcripts. The candidate's ambient-capture capability is a gap. "
        "No qualifying external demand row was recorded. "
        "https://github.com/jpequegn/provenance has 0 stars. README sha256 6abeab29e8dae2b93b986645657a5b0aa580db783573b806292c83a7a1309748.",
    )


def _regex() -> dict:
    readme = (
        "A browser-based regex tester with live matching. Built with Next.js and runs entirely in your browser.\n"
        "This tool tests regular expressions with live matching. Perfect for debugging regex patterns and learning regex syntax.\n"
        "Test regex patterns instantly"
    )
    candidate = _candidate(
        "toolsvana/regextester",
        "https://github.com/toolsvana/regextester",
        "Regex Tester",
        readme,
        "A browser-based regex tester with live matching.",
        "debugging regex patterns and learning regex syntax",
        "Test regex patterns instantly",
        ["app/components/RegexTesterTool.tsx", "app/page.tsx"],
        "2025-12-31T01:55:06Z",
        "2025-12-31T01:58:13Z",
        "0",
        "",
        ["regex", "live matching"],
    )
    demand = [
        _demand(
            candidate["repo_id"],
            "regex-mit-search",
            "github_search",
            "https://api.github.com/search/repositories",
            "",
            metric_name="total_count",
            metric_value="0",
            query='"regex tester" OR "test regular expressions" license:mit stars:>20 pushed:>2026-04-22',
        )
    ]
    return _pack(
        candidate,
        [],
        demand,
        "NO_GO",
        "The quoted MIT search for a maintained regex tester returned total_count 0. That is not proof none exists, and a search total does not pass demand. "
        "A broader query '\"regex\" tester OR playground license:mit stars:>10 pushed:>2026-04-22' returned total_count 231 and was not treated as a cover, because the result list was not fetched as excerpts. "
        "https://github.com/toolsvana/regextester has 0 stars. README sha256 8bbc9ff3e5d8b55e997bd112b4c56b09f0f951b8a2b6acf2a574d44ce1e8ede8.",
    )


def _thomas() -> dict:
    readme = (
        "# thomas\n\n> Simple pomodoro timer\n\n"
        "* `CommandOrControl+Alt+T` — show application *(global)*\n"
        "* `CommandOrControl+Enter` — start/cancel timer"
    )
    candidate = _candidate(
        "niittymaa/thomas",
        "https://github.com/niittymaa/thomas",
        "Simple pomodoro timer",
        readme,
        "Simple pomodoro timer",
        "",
        "start/cancel timer",
        ["app/index.js", "modules/timer.js"],
        "2017-12-11T00:23:11Z",
        "2017-12-11T00:23:11Z",
        "0",
        "MIT",
        ["pomodoro", "timer"],
    )
    prior = [
        _prior(
            candidate,
            "Splode/pomotroid",
            "https://github.com/Splode/pomotroid",
            "MIT",
            "5566",
            "2026-10-09T22:44:23Z",
            "Pomotroid is a simple and configurable Pomodoro timer. It aims to provide a visually-pleasing and reliable way to track productivity using the Pomodoro Technique.",
            ["pomodoro", "timer"],
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "NO_GO",
        "The README at https://github.com/niittymaa/thomas says 'Simple pomodoro timer' and does not name a target user. "
        "https://github.com/Splode/pomotroid is MIT, pushed 2026-10-09, and says 'simple and configurable Pomodoro timer'. "
        "The idea-quality gate stops before that alternative is applied. 0 stars. README sha256 88bafbbe627a4fc132a10dad5e72b06b5c1a96eabfb8c1c1dcc175e0016f92f7.",
    )


def _task_board() -> dict:
    candidate = _candidate(
        "PratikshaKushwaha/task-board",
        "https://github.com/PratikshaKushwaha/task-board",
        "Kanban-style Task Board with drag & drop, CRUD, and localStorage persistence using HTML, CSS & JavaScript",
        "",
        "Kanban-style Task Board",
        "",
        "drag & drop, CRUD, and localStorage persistence",
        ["index.html", "script.js"],
        "2025-12-31T18:01:54Z",
        "2025-12-31T18:17:50Z",
        "0",
        "",
        ["kanban", "drag"],
    )
    return _pack(
        candidate,
        [],
        [],
        "NO_GO",
        "https://github.com/PratikshaKushwaha/task-board has no README and the description does not name a target user. "
        "https://github.com/wekan/wekan is a MIT kanban and was not reached, because idea quality fails first. 0 stars. Three files.",
    )


def _feedly() -> dict:
    candidate = _candidate(
        "tobysauze/feedlyclone",
        "https://github.com/tobysauze/feedlyclone",
        "rss reader",
        "",
        "rss reader",
        "",
        "rss reader",
        ["apps/api/src/index.ts", "apps/web/src/app/layout.tsx"],
        "2025-12-31T07:30:17Z",
        "2025-12-31T09:23:32Z",
        "0",
        "",
        ["rss", "reader"],
    )
    prior = [
        _prior(
            candidate,
            "miniflux/v2",
            "https://github.com/miniflux/v2",
            "Apache-2.0",
            "9813",
            "2026-10-21T23:27:19Z",
            "Miniflux is a minimalist and opinionated feed reader. Supported feed formats: Atom 0.3/1.0, RSS 1.0/2.0, and JSON Feed 1.0/1.1.",
            ["rss", "reader"],
        )
    ]
    return _pack(
        candidate,
        prior,
        [],
        "NO_GO",
        "https://github.com/tobysauze/feedlyclone has description 'rss reader' and no README, so no target user. "
        "https://github.com/miniflux/v2 is Apache-2.0, pushed 2026-10-21, and is a feed reader with RSS. Idea quality fails first. 0 stars.",
    )


def _pack(candidate, prior, demand, label, justification, extra_evidence=None) -> dict:
    evidence = _evidence_from_csv(candidate, prior)
    for item in extra_evidence or []:
        evidence.append({**item, "content_hash": text_hash(item["text"])})
    return {
        "repo_id": candidate["repo_id"],
        "label": label,
        "justification": justification,
        "links": [candidate["html_url"], *[row["url"] for row in prior], *[row["url"] for row in demand if row.get("url")]],
        "candidate": candidate,
        "prior_art": prior,
        "demand": demand,
        "evidence": evidence,
    }


def _candidate(repo_id, html_url, description, readme, problem, user, scope, paths, created, pushed, stars, licence, terms) -> dict:
    return {
        "repo_id": repo_id,
        "html_url": html_url,
        "description": description,
        "readme_excerpt": readme,
        "problem_excerpt": problem,
        "user_excerpt": user,
        "scope_excerpt": scope,
        "code_paths": "|".join(paths),
        "created_at": created,
        "pushed_at": pushed,
        "days_since_push": str(days_between(pushed, COLLECTED)),
        "stargazers_count": stars,
        "forks_count": "0",
        "open_issues": "0",
        "licence_spdx": licence,
        "core_terms": "|".join(terms),
        "cohort": "eval",
        "data_origin": ORIGIN,
        "collected_at": COLLECTED,
    }


def _prior(candidate, alternative_id, url, licence, stars, pushed, excerpt, terms, licence_evidence_id="") -> dict:
    row = {
        "repo_id": candidate["repo_id"],
        "alternative_id": alternative_id,
        "url": url,
        "licence_spdx": licence,
        "stars": stars,
        "pushed_at": pushed,
        "days_since_push": str(days_between(pushed, COLLECTED)),
        "excerpt": excerpt,
        "candidate_excerpt": candidate["description"] + "\n" + candidate["readme_excerpt"],
        "required_terms": "|".join(terms),
        "data_origin": ORIGIN,
        "collected_at": COLLECTED,
    }
    if licence_evidence_id:
        row["licence_evidence_id"] = licence_evidence_id
    return row


def _demand(repo_id, signal_id, kind, url, excerpt, comments="", reactions="", metric_name="", metric_value="", query="") -> dict:
    return {
        "repo_id": repo_id,
        "signal_id": signal_id,
        "kind": kind,
        "url": url,
        "excerpt": excerpt,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "comments": comments,
        "reactions": reactions,
        "stars": "",
        "pushed_at": "",
        "days_since_push": "",
        "query": query,
        "data_origin": ORIGIN,
        "collected_at": COLLECTED,
    }


def _evidence_from_csv(candidate, prior) -> list[dict]:
    items = [
        {
            "id": f"candidate:{candidate['repo_id']}",
            "url": candidate["html_url"],
            "text": "\n".join(
                part
                for part in (
                    candidate.get("description", ""),
                    candidate.get("readme_excerpt", ""),
                )
                if part
            ),
        }
    ]
    for row in prior:
        items.append({"id": f"prior:{row['alternative_id']}", "url": row["url"], "text": row["excerpt"]})
    for item in items:
        item["content_hash"] = text_hash(item["text"])
    return items


def _one(name: str, repo_id: str) -> dict:
    rows = _read(name)
    return next(row for row in rows if row["repo_id"] == repo_id)


def _many(name: str, repo_id: str) -> list[dict]:
    return [row for row in _read(name) if row["repo_id"] == repo_id]


def _read(name: str) -> list[dict]:
    with (REAL / name).open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


if __name__ == "__main__":
    raise SystemExit(main())

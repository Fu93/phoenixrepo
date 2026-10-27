"""Commodity-category overlap. Offline fixtures, no network and no Nightshift.

The invoice fixture follows the scored v3 trace for tharani-2006/Invoice-generator-application:
Excel, Canva, and Invoice Ninja were verified full overlaps and did not block, because a web
row has no push date or stars. This file does not read or rewrite that trace.
"""
import json
from datetime import date

import pytest

from app.agents.blocker import commodity_category_overlap, commodity_rule_enabled
from app.agents.relevance import RelevanceClient, judge_discovery

AS_OF = date(2026, 10, 26)
PROBLEM = "creating, managing, and sending professional invoices"


def _client(tmp_path, payload: dict):
    return RelevanceClient(cache_dir=tmp_path, mock_handler=lambda _prompt, _sys: json.dumps(payload))


def _candidate(**extra) -> dict:
    row = {
        "repo_id": "tharani-2006/Invoice-generator-application",
        "description": "A full-stack web application for creating, managing, and sending professional invoices.",
        "readme_excerpt": (
            "Invoice Generator is a comprehensive application for creating, managing, and sending professional invoices."
        ),
        "stars": 0,
    }
    row.update(extra)
    return row


def _llm_candidate() -> dict:
    return {
        "verdict": "CANDIDATE",
        "reason": "No maintained alternative covers the same invoice workflow.",
        "problem_quote": PROBLEM,
        "alternative_repo": None,
        "alternative_quote": None,
    }


def _llm_names_stale_github() -> dict:
    return {
        "verdict": "ALREADY_EXISTS",
        "reason": "MyClientBase already manages invoices.",
        "problem_quote": PROBLEM,
        "alternative_repo": "jeroenvheel/MyClientBase",
        "alternative_quote": "free and open source web based invoice management system",
    }


def _web(url: str, title: str, quote: str, **extra) -> dict:
    row = {
        "alternative_id": f"web:{url}",
        "url": url,
        "source": "web",
        "title": title,
        "description": title,
        "excerpt": f"{title}. {quote}",
        "same_problem": True,
        "overlap": "full",
        "quotes": [quote],
        "quote_verified": True,
        "page_kind": "product",
        "stars": 0,
        "pushed_at": "",
    }
    row.update(extra)
    return row


def _excel() -> dict:
    quote = "Share Excel invoices through OneDrive and grant access to collaborate online"
    return _web(
        "https://excel.cloud.microsoft/create/en/invoice-generator/",
        "Microsoft Excel online invoice generator",
        quote,
    )


def _canva() -> dict:
    return _web(
        "https://www.canva.com/invoice/",
        "Canva invoice generator",
        "Free Online Invoice Generator - browser",
    )


def _canva_templates() -> dict:
    return _web(
        "https://www.canva.com/invoice/templates/",
        "Canva invoice templates",
        "Customize free invoice templates from Canva and impress clients with a beautiful, on-brand invoice",
        page_kind="template",
    )


def _invoice_ninja() -> dict:
    return _web(
        "https://invoiceninja.com/",
        "Invoice Ninja",
        "Create, send and manage invoices with ease. 11 customizable templates, branded with your logo and colors.",
    )


def _stale_github() -> dict:
    quote = "MyClientBase is a simple, intuitive, free and open source web based invoice management system developed with freelancers in mind."
    return {
        "alternative_id": "jeroenvheel/MyClientBase",
        "url": "https://github.com/jeroenvheel/MyClientBase",
        "source": "github",
        "excerpt": quote,
        "description": quote,
        "same_problem": True,
        "overlap": "full",
        "quotes": [quote],
        "quote_verified": True,
        "stars": 42,
        "pushed_at": "2012-05-23T00:00:00Z",
        "archived": False,
        "page_kind": "product",
        "licence_spdx": "",
    }


def _invoice_rows() -> list[dict]:
    return [_excel(), _canva(), _canva_templates(), _invoice_ninja(), _stale_github()]


@pytest.fixture
def commodity_on(monkeypatch):
    """The rule is opt-in. Flag-on tests set an explicit truthy value."""
    monkeypatch.setenv("PHOENIX_COMMODITY_CATEGORY_RULE", "1")


def test_default_off_matches_pre_pr_verdicts(monkeypatch, tmp_path):
    """Unset flag: the three-domain invoice case stays on the pre-PR verdicts."""
    monkeypatch.delenv("PHOENIX_COMMODITY_CATEGORY_RULE", raising=False)
    assert commodity_rule_enabled() is False
    rows = _invoice_rows()

    model_candidate = judge_discovery(
        _candidate(),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path / "candidate", _llm_candidate()),
        problem_alternatives=rows,
    )
    assert model_candidate[0] == "CANDIDATE", model_candidate[1]
    assert model_candidate[1].startswith("No maintained alternative covers the same invoice workflow.")
    assert "COMMODITY_CATEGORY" not in model_candidate[1]
    beat = {item["alternative"] for item in model_candidate.must_beat}
    assert any("excel.cloud.microsoft" in name for name in beat)
    assert any("canva.com/invoice/" in name for name in beat)
    assert any("invoiceninja.com" in name for name in beat)

    named = judge_discovery(
        _candidate(),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path / "named", _llm_names_stale_github()),
        problem_alternatives=rows,
    )
    assert named[0] == "CANDIDATE", named[1]
    assert named[1].startswith("must beat jeroenvheel/MyClientBase: did not block:")
    assert "COMMODITY_CATEGORY" not in named[1]


def test_only_explicit_truthy_values_enable_the_rule(monkeypatch):
    monkeypatch.delenv("PHOENIX_COMMODITY_CATEGORY_RULE", raising=False)
    assert commodity_rule_enabled() is False
    for flag in ("", "0", "off", "false", "no", "disabled", "maybe"):
        monkeypatch.setenv("PHOENIX_COMMODITY_CATEGORY_RULE", flag)
        assert commodity_rule_enabled() is False, flag
    for flag in ("1", "true", "yes", "on", "enabled", "TRUE", "On"):
        monkeypatch.setenv("PHOENIX_COMMODITY_CATEGORY_RULE", flag)
        assert commodity_rule_enabled() is True, flag


def test_invoice_three_domains_already_exists_on_both_candidate_paths(commodity_on, tmp_path):
    rows = _invoice_rows()
    for name, payload in (("candidate", _llm_candidate()), ("named", _llm_names_stale_github())):
        judged = judge_discovery(
            _candidate(),
            prior_rows=[],
            demand_rows=[],
            client=_client(tmp_path / name, payload),
            problem_alternatives=rows,
        )
        assert judged[0] == "ALREADY_EXISTS", judged[1]
        assert judged[1].startswith(
            "COMMODITY_CATEGORY: canva.com, excel.cloud.microsoft, invoiceninja.com each fully overlap "
            "creating, managing, and sending professional invoices (verified quotes)."
        )
        assert "github.com" not in judged[1].split("Method:", 1)[0]


def test_two_commercial_domains_stay_candidate(commodity_on, tmp_path):
    rows = [_excel(), _canva(), _canva_templates(), _stale_github()]
    judged = judge_discovery(
        _candidate(),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, _llm_candidate()),
        problem_alternatives=rows,
    )
    assert judged[0] == "CANDIDATE", judged[1]
    assert "COMMODITY_CATEGORY" not in judged[1]


def test_listicle_aggregator_and_review_rows_are_excluded(commodity_on, tmp_path):
    rows = [
        _excel(),
        _canva(),
        _web(
            "https://www.g2.com/categories/invoicing",
            "G2 best invoicing tools",
            "G2 lists the best invoicing tools for freelancers.",
            page_kind="listicle",
        ),
        _web(
            "https://alternativeto.net/software/invoice-ninja/",
            "AlternativeTo Invoice Ninja",
            "AlternativeTo groups invoice tools that replace Invoice Ninja.",
            page_kind="aggregator",
        ),
        _web(
            "https://www.capterra.com/invoice-software/",
            "Capterra invoice software review",
            "Capterra reviews invoice software for small businesses.",
            page_kind="review",
        ),
    ]
    judged = judge_discovery(
        _candidate(),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, _llm_candidate()),
        problem_alternatives=rows,
    )
    assert judged[0] == "CANDIDATE", judged[1]
    assert "COMMODITY_CATEGORY" not in judged[1]


def test_unverified_quote_is_excluded(commodity_on, tmp_path):
    zoho = _web(
        "https://www.zoho.com/invoice/",
        "Zoho Invoice",
        "Zoho Invoice helps businesses create and send invoices.",
        quote_verified=False,
    )
    judged = judge_discovery(
        _candidate(),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, _llm_candidate()),
        problem_alternatives=[_excel(), _canva(), zoho],
    )
    assert judged[0] == "CANDIDATE", judged[1]
    assert "COMMODITY_CATEGORY" not in judged[1]
    assert judged[0] != "ALREADY_EXISTS"


def test_free_core_differentiator_skips_paid_only_vendors(commodity_on, tmp_path):
    def paid(url: str, title: str, token: str) -> dict:
        quote = f"{token} invoicing is subscription required and paid-only."
        return _web(url, title, quote)

    paid_rows = [
        paid("https://quickbooks.intuit.com/online/invoicing/", "QuickBooks Online", "QuickBooks"),
        paid("https://www.freshbooks.com/", "FreshBooks", "FreshBooks"),
        paid("https://www.waveapps.com/invoice", "Waveapps invoicing", "Waveapps"),
    ]
    free_core = _candidate(
        repo_id="someone/free-invoice",
        description="A free alternative to hosted invoice generators.",
        readme_excerpt="The core value is being free. A free alternative for creating, managing, and sending professional invoices.",
    )
    blocked = judge_discovery(
        _candidate(repo_id="someone/invoice-app"),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, _llm_candidate()),
        problem_alternatives=paid_rows,
    )
    assert blocked[0] == "ALREADY_EXISTS", blocked[1]
    assert "COMMODITY_CATEGORY" in blocked[1]

    released = judge_discovery(
        free_core,
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, _llm_candidate()),
        problem_alternatives=paid_rows,
    )
    assert released[0] == "CANDIDATE", released[1]
    assert "COMMODITY_CATEGORY" not in released[1]


@pytest.mark.parametrize("flag", ["0", "off", "false", "no", "disabled"])
def test_flag_off_keeps_the_old_candidate(monkeypatch, tmp_path, flag):
    monkeypatch.setenv("PHOENIX_COMMODITY_CATEGORY_RULE", flag)
    judged = judge_discovery(
        _candidate(),
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, _llm_candidate()),
        problem_alternatives=_invoice_rows(),
    )
    assert judged[0] == "CANDIDATE", judged[1]
    assert "COMMODITY_CATEGORY" not in judged[1]


def test_novel_candidate_with_two_weak_commercial_pages_stays_candidate(commodity_on, tmp_path):
    candidate = {
        "repo_id": "orangekame3/mirrormate",
        "description": "A voice-first assistant that lives in a mirror.",
        "readme_excerpt": "A voice-first AI designed to live in a mirror and is not a chat UI in a browser.",
        "stars": 0,
    }
    weak = [
        _web(
            "https://www.amazon.com/alexa",
            "Amazon Alexa",
            "Alexa answers questions out loud.",
            overlap="partial",
            same_problem=True,
        ),
        _web(
            "https://www.apple.com/siri/",
            "Apple Siri",
            "Siri answers spoken questions on Apple devices.",
            overlap="partial",
            same_problem=True,
        ),
    ]
    payload = {
        "verdict": "CANDIDATE",
        "reason": "A mirror-resident assistant is not a general voice product.",
        "problem_quote": "voice-first AI designed to live in a mirror",
        "alternative_repo": None,
        "alternative_quote": None,
    }
    judged = judge_discovery(
        candidate,
        prior_rows=[],
        demand_rows=[],
        client=_client(tmp_path, payload),
        problem_alternatives=weak,
    )
    assert judged[0] == "CANDIDATE", judged[1]
    assert "COMMODITY_CATEGORY" not in judged[1]


def test_github_row_counts_only_when_blocker_checks_pass(commodity_on):
    candidate = _candidate(repo_id="someone/invoice-app", stars=10)
    mature = {
        "alternative_id": "invoice-org/server",
        "url": "https://github.com/invoice-org/server",
        "source": "github",
        "excerpt": "Server sends professional invoices.",
        "same_problem": True,
        "overlap": "full",
        "quotes": ["Server sends professional invoices."],
        "quote_verified": True,
        "stars": 800,
        "pushed_at": "2026-10-01T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
    }
    hit = commodity_category_overlap(
        candidate,
        [_excel(), _canva(), mature],
        as_of=AS_OF,
        problem=PROBLEM,
    )
    assert hit is not None
    assert hit["domains"] == ["canva.com", "excel.cloud.microsoft", "github.com"]
    assert hit["commercial_domains"] == ["canva.com", "excel.cloud.microsoft"]

    missed = commodity_category_overlap(
        candidate,
        [_excel(), _canva(), _stale_github()],
        as_of=AS_OF,
        problem=PROBLEM,
    )
    assert missed is None

"""First-party vendor docs count as official. Stars and push dates do not.

The sergiitk/pagerbeauty case: PagerDuty's own Datadog guide and Datadog's own
PagerDuty page were verified full overlaps and were dropped as immature.
A blog, a 0-star GitHub repo, and another product's docs stay immature.
Mock-only: evaluate_blocker does not call Nightshift.
"""
from datetime import date

from app.agents.blocker import evaluate_blocker, first_party_vendor_docs

AS_OF = date(2026, 10, 26)
CANDIDATE = {
    "repo_id": "sergiitk/pagerbeauty",
    "description": "Live PagerDuty on-call schedule viewer.",
    "readme_excerpt": "Shows who is on call from a PagerDuty schedule.",
    "stars": 400,
    "free": True,
    "self_hostable": True,
}
FREE_CORE = {
    **CANDIDATE,
    "description": "A free alternative to hosted on-call dashboards.",
    "readme_excerpt": "A free alternative to hosted on-call dashboards.",
}


def _page(**extra) -> dict:
    excerpt = extra.get("excerpt") or extra.get("description") or ""
    row = {
        "stars": 0,
        "pushed_at": "",
        "released_at": "",
        "archived": False,
        "licence_spdx": "",
        "same_problem": True,
        "same_job": True,
        "overlap": "full",
        "quote_verified": True,
        "source": "web",
        "quotes": [excerpt] if excerpt else [],
    }
    row.update(extra)
    return row


def _reasons(decision: dict) -> str:
    return " ".join(decision["reasons"])


def test_pagerduty_datadog_guide_blocks_pagerbeauty():
    quote = "PagerDuty's Datadog integration sends Datadog alerts into PagerDuty incidents."
    row = _page(
        title="PagerDuty Datadog integration guide",
        url="https://support.pagerduty.com/main/docs/datadog-integration",
        excerpt=quote,
        quotes=[quote],
    )
    official, why = first_party_vendor_docs(row)
    assert official is True
    assert "pagerduty.com" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is True
    assert decision["role"] == "block"
    assert decision["verdict_if_blocked"] == "ALREADY_EXISTS"
    assert "first-party" in _reasons(decision)
    assert "access kept" in _reasons(decision)


def test_datadog_pagerduty_page_blocks_pagerbeauty():
    quote = "Datadog's PagerDuty integration notifies the on-call schedule when a monitor alerts."
    row = _page(
        title="Datadog PagerDuty integration",
        url="https://docs.datadoghq.com/integrations/pagerduty/",
        excerpt=quote,
        quotes=[quote],
    )
    official, why = first_party_vendor_docs(row)
    assert official is True
    assert "datadoghq.com" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is True
    assert decision["verdict_if_blocked"] == "ALREADY_EXISTS"
    assert "first-party" in _reasons(decision)


def test_atlassian_support_docs_are_first_party():
    quote = "Atlassian Jira Service Management integrates with Datadog and shows the on-call roster."
    row = _page(
        title="Atlassian Jira Service Management",
        url="https://support.atlassian.com/jira-service-management-cloud/docs/integrate-with-datadog/",
        excerpt=quote,
        quotes=[quote],
    )
    official, why = first_party_vendor_docs(row)
    assert official is True
    assert "atlassian.com" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is True
    assert decision["verdict_if_blocked"] == "ALREADY_EXISTS"


def test_grafana_irm_docs_stay_partial():
    quote = "Grafana IRM routes alerts to a contact point."
    row = _page(
        title="Grafana IRM",
        url="https://grafana.com/docs/grafana/latest/alerting/configure-notifications/",
        excerpt=quote,
        quotes=[quote],
        overlap="partial",
        same_job=None,
    )
    official, _why = first_party_vendor_docs(row)
    assert official is True
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert decision["role"] == "mature_partial"
    assert decision["verdict_if_blocked"] is None


def test_random_blog_is_not_official():
    quote = "PagerDuty on-call viewer for schedules."
    row = _page(
        title="PagerDuty on-call viewer",
        url="https://medium.com/@someone/pagerduty-on-call-viewer",
        excerpt=quote,
        quotes=[quote],
    )
    official, why = first_party_vendor_docs(row)
    assert official is False
    assert "publisher" in why or "platform" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert decision["verdict_if_blocked"] is None
    assert "first-party vendor docs" not in _reasons(decision)
    assert "stars=0" in _reasons(decision)


def test_zero_star_github_repo_is_not_official():
    quote = "A PagerDuty schedule viewer."
    row = _page(
        alternative_id="someone/pagerbeauty-clone",
        title="pagerbeauty clone",
        url="https://github.com/someone/pagerbeauty-clone",
        excerpt=quote,
        quotes=[quote],
        source="github",
        stars=0,
    )
    official, why = first_party_vendor_docs(row)
    assert official is False
    assert "code host" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert decision["role"] == "must_beat"
    assert "stars=0" in _reasons(decision)


def test_different_products_docs_are_not_that_vendors_page():
    quote = "PagerDuty on-call schedule viewer built into the product."
    row = _page(
        title="PagerDuty on-call schedule",
        url="https://docs.djangoproject.com/en/stable/topics/db/",
        excerpt=quote,
        quotes=[quote],
    )
    official, why = first_party_vendor_docs(row)
    assert official is False
    assert "not the vendor" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert "not the vendor" in _reasons(decision)
    assert decision["verdict_if_blocked"] is None


def test_docs_platform_is_not_the_vendor_domain():
    quote = "PagerDuty on-call schedules are listed on this page."
    row = _page(
        title="PagerDuty on-call",
        url="https://pagerduty.readthedocs.io/en/latest/oncall.html",
        excerpt=quote,
        quotes=[quote],
    )
    official, why = first_party_vendor_docs(row)
    assert official is False
    assert "platform" in why


def test_same_job_still_gates_another_vendors_real_docs():
    quote = "Django's database API stores models."
    row = _page(
        title="Django databases",
        url="https://docs.djangoproject.com/en/stable/topics/db/",
        excerpt=quote,
        quotes=[quote],
        same_problem=False,
        same_job=False,
        overlap="none",
    )
    official, why = first_party_vendor_docs(row)
    assert official is True
    assert "djangoproject.com" in why
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert decision["verdict_if_blocked"] is None


def test_unverified_quote_does_not_make_vendor_docs_official():
    page = "PagerDuty's Datadog integration sends Datadog alerts into PagerDuty incidents."
    row = _page(
        title="PagerDuty Datadog integration guide",
        url="https://www.pagerduty.com/docs/datadog-integration",
        excerpt=page,
        quotes=["a fabricated sentence that is not on the page"],
        quote_verified=True,
    )
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert "first-party vendor docs" not in _reasons(decision)


def test_paid_only_quote_mismatches_a_candidate_whose_core_value_is_being_free():
    quote = "This Datadog integration is available on a paid plan only."
    row = _page(
        title="Datadog PagerDuty integration",
        url="https://docs.datadoghq.com/integrations/pagerduty/",
        excerpt=quote,
        quotes=[quote],
    )
    decision = evaluate_blocker(FREE_CORE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert decision["verdict_if_blocked"] is None
    assert "access mismatch" in _reasons(decision)
    assert "paid-only" in _reasons(decision)
    assert "core value is being free" in _reasons(decision)


def test_paid_only_quote_still_blocks_when_being_free_is_not_the_core_value():
    quote = "This Datadog integration is available on a paid plan only."
    row = _page(
        title="Datadog PagerDuty integration",
        url="https://docs.datadoghq.com/integrations/pagerduty/",
        excerpt=quote,
        quotes=[quote],
    )
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is True
    assert decision["verdict_if_blocked"] == "ALREADY_EXISTS"
    assert "access kept" in _reasons(decision)
    assert "core value is not being free" in _reasons(decision)

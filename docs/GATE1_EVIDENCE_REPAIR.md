# Gate 1 evidence repair — local, post-v3 candidate

This work fixes evidence plumbing and adds an opt-in substitution contract. It
does not change the frozen v3 traces, labels, reports, or the reported 10/16.
No new real-model evaluation or unseen labelled evaluation has been performed.

## Corrections used on new runs

**Opt-in: `PHOENIX_GATE1_EVIDENCE_REPAIR=1` (`1`, `true`, `yes`, `on`, or `enabled`).**
Everything in this section runs only when that switch is set. Unset, it is off and
the verdicts, tool calls, prompts, cache keys and trace shape of a default run are
those of main 37ab838 (checked on offline fixtures; see Default-off check below).
The switch is independent of `PHOENIX_STRICT_SUBSTITUTION` and
`PHOENIX_UNKNOWN_BLOCKER_REVIEW`. Rule B's strict reading of `free_tier`,
`self_hosted`, `official` and `commercial` as real booleans is also active when Rule B
itself is on, because Rule B needs it.

- Exact GitHub repository URLs returned by web search can now receive host facts
  and a README even when keyword repository search misses them. At most three
  optional metadata lookups use the existing toolbox budget; no product name is
  hardcoded. Search-source counts keep their original meaning.
- Web placeholders (`stars=0`, `archived=False`) no longer defeat host API facts.
  Missing GitHub search counts remain unknown, rather than becoming zero.
  Host URLs are parsed with an exact host check; arbitrary hostname substrings,
  userinfo, and query/path injection cannot select a repository API endpoint.
- Both single and batch classification preserve collected access, release,
  maintenance, page-kind and fork facts. The model cannot supply or overwrite
  those fields. A directory marked by the source is not silently made a product.
- A quote verified beyond the 1,000-character display excerpt retains its source
  context for the final gate. A `quote_verified` boolean alone is insufficient:
  a claimed full blocker must have quotes present in its collected text.
- Free-tier and self-hosted access require actual `True` booleans. Unknown
  strings do not establish access. Copyleft licences are not newly allowlisted;
  an explicitly evidenced self-hosted/free alternative follows the existing
  access policy. This distinguishes missing access facts from facts lost in transit.
- Model-named alternatives use exact canonical repo IDs/URLs or unique short
  names. Name prefixes and descriptive mentions cannot identify a blocker.
- Broader-search retries merge their coverage metadata and deduplicate rows.
  Earlier errors or budget truncation remain visible. Zero results still mean
  `NEEDS_PRIOR_ART`; no case is forced to pass to match a label.
- `ALTERNATIVES_VERIFIED` records facts after hydration, along with source-fact
  hashes, substitution assertions and a hash of verified context. `GATE_DECISION`
  retains raw and final verdicts. These hashes do not by themselves store the
  complete raw API response or make semantic judgments reproducible.

## Experimental quoted substitution contract

`PHOENIX_STRICT_SUBSTITUTION=1` enables the new mode. It is **off by default**,
independent of the default-off `PHOENIX_UNKNOWN_BLOCKER_REVIEW` safety hold.
Do not set either in a shared environment while another run is in progress.

The alternative classifier must supply:

```json
{
  "substitution": {
    "candidate_quote": "verbatim candidate source text",
    "alternative_quote": "verbatim alternative source text",
    "concrete_product": true,
    "same_user": true,
    "same_operation": true,
    "same_direction": true,
    "same_deployment": true
  }
}
```

The source text is the candidate's stored description/README/problem excerpt,
not just the model's paraphrased problem statement. Both quotes are checked again
at the gate; fields must be actual booleans. All matches plus a concrete product
give `quoted_match`. A quoted context difference gives `quoted_mismatch` and
cannot support a full-substitute refusal. A directory, missing proof, unknown
dimension, wrong quote or string boolean gives `unverified`; an otherwise passing
candidate is held for review. Phrase/keyword fallback cannot certify this mode.

The model still judges whether those contexts match. **Quote presence verifies
provenance, not the truth of the semantic inference.** These statuses do not
prove that Nemotron is accurate. A directory is a lead for further retrieval;
the contract does not automatically fetch all products linked from that page.

Strict mode changes the actual classifier system/user prompts and therefore the
cache identity. It does not reuse a legacy answer without the contract. A test
checks two calls across the mode change and a cache hit on the repeated strict
request. No original cache directory is migrated or invalidated by this work.

## What was actually verified

Synthetic counterexamples exercise source access facts surviving both parsers,
host metadata replacing placeholders, archived repositories, exact URL handling,
fork exclusion, quote context, wrong product names, source-error coverage and
bounded retrieval. Strict-mode cases include reversed operation direction,
different deployment/user, directories, missing/forged quotes and invalid types.

Two synthetic CSV → retrieval → classifier → host facts → final judge → sealed
report integrations exercise a real refusal and an uncertainty hold. All API
responses and model replies are mocked; their traces are test artifacts, **not
live NVIDIA evidence**. No unknown third-party repository code is executed.

The original 15 plumbing counterexamples were run against the previous local
Rule B candidate before these fixes: 13 failed, 2 passed. They pass after repair.
One old positive test fixture was missing its source text; that fixture now
includes the text it claims to quote. A separate negative test rejects an absent
source quote instead of weakening the new verification.

The frozen-fact Rule B reconstruction is still 10/16, four false passes changed
to review and four previously agreeing cases additionally reviewed. It is not a
test of the new retrieval flow or strict contract: v3 lacks their new inputs.

## Remaining validation, not a claimed fix of all six cases

1. Invoice and ScrapeServ: the new URL bridge helps only if search returns a
   relevant repository URL. It does not prove that missing named competitors are
   now recalled. Measure retrieval recall separately on independently selected cases.
2. Non-forge commercial web pages still need reliable collected product/access
   evidence. Hostname shape alone is not a verified maintainer or pricing policy.
3. go-file-sync: preserving real access facts repairs a data-loss path; it does
   not establish which alternative the actual next run will retrieve and verify.
4. x402: reassess the label independently. Its cited seller quickstart differs
   from the v3 facilitator directory. Do not change a verdict merely to fit that label.
5. CalendarSync: investigate retrieval and compare read/write direction. A
   completed search without alternatives still cannot assert novelty.
6. Freeze the code and policy, then test a newly labelled set chosen without
   looking at model output. Report false passes, false refusals, review workload,
   retrieval coverage, infrastructure failures and model requested/served IDs.
   Preserve v3 and publish any future result as a separate run.

Keep both switches off until that validation and a review-workload decision.
The 180-second per-attempt and 360-second per-call limits remain unchanged.

## Default-off check

With all three switches unset, 16 offline scenarios (a ytkb fixture end to end, forge and
web rows with unknown facts, a caller-set `quote_verified` with a quote absent from the
text, a substring-named alternative, a source-error row, string booleans in facts,
repository URL parsing, host-fact hydration, `search_prior_art` rows and tool calls, and a
model row with its prompt hashes) produce identical verdicts, reasons, row fields, tool
calls and prompt hashes on this branch and on main 37ab838. With the switch on, 10 of the
16 differ, which is the repair. `tests/agents/test_gate1_repair_flag.py` pins both sides.
The prompt text does not depend on this switch, so model cache keys do not change.
The offline evidence does not show what a live model would return on a real run.

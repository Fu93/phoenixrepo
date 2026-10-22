# Architecture

PhoenixRepo still ends an investigation in an evidence verdict. The revival cohort and the discovery cohort do not share a verdict function.

## Two cohorts

Repositories already in `phoenix.repo_evidence` keep `decide()` and `apply_revival_attempts()` in `app/agents/policy.py`. Missing tests, a missing licence file, a non-permissive SPDX id, a successor URL, and a qualifying stage-1 CI result mean what they meant before. hygen is in that set. It is a once-popular tool. Prior-art rows for it are recorded and a note is appended. They do not replace the revival verdict.

A discovery row in `phoenix.discovery_candidates` is judged by `app/agents/market_filter.py` instead. The repository's own stars, forks, and issues are stored and ignored. A unit test inflates those fields to 9999 and requires the same verdict. A missing SPDX id on an unfinished idea is a productise task, not an automatic `NO_GO`.

`POST /run` still needs a `phoenix.repo_evidence` row. A discovery repository is not added to that five-repository snapshot. Its plan is `scripts/write_discovery_prd.py`, which reads the CSVs. If a future profile row and a discovery row exist for the same id, the investigation uses the discovery verdict, skips the planner on `ALREADY_EXISTS` and discovery `NO_GO`, and writes the four-stage plan only for a discovery `CANDIDATE`.

## Evidence tables

File-backed views, same SQL shape as the other Phoenix views:

- `phoenix.discovery_candidates`
- `phoenix.prior_art`
- `phoenix.demand_signals`

`scripts/zetaris/04_phoenix_views.sql` and both branches of `scripts/zetaris/load_real.ps1` create them. The verify `SELECT` still checks `phoenix.repo_evidence` only. Sample mode serves header-only files from `data/sample` plus the committed rows in `data/real`. An empty result is a no-op: the revival verdict is unchanged.

`scripts/collect_discovery.py` normalises `data/discovery/raw/snapshot.json`. It does not call GitHub. Days are recomputed from `pushed_at` and `collected_at`. A stored day count that disagrees fails closed.

## Deterministic guard

The guard decides. A model may propose an alternative URL. `accept_proposed_alternative` returns the row only when that URL is already stored and the row already qualifies. Any other URL is rejected.

`ALREADY_EXISTS` requires every one of these:

- The licence is in the free open-source set (`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `MPL-2.0`, `GPL-2.0`, `GPL-3.0`, `LGPL-2.1`, `LGPL-3.0`, `AGPL-3.0`, `Unlicense`, `0BSD`). Empty and `NOASSERTION` do not qualify.
- `days_since_push` recomputes to 180 or fewer. A missing date fails closed. Stars are not a maintenance gate.
- The alternative is not the same repository.
- At least two required terms are recorded, and each term is a case-insensitive substring of both the candidate excerpt and the alternative excerpt.

External demand passes on one of these, and never on the candidate's own counts:

- `github_issue` whose URL does not contain the candidate id, whose excerpt contains a core term, and whose comments are at least 2 or whose reactions are at least 1.
- `related_repo` with at least 100 stars, a push inside 180 days, a core term in the excerpt, and a URL that is not the candidate.
- `registry_downloads` of at least 1000 only when the excerpt contains a core term, so a popular PDF library is not invoice demand.
- `github_search` is recorded with the query and `total_count`. It does not pass demand. A total of 0 is that query's total, not a census and not a failure by itself.

That list is the phrase/keyword fallback. The reason is labelled `Method: phrase/keyword.`

When Nightshift is in use and a discovery row exists, the equivalence role proposes capabilities and a covered / partial / missing map. `app/agents/equivalence.py` checks every quote. A quote counts only as an exact substring of the stored text, and only when the stored `content_hash` is the sha256 of that text. `ALREADY_EXISTS` then requires one alternative, inside the same 180-day window and the same OSI set, to cover every capability. Partial and missing are gaps. A gap list is `CANDIDATE` only when external demand also passes; otherwise `NO_GO`, with the gaps in the reason, labelled `Method: functional-equivalence.` A mapping whose capability citations are not in the stored text is rejected and the phrase fallback decides. Anki's GitHub `spdx_id` is `NOASSERTION`; the functional path may use AGPL-3.0 only when the stored LICENSE text contains the sentence "Anki is licensed under the GNU Affero General Public License, version 3 or". The phrase path does not.

Order for the phrase fallback: idea failure is `NO_GO`; else a qualifying alternative is `ALREADY_EXISTS`; else missing external demand is `NO_GO`; else `CANDIDATE`.

`ALREADY_EXISTS` is a terminal state reached from `VALUE_DECISION`. It does not get a revival or productise plan.

Role prompts are `app/prompts/*.md`. The trace stores `prompt_version`, sha256 of the file, on the LLM event. The reviewer prompt is the live judge. It is told to reject unsupported claims and missed alternatives. Sample mode does not call it.

`scripts/eval_ideas.py` prints phrase-method accuracy for `data/eval/cases.json`. The 22 Oct 2026 run was 9/12, accuracy 0.75. `scripts/eval_ideas_live.py` is the Nightshift runner and was not executed here.

## Plan

The revival planner still emits revive, refactor, and upgrade. The discovery planner emits those three as plan-only stages plus stage 4 `productise`: confirm an SPDX id when `licence_spdx` is empty, keep the install commands already in the README, and state the feature scope already in the README. Citations use the catalog SQL and `result_content_hash`. The committed file uses query id `snapshot` and says the run hash is not sealed.

The trace hash chain is unchanged in what it proves. It shows the record was not altered after a run. It does not show that the record is true. LLM events still store `requested_model`, `served_model`, and, when a role prompt was loaded, `prompt_version`.

## Story the demo prints

`scripts/discovery_story.py` prints the committed verdicts without a network call: filter (`ALREADY_EXISTS` or no external demand), rescue the idea that passes, and leave hygen on its revival track. The later fork work is [Fu93/hygen#1](https://github.com/Fu93/hygen/pull/1), commits U1 through U7. The U7 workflow run was still `in_progress` with a null conclusion when the jobs had already succeeded. That run is not described as green.

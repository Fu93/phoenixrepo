# What the audit of our own run found

One scored gate-1 run of the 16 cases in `data/eval/gate1_labels.json` that have an `audit` object. The verdicts are the `DISCOVERY_FILTER` lines in `data/replays/`. Nothing below is marked fixed. A sentence marked **inferred** is not a field the trace stored.

The gate currently cannot refuse a repo because of a famous commercial product.

## Cause per case

Eight of the twelve v3 `CANDIDATE` verdicts use a reason that starts with `must beat <name>: did not block`. That string is written in `app/agents/relevance.py` from line 1956 through line 2019, in the branch that runs when the model verdict is `ALREADY_EXISTS` and the named alternative fails checks A–E. **Inferred** from that reason format. The trace does not record the model's raw verdict. All 4 false passes take this path. The other four `CANDIDATE` reasons (`samuelarogbonlo/knetvis`, `Carvenjizaks/gc-sync-webhook`, `ri-char/pwatch`, `0xMH/fichero-printer`) do not.

| Case | Cause in the committed trace |
| --- | --- |
| `tharani-2006/Invoice-generator-application` | Web-row gap, plus a recall gap. Excel, Canva, and Invoice Ninja are full-overlap web rows under `must_beat` with `did not block` (no push or release date, `stars=0`, `official=False`). A web search row is stored with `stars` 0 and an empty `pushed_at` (`app/agents/relevance.py` around 2391). Nothing in `app/` or `scripts/` sets `official`, `commercial`, `page_checked_at`, `pricing_page`, or `free_tier` on that row. Tests set those fields. The strings `invoify` and `InvoicePlane` do not appear in this trace. |
| `goodreasonai/ScrapeServ` | Recall gap. The string `Gotenberg` does not appear. |
| `rianvdm/raindrop-wordpress-linkblog-sync` | Web-row gap. The IFTTT page is a full-overlap web row under `must_beat` with `did not block`. |
| `Shabi-x/go-file-sync` | Licence gate (licence not on the permissive list; the licence name is not in the trace). |
| `Dhaiwat10/x402-sovereign` | Over-refusal. The trace refused the repository because `https://docs.x402.org/dev-tools/facilitators` blocked. That host starts with `docs`, so `_docs_shaped` in `app/agents/blocker.py` matches. The human label cites `https://docs.x402.org/getting-started/quickstart-sellers`. |
| `aa12gq/stack-file-sync-vscode` | 10/16 agree, one of them not by evidence. This agreement is `NEEDS_REVIEW` because `alternative_repo` `espenalbert/path-sync` was not found (`app/agents/relevance.py` around 1975). The same trace lists `web:https://espenalbert.github.io/path-sync/` with overlap `partial`. That is an id-format mismatch. |
| All 16 scored v3 runs | Each `evidence_source` line is `evidence_source=csv` with `prior_rows=0`. No scored verdict used a Zetaris query or a `prior_art` row. |

## Limits and next

These are post-v3 and unvalidated. Pull requests #59, #62, #65, and #66 are not merged and are not on this branch. Each switch is default off: it runs only when set to `1`, `true`, `yes`, `on`, or `enabled`. Unset stays off. None of them are part of any reported number. None are described as fixed.

| Change | Where | Switch |
| --- | --- | --- |
| Rule B | Pull request #59, not merged | `PHOENIX_UNKNOWN_BLOCKER_REVIEW` is default off. `unknown_blocker_review_enabled` on `cursor/unknown-blocker-review-cd8e` returns true only for `1`, `true`, `yes`, `on`, or `enabled`. |
| Web GitHub lookup | Pull request #62, not merged | `PHOENIX_WEB_GITHUB_API_LOOKUP` is default off. Same on values. |
| Quote-fail retry | Pull request #65, not merged | `PHOENIX_QUOTE_FAIL_FLAG` is default off. Same on values. One verbatim retry when a full-overlap quote fails, then `NEEDS_REVIEW`. |
| Commodity-category rule | Pull request #66, not merged | `PHOENIX_COMMODITY_CATEGORY_RULE` is default off. Same on values. It was designed on these same 16 cases. It needs a separate unseen labelled set before anyone treats it as checked. Using the same 16 cases is an overfitting risk. |
| Copyleft licence-gate widening | No flag and no open pull request in this checkout | Not a switch here. Gate 1's `PERMISSIVE` set in `app/agents/blocker.py` does not include `GPL-3.0`. |

**Offline reading of Rule B, not a live run.** This is recomputed from the recorded `must_beat` reasons. The traces do not store the raw model answers. It is not a new live run, and it is not a reported number. Reading those lines against the Rule B conditions on pull request #59 (full-overlap, non-forge web page, verified quote, no push or release date, no proven non-match such as a listicle or a category page) would turn the 4 false passes from `CANDIDATE` into `NEEDS_REVIEW`. The same reading would also hold 4 `CANDIDATE` verdicts that are already inside the label: `DayanK/LinkedinSyncContacts`, `oleksiiluchnikov/obsidian-localtable`, `samwho/spacer`, and `sealdice/qq-group-files-sync`. Agreement stays 10/16, because `NEEDS_REVIEW` is already acceptable for those four and is not acceptable for the four false passes.

## Earlier Zetaris gate-1 trace

The committed Zetaris gate-1 trace judged Invoice `ALREADY_EXISTS` with evidence alternative `al1abb/invoify`. That run's `prior_art` query for Invoice returned 1 row and selected `licence_spdx`, `stars`, and `pushed_at`. The committed `data/real/prior_art.csv` row is MIT, 6376 stars, pushed `2026-08-24T02:55:34Z`. The scored v3 Invoice trace has `prior_rows=0` and does not name that row.

27 of 27 answers used were Nemotron's, 21 through Nightshift and 6 through nvidia-direct. The trace is `data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl`. The 21 `LLM_CALL` lines have no `provider` field and carry Nightshift's routing block. The 6 store `provider` `nvidia-direct` with `fallback_reason` `nightshift HTTP 502`. The writer is `_observe` / `_store` in `app/agents/relevance.py`, the same path as the scored 158 `LLM_CALL` lines.

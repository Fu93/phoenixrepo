# Rule B: opt-in uncertainty review, not a correct refusal

`PHOENIX_UNKNOWN_BLOCKER_REVIEW` is **off by default**. Only `1`, `true`, `yes`,
`on`, or `enabled` enables it. It is a post-v3 proposal designed after seeing the
16 scored cases, with no held-out validation. Do not enable it by default or count
its results in the scored v3 figures.

When enabled, a quoted full-overlap non-forge web alternative with only unknown
blocker facts can hold a would-be `CANDIDATE` as `NEEDS_REVIEW`. A proven non-match
or an existing `ALREADY_EXISTS` is not rewritten. The rule never invents a correct
`ALREADY_EXISTS` from missing facts. Explicit unknown licence markers, such as
`NOASSERTION` and `unknown`, are unknown; an explicit non-permissive licence keeps
the existing access policy. Unparsed access strings are not known booleans.

## Compatibility with the current trace

The proposed integration retains #67. A hold has one `GATE_DECISION` with the
original model verdict, the actual holding alternative, path
`UNKNOWN_BLOCKER_FACTS`, and final `NEEDS_REVIEW`. A separate
`UNKNOWN_BLOCKER_FACTS` event and the final filter record retain the unknown fields.
No raw page body or model rationale is added to that gate event.

## Own distribution packages

A candidate's own distribution package should not trigger this uncertainty hold.
The new exclusion is deliberately narrow: already-collected Alpine package
metadata must have an explicit `Project` table entry pointing to the **exact**
candidate GitHub repository. HTML table cells and normalized `Project | URL`
lines are supported. Same names, a bare GitHub mention, ambiguous Project values,
another host, and absent metadata do not establish identity. Another unknown
competitor can still hold the case.

The helper performs no fetch. A caller must already have that page evidence; the
rule does not infer or synthesize it from a name. The trace records source URL,
project URL and the collected-text hash when this exclusion applies.

The [Alpine spacer page](https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/spacer)
was checked on 27 October 2026: its Project points to `samwho/spacer`. A normalized
snapshot is in `tests/fixtures/prior_art/alpine_spacer_project_evidence.json`.
That is **new identity evidence**, not a field preserved by v3. It must not be
retroactively inserted into the scored trace or claimed as an automatic v3 fix.

## Reconstructed frozen-fact check

```text
python scripts/rule_b_offline.py --web-cache-copy <copied-web-cache> --as-of 2026-10-26
```

The script verifies pinned committed v3 traces, uses their recorded verified
alternatives, reconstructs old stars/date/licence placeholders, and reads web
snippets only from an explicit cache copy. Cache iteration is sorted. Input hashes,
the fixed date and the reconstruction assumptions are reported. It calls the
actual guard with the switch off and on; it does **not** rerun the model or the
full decision flow. Network access is disabled in its CLI process.

On the checked 16 cases, the frozen-fact reconstruction gave:

| Measure | Flag off | Flag on |
| --- | --- | --- |
| Recorded-label agreement | 10/16 | 10/16 |
| Automatic false passes (`CANDIDATE` on must-reject cases) | 4 | 0 |
| Must-reject cases still outside their required `ALREADY_EXISTS` label | 4 | 4 |
| `NEEDS_REVIEW` verdicts, including the existing review | 1 | 9 |

The eight added reviews consist of four false passes and four previously agreeing
`CANDIDATE`s. The latter cost is 4/16 (25%); **all added reviews are 8/16 (50%)**.
The acceptable set includes `NEEDS_REVIEW` for the four agreeing candidates, so
agreement does not measure this added workload.

The false passes are Invoice, ScrapeServ, raindrop-sync, and go-file-sync. The four
additional agreeing reviews are LinkedinSyncContacts, spacer, obsidian-localtable,
and qq-group-files-sync. x402-sovereign and CalendarSyncProject remain unchanged.

## Separate new-evidence sensitivity check

```text
python scripts/rule_b_offline.py --web-cache-copy <copied-web-cache> --as-of 2026-10-26 --identity-evidence tests/fixtures/prior_art/alpine_spacer_project_evidence.json
```

With the newly checked Alpine Project metadata added to an in-memory copy,
spacer no longer triggers the hold. Four false passes still go to `NEEDS_REVIEW`;
three other agreeing candidates require added review. Total added reviews are
seven, and total `NEEDS_REVIEW` verdicts are eight. Label agreement is still 10/16.
This changed the evidence supplied to the guard. It is not the frozen-fact result,
a model rerun, or evidence of generalisation. No case-name whitelist is used.

Unseen synthetic tests cover exact upstream matches, same-name different projects,
missing evidence, conflicting metadata, spoofed hosts, and another competitor
remaining after an own-package exclusion. These test logic, not held-out model
quality. Production enablement still requires an unseen labelled set and an
explicit review-workload decision.

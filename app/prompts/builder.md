# Builder

Job: list the verification steps that cite rows already in EVIDENCE_JSON. Do not claim a command passed unless the row says so.

Cite evidence ids, or write unknown.

## Rubric

- Every step names a fact from a cited row.
- A step with an empty `evidence_ids` list is unsupported.
- Do not report VALIDATION_PASS without a cited row.

## JSON schema

```json
{"steps":["..."],"evidence_ids":["EV-..."]}
```

## Positive example

42wim/matterbridge revival row. The policy result is on the cited profile.

```json
{"steps":["Record the policy result for 42wim/matterbridge from the cited profile row."],"evidence_ids":["EV-PROFILE-42wim-matterbridge"]}
```

## Negative example

Do not emit this.

```json
{"verification_result":"PASS","steps":["VALIDATION_PASS without a cited row"],"evidence_ids":[]}
```

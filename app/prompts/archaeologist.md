# Archaeologist

Job: turn one Zetaris repository row into claims. Each claim states one fact the row already shows.

Cite an evidence id from EVIDENCE_JSON, or write unknown. Do not infer demand, users, stars, or a licence from a badge.

## Rubric

Score only what the row shows. Each score is 0, 1, or 2 and names the field it used.

- problem clarity: the description or README states a concrete problem
- target user: a person or group is named in that text
- scope: the text says what the software does
- feasibility from existing code: `code_paths` or the profile row shows source files beyond a template

## JSON schema

```json
{"claims":[{"id":"CLM-...","statement":"...","evidence_ids":["EV-..."],"confidence":0.0}]}
```

## Positive example

Repository: SergioMendolia/ytkb. The discovery row has `licence_spdx` empty and `days_since_push` 205.

```json
{"claims":[{"id":"CLM-LICENCE","statement":"licence_spdx is empty.","evidence_ids":["EV-DISCOVERY-SergioMendolia-ytkb"],"confidence":0.9},{"id":"CLM-STALL","statement":"days_since_push is 205.","evidence_ids":["EV-DISCOVERY-SergioMendolia-ytkb"],"confidence":0.9}]}
```

## Negative example

Do not emit this. tharani-2006/Invoice-generator-application has an empty `licence_spdx`. The README badge text `license-MIT` is not an SPDX id.

```json
{"claims":[{"id":"CLM-LICENCE","statement":"The licence is MIT.","evidence_ids":[],"confidence":0.9}]}
```

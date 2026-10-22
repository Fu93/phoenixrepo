# Planner

Job: return the deterministic PRD already in EVIDENCE_JSON. Do not add stages, versions, or issues that are not in that document.

Cite evidence ids, or write unknown.

## Rubric

- Revival plans have stages revive, refactor, and upgrade.
- A discovery CANDIDATE may add productise. ALREADY_EXISTS does not get a plan.
- Every version, count, and URL in a finding is already in the snapshot.

## JSON schema

```json
{"findings":["..."],"stages":[{"id":"revive"}]}
```

## Positive example

SergioMendolia/ytkb discovery plan. Stage ids are revive, refactor, upgrade, productise. `licence_spdx` is empty, so productise includes adding an SPDX id. The plan does not fork the repository.

```json
{"findings":["licence_spdx is empty on SergioMendolia/ytkb."],"stages":[{"id":"revive"},{"id":"refactor"},{"id":"upgrade"},{"id":"productise"}]}
```

## Negative example

Do not emit this. tharani-2006/Invoice-generator-application is ALREADY_EXISTS because of al1abb/invoify. It does not get a revival plan.

```json
{"findings":["Fork the invoice repository and replace invoify."],"stages":[{"id":"revive"},{"id":"rewrite"}]}
```

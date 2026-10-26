# Architect

Job: write the revival contract that the cited rows already allow. One contract. No new features.

Cite evidence ids, or write unknown.

## Rubric

- The contract names the repository id from the row.
- It repeats licence, tests, and CI only when those fields are on the row.
- It does not promise a rewrite in a language the row does not name.

## JSON schema

```json
{"contract":"...","evidence_ids":["EV-..."]}
```

## Positive example

jondot/hygen on the revival row: licence MIT, the stage-1 plan does not fork again inside this contract.

```json
{"contract":"Revive jondot/hygen only if the cited rows still show MIT, tests, and the recorded CI facts. Do not add a successor.","evidence_ids":["EV-PROFILE-jondot-hygen"]}
```

## Negative example

Do not emit this. SergioMendolia/ytkb has an empty `licence_spdx`.

```json
{"contract":"Relicense ytkb to GPL-3.0 and rewrite it in Rust.","evidence_ids":[]}
```

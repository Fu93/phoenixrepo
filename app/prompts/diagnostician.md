# Diagnostician

Job: say which Zetaris view to read again after a validation failure. Do not invent a passing CI run.

Cite evidence ids, or write unknown.

## Rubric

- `action` names a view that this investigation already uses.
- `claims` stay inside the validation reason and the cited rows.
- `decision` repeats the current verdict. It does not upgrade it.

## JSON schema

```json
{"action":"re-query phoenix.revival_signals","reason":"...","claims":[],"decision":"RESURRECTION_FAILED","evidence_ids":["EV-..."]}
```

## Positive example

jondot/hygen stage-1 CI failed at the Node 14 toolchain step. That is the recorded reason. It is not a test failure.

```json
{"action":"re-query phoenix.revival_signals","reason":"CI decayed at Use Node.js 14.x.","claims":[{"id":"CLM-CI","statement":"The recorded CI failure is the toolchain step, not a test assertion.","evidence_ids":["EV-PROFILE-jondot-hygen"]}],"decision":"RESURRECTION_FAILED","evidence_ids":["EV-PROFILE-jondot-hygen"]}
```

## Negative example

Do not emit this. The hygen snapshot does not show a green stage-1 run on the upstream repository.

```json
{"action":"mark CI green","reason":"The tests probably pass now.","claims":[],"decision":"RESURRECTED","evidence_ids":[]}
```

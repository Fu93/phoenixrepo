# n8n live evidence

One live call through the gate-1 ToolBox to the n8n workflow n8n/phoenix_evidence_webhook.json, sealed trace verifiable with scripts/verify_trace.py. Gate 1's search does not call n8n. No model call, no verdict. The workflow imported for this run differs from n8n/phoenix_evidence_webhook.json only in the Respond to Webhook body expression and the added workflow id; the modified copy is included as workflow_as_run.json.

Files:

- `run_n8n_check.py`: run from the repo root with `N8N_WEBHOOK_URL` set; builds the gate-1 ToolBox, makes one n8n call, seals the trace.
- `trace-n8n-live-20261027T225539Z.jsonl`: the sealed hash-chained trace (TOOL_CALL n8n, RUN_SEAL).
- `RESULT-n8n-live-20261027T225539Z.json`: summary of the run.
- `workflow_as_run.json`: the exact workflow JSON imported into n8n for this run.

Verify:

    python scripts/verify_trace.py docs/evidence/n8n-live/trace-n8n-live-20261027T225539Z.jsonl --expected-run-hash 7037c3a4dbeb53a66691ee4ee9acc5b34046a350f8547175247c8c98a4faf81c
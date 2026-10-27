# Replay page hash check

`/ui` and `scripts/export_static_ui.py` both call `app.replay_ui.replay_payload`. When `data/replays/<id>/record.json` has a non-empty `recorded_run.run_hash`, that string is passed to `scripts.verify_trace.verify` as the expected run hash.

A match is a pass. A trace that is edited and re-sealed has a new run hash; the page shows a failure, not a pass. When `recorded_run` is missing or `run_hash` is empty, the page says `no recorded hash to compare` and does not show a pass. A broken chain on that file is included in the same line.

`invoice-already-exists` and `invoice-gate2-sample` store a venture run hash and `jsonl_committed: false`. Their SAMPLE seals are different hashes, so this check fails for those two. `hygen-already-exists-quote` and `mirrormate-human-label` have `recorded_run: null`, so the page says there is no recorded hash to compare.

`scripts/export_static_ui.py` embeds `exported_at` (UTC, second resolution) and `source_commit`. `source_commit` is the `GITHUB_SHA` environment variable when that is set and non-empty, otherwise the output of `git rev-parse HEAD`, otherwise the string `unknown`. The static page shows both next to the verification result.

When a commit is named, the run hash check is anchored to the commit the page was exported from. It does not defend against the author rewriting history. When the commit is `unknown`, the page says so and does not claim that anchor.

# Replay page hash check

`/ui` and `scripts/export_static_ui.py` both call `app.replay_ui.replay_payload`. The `trace_class` on the replay's row in `data/replays/manifest.json` decides the check.

When that class is RECORDED and `data/replays/<id>/record.json` has a non-empty `recorded_run.run_hash`, that string is passed to `scripts.verify_trace.verify` as the expected run hash. A match is a pass. A RECORDED trace that is edited and re-sealed has a new run hash; the page shows a failure, not a pass. A tampered RECORDED trace fails the chain. When a RECORDED replay has no `recorded_run.run_hash`, the page says `no recorded hash to compare` and does not show a pass. A broken chain on that file is included in the same line.

When the manifest `trace_class` is SAMPLE, the page does not compare `recorded_run.run_hash`. It shows `sample, not a recorded run: hash not compared`. That line is not a pass and not a failure. A SAMPLE replay is a reconstruction sealed over committed files, not a trace copied from a run. `invoice-already-exists`, `invoice-gate2-sample`, `hygen-already-exists-quote`, and `mirrormate-human-label` are SAMPLE in the manifest, so they show that line. The two invoice replays store a venture run hash that is not the SAMPLE seal; the page does not show that difference as a failure.

`scripts/export_static_ui.py` embeds `exported_at` (UTC, second resolution) and `source_commit`. `source_commit` is the `GITHUB_SHA` environment variable when that is set and non-empty, otherwise the output of `git rev-parse HEAD`, otherwise the string `unknown`. The static page shows both next to the verification result.

When a commit is named, the run hash check is anchored to the commit the page was exported from. It does not defend against the author rewriting history. When the commit is `unknown`, the page says so and does not claim that anchor.

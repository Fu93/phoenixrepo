# Nightshift probe

`scripts/nightshift_probe.py` asks this machine's Nightshift which `nvidia/nemotron-*` ids it lists, calls each requested id once per variant, and prints the HTTP status, latency, and served model. It does not change configuration.

It needs `METERLESS_RUNTIME_TOKEN` in the environment or in `.env`. The script does not print that token, a request or response header, or an account id. Do not paste the token into a commit, a screenshot, or this file.

## Run

From the repository root, with the token already in the environment:

```bash
python scripts/nightshift_probe.py
python scripts/nightshift_probe.py --quick
python scripts/nightshift_probe.py --redact --out docs/probes/nightshift-probe.redacted.json
```

`--quick` uses one variant per id. The default run tries `default`, `thinking_off_template`, and `reasoning_none`.

Without `--redact` the script writes `logs/nightshift_probe-<utc>.json`. `logs/` is gitignored. That file can contain Nightshift routing headers. Do not commit it.

`--redact` writes the same measurements without the routing block, without the response body, and without userinfo or a query string on the base URL. That is the file to commit.

## Where the committed output goes

No redacted probe output is in this repository yet. When a run has been made, commit the file written by `--redact` at:

`docs/probes/nightshift-probe.redacted.json`

Do not invent that file. A placeholder with made-up model ids would be a false measurement. Until that command has been run on a machine that holds the token, this path stays absent.

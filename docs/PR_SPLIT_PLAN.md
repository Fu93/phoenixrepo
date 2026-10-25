# PR #6 split plan: land the code, keep the run artifacts out of main

Status: a plan only. Nothing here has been executed. No branch was created and no pull request was opened for it.

## Facts (read from git and `gh pr view 6`, 25 Oct 2026)

- PR #6 is `cursor/idea-filter-13a8` (GitHub `Fu93/phoenixrepo`). Its **base is `cursor/hygen-upgrade-prd-13a8`, not `main`** (`gh pr view 6` reports `baseRefName: cursor/hygen-upgrade-prd-13a8`).
- `origin/cursor/hygen-upgrade-prd-13a8` is `6fc8c49`. It is one commit ahead of `origin/main`. `git merge-base HEAD origin/main` is `7f06509` (merge of PR #3). The branch carries 17 files (+4,847 lines) that `main` does not have: the hygen upgrade planner, `data/upgrade/**` (11 files, about 553 KB) and its docs. Those belong to the base branch's own PR, not to PR #6.
- The PR #6 branch is 31 commits ahead of its base, and 0 behind, before the docs commits of this task.

## Diff size

| Compared with | Files changed | Lines |
| --- | --- | --- |
| PR base `origin/cursor/hygen-upgrade-prd-13a8` | 116 | +19,521 / -96 |
| `origin/main` (merge-base `7f06509`) | 133 | +24,368 / -96 |

All figures are `git diff --shortstat` before the two docs files of this task were added.

## (a) Code, tests, docs and small data fixtures: what the PR should land

Changed paths against the PR base, with the size of each group at the PR head (`git cat-file -s`):

| Group | Files | Lines added | Bytes at head |
| --- | --- | --- | --- |
| `app/` (gate 1, revive loop, provider, relevance, planners) | 36 | +6,512 | 343,953 |
| `scripts/` (judge_repo, revive, run_sharded, score_candidates, render_trace, ...) | 15 | +4,630 | 217,572 |
| `tests/` | 35 | +5,723 | 298,972 |
| `docs/` | 8 | +950 | 202,665 |
| `README.md`, `.gitignore`, `api/`, `config/`, `state/`, `n8n/` | 6 | +118 / -8 | 61,039 (README at the pre-task size) |
| `output_examples/` (3 regenerated JSON files) | 3 | +3 / -3 | 86,046 |
| `data/eval/` (`cases.json`, `queries.json`: offline eval fixtures) | 2 | +946 | 61,030 |
| `data/real/` (committed five-repo and discovery snapshot that tests read) | 7 | +96 / -26 | 18,756 |
| `data/discovery/raw/snapshot.json` (committed two-repo idea-filter snapshot) | 1 | +540 | 22,753 |
| `data/sample/` (header-only CSVs) | 3 | +3 | 512 |

These are small, reviewable, and read by tests or documented in the README. No file in the diff was deleted (only modified or added).

## (b) Run artifacts that must not go to `main`

None of these is part of the PR diff today. They are **untracked or gitignored** in the working copy. The risk is a careless `git add -A`, a force-add, or a later "add the results" commit. Sizes measured on 25 Oct 2026 around 19:20 (UK time) while the `curated30d` batch was still running, so they will grow.

| Path | State in git | Size when measured |
| --- | --- | --- |
| `data/discovery/2026-10-22/` | untracked | 324 files, 14,588,352 bytes |
| `data/discovery/curated30/` | untracked | 7 files, 14,096,232 bytes |
| `data/discovery/trial-5/` | untracked | 178 files, 9,947,325 bytes |
| `data/discovery/_shards/` | gitignored | 6,872 files, 371,886,326 bytes |
| `reports/` (gate-1 HTML reports, `revive.json` trials) | untracked | 24 files, 76,730 bytes |
| `logs/` (traces, caches of runs, `*_scores.md`) | gitignored | 1,001 files, 50,758,680 bytes |
| `work/` (clones made by `scripts/revive.py`) | gitignored | 60,018 files, 1,390,372,719 bytes |
| `.cache/` | gitignored | not measured |

Reasoning:

- `work/` holds clones and `node_modules` of other people's repositories. It must never be committed.
- The `data/discovery/*` snapshots are tens of MB of raw GitHub and search payloads. They are reproducible from the scripts, and the committed small snapshot (`data/discovery/raw/snapshot.json`) is enough for the tests.
- `logs/` and `reports/` are evidence for the demo. Keep them locally (or as a release asset or a separate `evidence` branch if a reviewer must see them). The hash-chained traces can be verified with `scripts/verify_trace.py` wherever they are stored.
- `.gitignore` already covers `logs/`, `work/`, `.cache/`, `data/discovery/_shards/`. It does **not** cover `reports/` or the three untracked `data/discovery/<run>/` folders. A one-line addition (see the optional step) closes that gap.

## How to land only (a)

Because the artifacts are not tracked, PR #6 as it stands already contains only group (a). The plan below produces the same content as a clean, explicitly path-limited branch. Use it if you want a guarantee that no artifact slips in, or if the PR branch will receive more commits that might add artifacts.

Pre-checks (read only):

```powershell
git fetch origin
git status --short                       # expect only the untracked artifact folders listed above
git diff --name-only origin/cursor/hygen-upgrade-prd-13a8 HEAD |
  Select-String -Pattern '^(logs|reports|work)/|^data/discovery/(20|curated|trial|_shards)'   # expect no output
```

Option 1 (recommended): keep PR #6 on its real base, then retarget in order.

1. Merge the base branch's own PR first (`cursor/hygen-upgrade-prd-13a8` into `main`). That brings the 17 hygen-upgrade files.
2. Retarget PR #6 to `main` in the GitHub UI. The diff then shrinks from 133 to 116 files.
3. Before merging, repeat the pre-check above against `origin/main`.

Option 2: a clean landing branch with only the (a) paths, from the PR base.

```powershell
git fetch origin
git checkout -b landing/idea-filter-code origin/cursor/hygen-upgrade-prd-13a8

# take the (a) paths from the PR branch
git checkout cursor/idea-filter-13a8 -- `
  README.md .gitignore `
  api app config state n8n output_examples scripts tests docs `
  data/eval data/real data/sample data/discovery/raw/snapshot.json

# nothing deleted in the PR, so no `git rm` is needed.
# `git checkout <branch> -- <dir>` does not delete files that exist only on the base,
# which is correct here because the PR deletes no file.

# verify: same content as the PR for (a), and no artifacts
git diff --stat cursor/idea-filter-13a8 -- README.md app scripts tests docs api config state n8n output_examples data/eval data/real data/sample data/discovery/raw   # expect empty
git diff --name-only origin/cursor/hygen-upgrade-prd-13a8 HEAD |
  Select-String -Pattern '^(logs|reports|work)/|^data/discovery/(20|curated|trial|_shards)'   # expect no output
python -m pytest -q

git commit -m "land idea filter, gate 1, revive loop, tests and docs (code only, no run artifacts)"
git push -u origin landing/idea-filter-code
```

Then open a pull request from `landing/idea-filter-code` (the user does this; this plan does not). Base it on `cursor/hygen-upgrade-prd-13a8` first, or on `main` once the base branch is merged. The history of 31+ commits is flattened into one commit by this route; if the per-commit history matters, use Option 1.

Option 2b: the same, from `origin/main`. This must also take the 17 hygen-upgrade files from the base branch:

```powershell
git checkout -b landing/idea-filter-code-main origin/main
git checkout origin/cursor/hygen-upgrade-prd-13a8 -- app/agents/upgrade_planner.py data/upgrade scripts/collect_hygen_upgrade.py scripts/write_upgrade_prd.py tests/agents/test_upgrade_planner.py docs/revival/jondot__hygen.upgrade.prd.json docs/revival/jondot__hygen.upgrade.prd.md
# then the same `git checkout cursor/idea-filter-13a8 -- ...` line as in Option 2
```

Optional hardening (a one-line docs/config change, also not executed): append `reports/` and `data/discovery/20*/`, `data/discovery/curated*/`, `data/discovery/trial-*/` to `.gitignore`.

## What not to do

- Do not run `git add -A` or `git add .` in the working copy while the artifact folders are untracked.
- Do not force-push or rewrite the PR branch.
- Do not stop, restart or modify files under `logs/`, `data/discovery/`, `reports/`, `work/` while the batch is running. Everything above only reads them.
- Merging is a separate decision for the owner.

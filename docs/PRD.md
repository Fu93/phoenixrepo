# PhoenixRepo product direction

PhoenixRepo rescues a good idea that was abandoned as a GitHub project and turns it into a product only after a filter. Abandoned idea repositories usually have about zero stars and no followers, because nobody could use a project that was never finished. Those counts are not evidence that the idea is bad, and they are not evidence that anyone wants it.

The filter has three gates. Each one cites text that was fetched, not a score invented for the repository.

1. **Idea.** The README or description states a problem, a target user, and a scope. The tree has real code beyond a template. The last push is at least 180 days before the collection date. A missing SPDX id is recorded and does not, by itself, reject an unfinished idea.
2. **Prior art.** A model may list core capabilities and mark each one covered, partial, or missing, with a verbatim quote. The guard keeps a quote only when it is an exact substring of stored fetched text whose sha256 matches. `ALREADY_EXISTS` requires one maintained free OSI alternative to cover every capability. A gap list is `CANDIDATE` only when external demand also passes; otherwise it is `NO_GO`. With no verified mapping, the labelled fallback is phrase/keyword matching: every required phrase must appear in both excerpts. An alternative that is abandoned, has no free OSI licence, or does not cover a capability does not count.
3. **External demand.** Demand is for the problem, measured outside the candidate: an issue or pull request on another project, a maintained popular repository about the same problem, or package-registry downloads of a package that is actually about the problem. A GitHub search `total_count` is stored and is not a pass. The candidate's own stars, forks, and issues are never the demand signal.

Only a concrete idea with external demand and no adequate free maintained alternative is a `CANDIDATE`. That result is a plan. This run does not fork it or commit to it. `NO_GO` means the README does not state the idea, the repository is not stalled, the tree is still a template, or external demand was not shown.

## Sample collected 22 Oct 2026

Collection time `2026-10-22T19:11:28+00:00`. No token was written. Days are the date difference from `pushed_at` to that collection date.

| Repository | Stars | Verdict | Why |
| --- | --- | --- | --- |
| [tharani-2006/Invoice-generator-application](https://github.com/tharani-2006/Invoice-generator-application) | 0 | `ALREADY_EXISTS` | Stalled since `2026-02-28` (236 days). Description: "Full-stack invoice generator with real-time previews, PDF export...". The API licence is empty. The README badge `license-MIT` is badge text, not `licence_spdx`. [al1abb/invoify](https://github.com/al1abb/invoify) is MIT, 6,376 stars, pushed `2026-08-24` (59 days), and its README says it is "a free, web-based invoice generator" and "get a real PDF." |
| [SergioMendolia/ytkb](https://github.com/SergioMendolia/ytkb) | 0 | `CANDIDATE` | Stalled since `2026-03-31` (205 days). Real Go files include `main.go` and `cmd/push.go`. Core phrases: "knowledge base" and "push changes back". Plan only: `docs/revival/SergioMendolia__ytkb.prd.md`. |
| [jondot/hygen](https://github.com/jondot/hygen) | 5,927 | revival-set, not a discovery candidate | Once-popular code generator, not a 0-star idea repository. The idea filter does not replace its revival verdict. |

### ytkb demand and the alternatives that do not cover it

[foliant-docs/foliant#80](https://github.com/foliant-docs/foliant/pull/80) is open (`merged_at` null) with 2 comments and 2 reactions. The body says it "fetches issues and knowledge base articles from a YouTrack instance" and generates markdown. That is external demand for the problem. It does not contain "push changes back", and the Foliant repository itself was last pushed `2026-03-29` (207 days), so it is not a maintained product that covers the cited need.

The issue search `"knowledge base" youtrack markdown is:issue` returned `total_count` 10. The repository search `youtrack markdown knowledge in:description stars:>20 pushed:>2026-04-22` returned `total_count` 0. Both numbers are that query's total. A zero is not proof that no tool exists outside the query.

Recorded alternatives that do not qualify: [foliant-docs/foliant](https://github.com/foliant-docs/foliant) (not inside the 180-day window, and the cited text does not mention YouTrack), [billpeet/yt-cli](https://github.com/billpeet/yt-cli) (no SPDX id and neither core phrase), [NapalmDeath/youtrack-outline-importer](https://github.com/NapalmDeath/youtrack-outline-importer) (abandoned, no SPDX id, "KnowledgeBase" is not the phrase "knowledge base"), [udamir/youtrack-markdown-snippet](https://github.com/udamir/youtrack-markdown-snippet), and [siporoz/youtrack-ai-context-exporter](https://github.com/siporoz/youtrack-ai-context-exporter). None of those excerpts contains both required phrases.

The ytkb plan keeps `go build -o ytkb` and `go install`, asks a human to confirm an SPDX id because `licence_spdx` is empty, and states the README features. It does not schedule the unchecked roadmap lines "Manage images and attachments" and "Allow page creation from new md files".

### hygen stays the revival showcase

hygen's description is "The simple, fast, and scalable code generator that lives in your project." On 22 Oct 2026 the GitHub payload reported 5927 stars, 275 forks, 102 open issues, licence MIT, and last push `2024-07-09`. That is a once-popular tool, not a 0-star idea repository.

The prior-art rows cite [plopjs/plop](https://github.com/plopjs/plop), [yeoman/yo](https://github.com/yeoman/yo), and [cookiecutter/cookiecutter](https://github.com/cookiecutter/cookiecutter). Their short cited descriptions do not contain hygen's required phrase "injecting into existing files". cookiecutter's last push `2026-04-01` is also outside the 180-day window. The repository search `code generator in:description inject template stars:>1000 pushed:>2025-10-01` returned `total_count` 0. That is not a census of generators. The phrase fallback therefore does not return `ALREADY_EXISTS`, and the revival verdict is unchanged.

The labelled eval set in `data/eval/cases.json` is a separate judgement. It includes a longer plop excerpt. Plop's README says the modify action can "find/replace text in the file located at the `path` specified" and that append "is used to append data in a file at a particular location." The human label for that eval case is `ALREADY_EXISTS`. The phrase method on the same fixture returns `NO_GO`. That disagreement is one of the three misses in the deterministic score below. It does not change the revival verdict.

## Eval, 22 Oct 2026

`python scripts/eval_ideas.py` scored the phrase/keyword fallback against 12 labelled repositories: 9 correct, accuracy 0.75. Queries are in `data/eval/queries.json` (37 recorded, including npm totals and PyPI searches that returned a client-challenge page or the XML-RPC fault, not a package count). `python scripts/eval_ideas_live.py` is the Nightshift runner. It was not executed in this environment, so no live accuracy is reported.

The stage-1 snapshot still says the fork was `ahead_by` 0 with no Actions runs. That sentence is about that collection. Later work on the fork is separate: [Fu93/hygen#1](https://github.com/Fu93/hygen/pull/1) is an open draft, not merged, head `807e36152abb6563717c1b2f480084f3d8bb736c`, title "Revive hygen: CI, security pins, Windows tests, and generator fixes". Commits U1 through U7 are on that branch. U8 was not committed.

The workflow run [39828539248](https://github.com/Fu93/hygen/actions/runs/39828539248) for U7 (`807e361`) had status `in_progress` and its conclusion is null at collection. The four jobs had already concluded `success`: `build (20.x, ubuntu-latest)`, `build (22.x, ubuntu-latest)`, `build (20.x, windows-latest)`, and `build (22.x, windows-latest)`. This document does not call the run green.

## What remains unverified

- Whether run 39828539248 has since received a conclusion. At `2026-10-22T19:11:28Z` the conclusion is null.
- Whether a YouTrack tool outside the recorded queries covers "push changes back". The queries are the evidence. They are not a proof of absence.
- `load_real.ps1` was not executed against a running Zetaris.
- The ytkb document is a plan. Nothing was forked or committed for that repository.
- A README licence badge is not an SPDX id.

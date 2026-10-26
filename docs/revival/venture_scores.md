# Venture Scorecard & Protagonist Selection

- **Date**: 2026-10-22T22:09:00.336503+00:00
- **Run Hash**: `322ce42a3527a7c4a719853e61b5c2842833653fb5384f0726ed83856d475551`
- **Command**: `scripts\score_candidates.py`
- **LLM Evaluator**: Deterministic Baseline Analyst

## Protagonist Selection

- **Eligible Candidates**: 1
- **Note**: selection is trivial: only one eligible candidate.

**Selected Protagonist**: [`SergioMendolia/ytkb`](https://github.com/SergioMendolia/ytkb)
- **Weighted Total**: 48.33/100
- **Feasibility Score**: 2/3
- **Selection Reason**: Selected protagonist SergioMendolia/ytkb with highest weighted total 48.33 (feasibility 2).

## Eligible Candidates Ranking

| Rank | Repository | Total | Feasibility | Problem | Demand | Diff | Feas | Prod | Moat | Flags |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | [`SergioMendolia/ytkb`](https://github.com/SergioMendolia/ytkb) | **48.33** | 2 | 2 | 1 | 2 | 2 | 1 | 0 | `None` |

## Candidate Criteria Breakdown

### SergioMendolia/ytkb (Total: 48.33/100)

- **problem_severity**: 2/3 (SUPPORTED)
  - *Rationale*: Stated struggling job in description/README: 'A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.'. Meaningful recurring workflow friction.
  - *Evidence IDs*: `candidate:SergioMendolia/ytkb`
- **demand_evidence**: 1/3 (SUPPORTED)
  - *Rationale*: Stored qualifying external demand signal from https://github.com/foliant-docs/foliant/pull/80 (foliant-pr-80): "preprocessor (`youtrack`) that fetches issues and knowledge base articles from a YouTrack instance via the REST API and generates markdown files for inclusion in Foliant projects.".
  - *Evidence IDs*: `demand:foliant-pr-80`
- **differentiation**: 2/3 (SUPPORTED)
  - *Rationale*: Score 2: Missing capabilities ('knowledge base', 'push changes back') not covered by any maintained free OSS alternative. Alternatives checked: udamir/youtrack-markdown-snippet (maintained free MIT, 78d; lacks all missing capabilities); siporoz/youtrack-ai-context-exporter (maintained free MIT, 169d; lacks all missing capabilities); foliant-docs/foliant (unmaintained 207d); billpeet/yt-cli (licence=no licence); NapalmDeath/youtrack-outline-importer (licence=no licence, unmaintained 837d). Candidate quotes: for 'knowledge base': "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online."; for 'push changes back': "- **Push**: Push changes back to YouTrack (update only, no creation)".
  - *Evidence IDs*: `candidate:SergioMendolia/ytkb, prior:foliant-docs/foliant, prior:billpeet/yt-cli, prior:NapalmDeath/youtrack-outline-importer, prior:udamir/youtrack-markdown-snippet, prior:siporoz/youtrack-ai-context-exporter`
- **feasibility**: 2/3 (SUPPORTED)
  - *Rationale*: Structured codebase with 6 non-boilerplate source paths (main.go, cmd/push.go, cmd/download.go). Solid working baseline.
  - *Evidence IDs*: `candidate:SergioMendolia/ytkb`
- **productization_path**: 1/3 (SUPPORTED)
  - *Rationale*: Identifiable user persona ('edit them in your editor of choice') for open-source utility; commercial SaaS/tier monetization is nascent.
  - *Evidence IDs*: `candidate:SergioMendolia/ytkb`
- **moat**: 0/3 (SUPPORTED)
  - *Rationale*: Score 0: No structural power from Hamilton Helmer's 7 Powers (scale economies, network economies, counter-positioning, switching costs, branding, cornered resource, process power). Open-source utility with zero barriers to duplication.
  - *Evidence IDs*: `none`

## Ineligible Repositories

| Repository | Verdict | Reason |
| :--- | :---: | :--- |
| [`tharani-2006/Invoice-generator-application`](https://github.com/tharani-2006/Invoice-generator-application) | `ALREADY_EXISTS` | al1abb/invoify (https://github.com/al1abb/invoify) is a maintained free OSS alternative (MIT, pushed 2026-08-24T02:55:34Z, 59 days before collection) whose excerpt contains every required term (invoice, pdf). Excerpt: Invoify is a free, web-based invoice generator built with Next.js, TypeScript, React and the shadcn/ui library. Fill in a form, pick a template, and get a real PDF. Method: phrase/keyword. |

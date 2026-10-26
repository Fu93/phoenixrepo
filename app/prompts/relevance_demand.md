# Relevance Demand Judge

Job: Judge whether an external user signal (issue, discussion, or forum post) expresses real user demand or struggle for one of the candidate's extracted capabilities.

If unsure, relevant=false.
If the signal text does not express real demand or the quote is missing, write unknown and return relevant=false.

The guard will discard any quote that is not an exact verbatim substring of the signal text.
The guard will reject signals from PRs, code files, repository root pages, or the candidate's own repository.

## Rubric

- `relevant`: true only if the signal text clearly shows a user wanting, needing, or struggling with the specific capability.
- `capability`: must match one of the candidate's extracted capability names.
- `quote`: must be an exact verbatim substring from the signal text.
- `why`: one concise sentence explaining why this signal represents demand.

## JSON schema

```json
{
  "relevant": true,
  "capability": "Local note synchronization",
  "quote": "exact words quoted from the signal text",
  "why": "User is asking how to synchronize markdown notes across devices without third-party cloud."
}
```

## Positive example

SergioMendolia/ytkb demand signal from foliant-docs/foliant:

```json
{
  "relevant": true,
  "capability": "YouTrack markdown sync",
  "quote": "need a way to sync articles from YouTrack knowledge base into local markdown",
  "why": "The user is directly asking for a tool to synchronize YouTrack knowledge base articles locally."
}
```

## Negative example

Do not emit this. An unrelated issue or generic discussion is not demand for the candidate's capabilities.

```json
{
  "relevant": false,
  "capability": "unknown",
  "quote": "",
  "why": "The issue is a build script error unrelated to note synchronization."
}
```

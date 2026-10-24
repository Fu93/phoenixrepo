# Relevance Discovery Judge

Job: Judge whether an abandoned candidate repository is a viable product idea (CANDIDATE), already exists as a maintained free open-source product (ALREADY_EXISTS), or is a non-product / non-viable toy / tutorial (NO_GO).

If unsure, verdict=NO_GO.
If the text does not state a clear user problem or quote is missing, write unknown and return NO_GO.

The guard will discard any quote that is not an exact verbatim substring of the stored text.
Never judge quality by candidate repository stars or forks.

## Rubric

- `verdict`: "CANDIDATE" if the repository solves a concrete user problem and is not completely solved by an existing maintained free alternative; "ALREADY_EXISTS" if an existing maintained free alternative already covers it; "NO_GO" if it is a tutorial, homework, library stub, or lacks real product scope.
- `reason`: One concise sentence explaining the verdict.
- `problem_quote`: Verbatim quote from candidate README or description stating the problem or core capability.
- `alternative_repo`: For ALREADY_EXISTS, the repository ID of the alternative from prior art (or null).
- `alternative_quote`: For ALREADY_EXISTS, verbatim quote from that alternative's text demonstrating it already solves this problem (or null).

## JSON schema

```json
{
  "verdict": "CANDIDATE",
  "reason": "Solves a real problem not solved by maintained alternatives.",
  "problem_quote": "exact words quoted from candidate README",
  "alternative_repo": null,
  "alternative_quote": null
}
```

## Positive example

SergioMendolia/ytkb:

```json
{
  "verdict": "CANDIDATE",
  "reason": "A CLI tool for syncing YouTrack knowledge base articles to local markdown with bi-directional push.",
  "problem_quote": "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.",
  "alternative_repo": null,
  "alternative_quote": null
}
```

## Negative example

Do not emit this. If a quote is not in the text, write unknown rather than inventing text.

```json
{
  "verdict": "NO_GO",
  "reason": "unknown non-product tutorial",
  "problem_quote": "unknown non-existent text",
  "alternative_repo": null,
  "alternative_quote": null
}
```


## Substitution rule (overrides feature-by-feature comparison)
Judge by the user's core job, not by feature lists. Ask: "If this repository disappeared, could its target user do the same core job with the alternative, with at most minor inconvenience?" If yes, the overlap is "full" even when the alternative lacks some secondary features (diff view, tree view, stars, cookies, offline mode, UI polish) or combines them differently. A unique combination of secondary features is NOT novelty. Only a capability the core job cannot be done without, and that no alternative offers, counts as differentiation.
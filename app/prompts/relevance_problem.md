# Relevance Problem Extractor

Job: Extract a concise, precise problem statement, target user, product category, and problem-oriented search queries from a candidate repository's description and README.

The guard will discard any quote that is not an exact verbatim substring of the stored candidate text.
Never judge quality by candidate repository stars or forks.

## Rubric

- `problem`: One clear, concise sentence describing the user problem or struggling job being solved (Jobs-To-Be-Done framing).
- `target_user`: The specific user persona or audience who experiences this friction.
- `category`: Broad product category (e.g. developer-tools, note-taking, system-monitoring, productivity, automation).
- `search_queries`: 3 to 5 short, problem-oriented search queries (2 to 4 words each) to find other tools or prior art solving the same user problem. Do NOT use the candidate repository name, owner name, or implementation trivia.
- `quotes`: 1 to 3 verbatim quotes from the candidate README or description that directly support the problem statement.

## JSON schema

```json
{
  "problem": "One sentence describing the struggling job or friction.",
  "target_user": "Specific target persona.",
  "category": "developer-tools",
  "search_queries": [
    "query one",
    "query two",
    "query three"
  ],
  "quotes": [
    "exact verbatim words quoted from candidate README"
  ]
}
```

## Positive example

Repository: `SergioMendolia/ytkb`
Description: `A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.`

```json
{
  "problem": "Developers and technical writers cannot easily edit and sync YouTrack knowledge base articles offline in their local markdown editor.",
  "target_user": "Developers and technical writers using YouTrack knowledge base",
  "category": "developer-tools",
  "search_queries": [
    "youtrack knowledge base sync",
    "youtrack markdown export",
    "sync knowledge base markdown",
    "youtrack cli editor"
  ],
  "quotes": [
    "syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online"
  ]
}
```

## Negative example

Do not invent or paraphrase quotes. If text is missing, write "unknown". Any quote not found verbatim will cause immediate rejection.

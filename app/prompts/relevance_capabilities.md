# Relevance Capabilities Extractor

Job: Read the candidate repository README excerpt and extract up to 5 concrete user-facing capabilities, each with an exact quote from the README.

If the text does not state user-facing capabilities, write unknown and return an empty list.

The guard will discard any quote that is not an exact verbatim substring of the stored README text.
The guard will discard any capability name consisting only of repository name tokens, owner tokens, or generic words (code, config, https, python, main, install, source).

## Rubric

- Each capability must describe a distinct user-facing feature or action (what a user can do with the tool).
- Quote must be verbatim from the README text.
- Maximum 5 capabilities.

## JSON schema

```json
{
  "capabilities": [
    {
      "name": "short descriptive feature name",
      "quote": "exact words quoted from the README"
    }
  ]
}
```

## Positive example

Crazygrandma/NoteSync:

```json
{
  "capabilities": [
    {
      "name": "Local note synchronization",
      "quote": "Sync notes locally between different NoteSync Clients"
    },
    {
      "name": "Cross-platform desktop application",
      "quote": "built with Tauri and Vite"
    }
  ]
}
```

## Negative example

Do not emit this. "python main" and "code" are generic words or repo-name tokens, not user-facing capabilities, and the quote must not be invented.

```json
{
  "capabilities": [
    {
      "name": "code",
      "quote": "unknown python main"
    }
  ]
}
```

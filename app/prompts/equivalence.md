# Equivalence

Job: list the candidate's core capabilities, then map each one to each stored alternative as covered, partial, or missing.

Quote the candidate citation and the alternative excerpt verbatim from EVIDENCE_JSON. If the text is not there, write unknown and use status missing. Do not treat a near miss as covered.

The guard will discard any quote that is not an exact substring of the stored text for that evidence id.

## Rubric

Score the candidate idea 0, 1, or 2 with a citation:

- problem clarity
- target user
- scope
- feasibility from existing code

A capability is covered only when the alternative quote states that capability. partial means the quote is related and incomplete. missing means the stored text does not show it. One alternative must cover every capability. Stars are not a reason to cover or to skip.

## JSON schema

```json
{"capabilities":[{"id":"pdf","statement":"...","citation":"exact words","evidence_id":"readme:owner/name"}],"mappings":[{"capability_id":"pdf","alternative_url":"https://github.com/owner/name","status":"covered","quote":"exact words","evidence_id":"readme:owner/name"}]}
```

`status` is `covered`, `partial`, or `missing`. A missing cell uses an empty quote.

## Positive example

tharani-2006/Invoice-generator-application. Stored invoify text: "Fill in a form, pick a template, and get a real PDF."

```json
{"capabilities":[{"id":"invoice-pdf","statement":"Create an invoice and export a PDF.","citation":"PDF export","evidence_id":"readme:tharani-2006/Invoice-generator-application"}],"mappings":[{"capability_id":"invoice-pdf","alternative_url":"https://github.com/al1abb/invoify","status":"covered","quote":"get a real PDF","evidence_id":"readme:al1abb/invoify"}]}
```

## Negative example

Do not emit this. The foliant pull 80 body fetches YouTrack knowledge base articles into markdown. It does not say "push changes back". SergioMendolia/ytkb's push capability is not covered.

```json
{"capabilities":[{"id":"push","statement":"Push markdown edits back to YouTrack.","citation":"push them back online","evidence_id":"readme:SergioMendolia/ytkb"}],"mappings":[{"capability_id":"push","alternative_url":"https://github.com/foliant-docs/foliant","status":"covered","quote":"generates markdown files","evidence_id":"readme:foliant-docs/foliant"}]}
```

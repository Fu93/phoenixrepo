# Schema freeze

Schema 1.0 and pipeline 0.1.0 are frozen for the foundation.

Frozen field names are the ones already stored in evidence packs: `id`, `statement`, `relation`. They are not renamed to `claim_id`, `text`, or `relation_type`. A rename is a 2.0 change.

After 22 October 2026, fields may be added. Meaning may not change without a schema version bump. Claim status is not a repository decision.

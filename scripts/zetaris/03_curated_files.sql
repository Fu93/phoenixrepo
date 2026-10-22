-- Statements 3 and 4. Register the curated CSVs from storage Zetaris can read.
-- Zetaris cannot read a file that only exists on the laptop. Copy
-- data/live/curated_signals.example.csv and data/live/revival_signals.example.csv
-- to S3, Azure, GCS, or platform storage, fill one row whose repo_id is
-- owner/name (the same value GitHub calls full_name), then replace the paths.
-- Do not put storage keys in git. Header-only examples are templates, not facts.
--
-- days_since_push, tests, CI, and unique_capability are curated columns.
-- They are not single fields on the GitHub repository object. data_origin
-- should say where the row came from, for example github-rest-plus-curated.
-- Run each statement separately.

CREATE LIGHTNING DATABASE PHOENIX_FILES DESCRIBE BY "Curated PhoenixRepo evidence that GitHub's repo object does not carry";

-- Next statement:
-- CREATE LIGHTNING FILESTORE TABLE curated_signals FROM PHOENIX_FILES FORMAT CSV OPTIONS (
--   path "s3a://your-bucket/phoenix/curated_signals.csv",
--   header "true",
--   delimiter ","
-- );

-- Next statement:
-- CREATE LIGHTNING FILESTORE TABLE revival_signals_file FROM PHOENIX_FILES FORMAT CSV OPTIONS (
--   path "s3a://your-bucket/phoenix/revival_signals.csv",
--   header "true",
--   delimiter ","
-- );

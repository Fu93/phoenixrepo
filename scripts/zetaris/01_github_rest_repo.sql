-- PhoenixRepo live evidence, statement 1 of the GitHub profile.
-- Run each statement on its own in the Zetaris SQL Editor (http://localhost:3000)
-- or through POST /sql-editor/sqls/run. Do not send this file as one request.
--
-- Replace OWNER and REPO before running. This registers one public GitHub
-- repository object. GitHub's REST API terms apply. Unauthenticated calls are
-- rate-limited. Do not paste a token into a file you commit.
--
-- Source: https://docs.github.com/en/rest/repos/repos#get-a-repository
-- The response is one JSON object. Licence may be null; pick a repository
-- that has a licence object if this view returns an error on license.spdx_id.

CREATE LIGHTNING DATABASE GITHUB_REST DESCRIBE BY "Public GitHub repository objects for PhoenixRepo";

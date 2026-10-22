-- Statement 2. Replace OWNER and REPO. One statement.

CREATE LIGHTNING REST TABLE repo_facts FROM GITHUB_REST REQUEST(
 endpoint "https://api.github.com/repos/OWNER/REPO",
 method "get",
 response_type "json",
 http_encoding "URLENCODED"
) HEADER (
 Accept "application/vnd.github+json",
 User-Agent "phoenixrepo-hackathon"
) BODY ();

"""A repository's own package, docs, homepage, fork, and mirror are not alternatives.

Gate 1 was refusing phillipdupuis/pydantic-to-typescript because a web result was
``https://pypi.org/project/pydantic-to-typescript/``, the package that repository
publishes. The same rule covers npm, crates, a docs or homepage URL, a GitHub fork
of the repository, and a mirror that repeats its README.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

_MANIFEST_PATHS = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml")
_FORGES = {"github.com", "gitlab.com", "codeberg.org", "bitbucket.org"}
_README_MIN = 80


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().casefold())


def _norm_url(value: str) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("web:"):
        raw = raw[4:]
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/").lower()
    return host + path


@dataclass
class OwnIdentity:
    repo_id: str
    owner: str
    name: str
    packages: set[str] = field(default_factory=set)
    packages_pep: set[str] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)
    readme: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "owner": self.owner,
            "name": self.name,
            "packages": sorted(self.packages),
            "urls": sorted(self.urls),
        }


def _add_package(identity: OwnIdentity, name: str) -> None:
    text = str(name or "").strip()
    if not text or len(text) < 2 or " " in text:
        return
    identity.packages.add(text.casefold())
    identity.packages_pep.add(_pep503(text))


def _add_url(identity: OwnIdentity, url: str) -> None:
    norm = _norm_url(url)
    if not norm or norm in {"github.com", "pypi.org", "npmjs.com", "crates.io"}:
        return
    identity.urls.add(norm)


def _names_from_toml(text: str) -> tuple[list[str], list[str]]:
    names: list[str] = []
    urls: list[str] = []
    if tomllib is None:
        return names, urls
    try:
        data = tomllib.loads(text)
    except Exception:
        return names, urls
    if not isinstance(data, dict):
        return names, urls
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    poetry = {}
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    if isinstance(tool.get("poetry"), dict):
        poetry = tool["poetry"]
    cargo = data.get("package") if isinstance(data.get("package"), dict) else {}
    for block in (project, poetry, cargo):
        if block.get("name"):
            names.append(str(block["name"]))
        for key in ("homepage", "documentation", "repository", "readme"):
            if isinstance(block.get(key), str) and block[key].startswith("http"):
                urls.append(str(block[key]))
    project_urls = project.get("urls") if isinstance(project.get("urls"), dict) else {}
    poetry_urls = poetry.get("urls") if isinstance(poetry.get("urls"), dict) else {}
    for block in (project_urls, poetry_urls):
        for value in block.values():
            if isinstance(value, str) and value.startswith("http"):
                urls.append(value)
    return names, urls


def _names_from_package_json(text: str) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], []
    if not isinstance(data, dict):
        return [], []
    names = [str(data["name"])] if data.get("name") else []
    urls = []
    for key in ("homepage", "repository"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            urls.append(value)
        elif isinstance(value, dict) and str(value.get("url") or "").startswith("http"):
            urls.append(str(value["url"]))
    return names, urls


def _names_from_setup(text: str, kind: str) -> tuple[list[str], list[str]]:
    names = re.findall(r"(?im)^\s*name\s*=\s*['\"]([^'\"]+)['\"]", text)
    if kind == "setup.cfg":
        names += re.findall(r"(?im)^\s*name\s*=\s*([^\s#]+)\s*$", text)
    urls = re.findall(r"(?im)^\s*(?:url|homepage|home_page)\s*=\s*['\"]?(https?://[^\s'\"]+)", text)
    return names, urls


def apply_manifest(identity: OwnIdentity, filename: str, text: str) -> None:
    """Read package names and homepage or docs URLs from one manifest."""
    if not text:
        return
    low = filename.casefold()
    if low.endswith("pyproject.toml") or low.endswith("cargo.toml"):
        names, urls = _names_from_toml(text)
    elif low.endswith("package.json"):
        names, urls = _names_from_package_json(text)
    elif low.endswith("setup.py") or low.endswith("setup.cfg"):
        names, urls = _names_from_setup(text, "setup.cfg" if low.endswith("setup.cfg") else "setup.py")
    else:
        names, urls = [], []
    for name in names:
        _add_package(identity, name)
    for url in urls:
        _add_url(identity, url)


def build_identity(candidate: dict[str, Any], manifests: dict[str, str] | None = None) -> OwnIdentity:
    """Owner, repo name, declared package names, and the repository's own URLs."""
    repo_id = str(candidate.get("repo_id") or "").strip()
    owner, _, name = repo_id.partition("/")
    identity = OwnIdentity(repo_id=repo_id, owner=owner, name=name)
    if name:
        _add_package(identity, name)
    if owner and name:
        _add_url(identity, f"https://github.com/{owner}/{name}")
        _add_url(identity, f"https://{owner}.github.io/{name}")
    html = str(candidate.get("html_url") or "")
    if html:
        _add_url(identity, html)
    readme = str(candidate.get("readme_excerpt") or candidate.get("readme") or "")
    identity.readme = re.sub(r"\s+", " ", readme).strip().casefold()
    texts = dict(manifests or {})
    embedded = candidate.get("manifests") or candidate.get("_manifest_texts") or {}
    if isinstance(embedded, dict):
        texts.update({str(k): str(v) for k, v in embedded.items()})
    for filename, text in texts.items():
        apply_manifest(identity, filename, text)
    return identity


def _registry_slug(url: str) -> str | None:
    raw = str(url or "").strip()
    if raw.lower().startswith("web:"):
        raw = raw[4:]
    if "://" not in raw:
        return None
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in parsed.path.split("/") if part]
    if host == "pypi.org" and len(parts) >= 2 and parts[0] in {"project", "pypi"}:
        return parts[1]
    if host in {"npmjs.com"} and len(parts) >= 2 and parts[0] == "package":
        if parts[1].startswith("@") and len(parts) >= 3:
            return f"{parts[1]}/{parts[2]}"
        return parts[1]
    if host == "registry.npmjs.org" and parts:
        if parts[0].startswith("@") and len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return parts[0]
    if host == "crates.io" and len(parts) >= 2 and parts[0] == "crates":
        return parts[1]
    return None


def _slug_is_own(identity: OwnIdentity, slug: str) -> bool:
    text = str(slug or "").strip()
    if not text:
        return False
    if text.casefold() in identity.packages:
        return True
    return _pep503(text) in identity.packages_pep and _pep503(text) != ""


def _url_is_own(identity: OwnIdentity, url: str) -> str | None:
    norm = _norm_url(url)
    if not norm:
        return None
    for known in identity.urls:
        if norm == known or norm.startswith(known + "/"):
            return "own homepage, docs, or repository URL"
    slug = _registry_slug(url if "://" in url or url.lower().startswith("web:") else "")
    if slug and _slug_is_own(identity, slug):
        return f"own registry package {slug}"
    parsed_raw = url[4:] if url.lower().startswith("web:") else url
    if "://" not in parsed_raw:
        return None
    parsed = urlparse(parsed_raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in parsed.path.split("/") if part]
    if host in _FORGES and len(parts) >= 2:
        if parts[0].casefold() == identity.owner.casefold() and parts[1].casefold() == identity.name.casefold() and identity.name:
            return "own repository URL"
    if identity.owner and host == f"{identity.owner.casefold()}.github.io":
        if not parts or parts[0].casefold() == identity.name.casefold():
            return "own docs URL"
    return None


def _readme_match(identity: OwnIdentity, row: dict[str, Any]) -> bool:
    if len(identity.readme) < _README_MIN:
        return False
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("readme", "excerpt", "description")
    )
    text = re.sub(r"\s+", " ", blob).strip().casefold()
    if len(text) < _README_MIN:
        return False
    return text.startswith(identity.readme[:_README_MIN]) or identity.readme.startswith(text[:_README_MIN])


def own_reason(identity: OwnIdentity, row: dict[str, Any]) -> str | None:
    """Why this row is the candidate itself, or None when it is a different product."""
    alt_id = str(row.get("alternative_id") or "").strip()
    url = str(row.get("url") or "").strip()
    if identity.repo_id and alt_id.casefold() == identity.repo_id.casefold():
        return "same repository"
    if alt_id.lower().startswith(("npm:", "pypi:", "crates:")):
        slug = alt_id.split(":", 1)[1].strip()
        if _slug_is_own(identity, slug):
            return f"own registry package {slug}"
    elif alt_id and "/" not in alt_id and ":" not in alt_id and _slug_is_own(identity, alt_id):
        return f"own registry package {alt_id}"
    for candidate_url in (url, alt_id):
        why = _url_is_own(identity, candidate_url)
        if why:
            return why
    parent = str((row.get("parent") or {}).get("full_name") or row.get("parent_full_name") or "").casefold()
    source = str((row.get("source_repo") or {}).get("full_name") or "").casefold() if isinstance(row.get("source_repo"), dict) else ""
    fork = bool(row.get("fork") or row.get("is_fork"))
    if fork and identity.repo_id and (parent == identity.repo_id.casefold() or source == identity.repo_id.casefold()):
        return "fork of this repository"
    name = alt_id.split("/")[-1] if "/" in alt_id and ":" not in alt_id else ""
    if fork and identity.name and name.casefold() == identity.name.casefold():
        return "fork of this repository"
    owner = alt_id.split("/")[0] if alt_id.count("/") == 1 and ":" not in alt_id else ""
    if fork and identity.owner and owner.casefold() == identity.owner.casefold() and _readme_match(identity, row):
        return "mirror by the same owner"
    if _readme_match(identity, row):
        return "same README as this repository"
    return None


def split_own(identity: OwnIdentity, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return (kept rows, filtered records). Filtered records explain the drop."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for row in rows:
        why = own_reason(identity, row)
        if why:
            dropped.append({
                "alternative": str(row.get("alternative_id") or row.get("url") or ""),
                "url": str(row.get("url") or ""),
                "reason": why,
            })
        else:
            kept.append(row)
    return kept, dropped


def names_own_identity(identity: OwnIdentity, ref: str) -> bool:
    """True when an LLM-picked name or URL is this repository's own identity."""
    text = str(ref or "").strip()
    if not text:
        return False
    return own_reason(identity, {"alternative_id": text, "url": text}) is not None


def load_manifests(toolbox: Any, repo_id: str) -> dict[str, str]:
    """Fetch pyproject, setup, package.json, and Cargo.toml when the budget allows.

    A missing file is normal. The token stays inside the GitHub tool and is not logged here.
    """
    if toolbox is None or not repo_id or "/" not in repo_id:
        return {}
    budget = getattr(toolbox, "budget", None)
    if budget is not None and getattr(budget, "remaining_calls", 99) < 30:
        return {}
    found: dict[str, str] = {}
    for path in _MANIFEST_PATHS:
        if budget is not None and getattr(budget, "remaining_calls", 1) <= 8:
            break
        try:
            result = toolbox.call("github", {"action": "file", "repo": repo_id, "path": path})
        except Exception:
            continue
        if not getattr(result, "ok", False) or not isinstance(getattr(result, "data", None), dict):
            continue
        text = str(result.data.get("decoded_text") or "")
        if text.strip():
            found[path] = text
    return found

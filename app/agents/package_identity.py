"""Narrow, offline identity evidence for a distribution package's upstream.

This does not fetch a page or infer identity from a package name. It accepts only
an explicit Project entry in already collected text/HTML from Alpine's package
site, pointing to the exact candidate GitHub repository. Missing evidence stays
unknown. Rule B calls it only when its opt-in switch is on.
"""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse, urlunparse


def _github_repo(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != "github.com" or parsed.query or parsed.fragment:
        return None
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or any(part in {".", ".."} for part in parts) or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        return None
    return "/".join(parts).casefold()


class _ProjectTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.links: list[str] = []
        self.cell: list[str] | None = None
        self.projects: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.cells, self.links = [], []
        elif tag in {"td", "th"}:
            self.cell = []
        elif tag == "a" and self.cell is not None:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cells and self.cells[0].casefold() == "project" and len(self.links) == 1:
            self.projects.append(self.links[0])


def own_distribution_package(candidate: dict[str, Any], row: dict[str, Any]) -> dict[str, str] | None:
    url = str(row.get("url") or row.get("alternative_id") or "").removeprefix("web:")
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != "pkgs.alpinelinux.org" or not parsed.path.startswith("/package/"):
        return None
    repo = str(candidate.get("repo_id") or "")
    expected = _github_repo(f"https://github.com/{repo}")
    if not expected:
        return None
    # Quotes alone are insufficient. Require the page's explicit upstream metadata.
    sources = []
    projects = []
    for key in ("page_text", "page_html", "text", "excerpt", "readme", "description"):
        text = row.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        table = _ProjectTable()
        try:
            table.feed(text)
        except (ValueError, AssertionError):
            return None
        found = table.projects + re.findall(r"(?im)^\s*Project\s*\|\s*(https?://\S+)\s*$", text)
        if found:
            projects.extend(found)
            sources.append(text)
    if not projects or {_github_repo(project) for project in projects} != {expected}:
        return None
    return {
        "kind": "own_distribution_package", "source_url": urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", "")),
        "project_url": f"https://github.com/{repo}", "collected_text_sha256": hashlib.sha256("\n".join(sources).encode("utf-8")).hexdigest(),
    }

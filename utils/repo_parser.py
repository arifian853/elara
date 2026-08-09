"""
utils/repo_parser.py — Parser for GitHub Repositories using GitHub API.

Fetches repo metadata, README, package manifests, and summary file tree.
RAW code files (.ts, .py, etc.) are omitted in v1.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import httpx

from config import settings

logger = logging.getLogger(__name__)


def parse_github_url(url: str) -> tuple[str, str]:
    """
    Extract (owner, repo) from GitHub URL.

    Example: https://github.com/arifian853/portfolio-LTS -> ("arifian853", "portfolio-LTS")
    """
    clean_url = url.rstrip("/").removesuffix(".git")
    match = re.search(r"github\.com/([^/]+)/([^/]+)", clean_url)
    if not match:
        raise ValueError(f"Invalid GitHub URL: {url}")
    return match.group(1), match.group(2)


async def fetch_github_repo_data(owner: str, repo: str) -> dict:
    """
    Fetch repo metadata, README, package manifests, and file tree summary.

    Returns dict containing:
        - metadata: {github_url, repo, owner, description, tech_stack, stars, updated_at}
        - full_text: Compiled markdown string for chunking & embedding
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Elara-Public-RAG",
    }
    if settings.github_token:
        headers["Authorization"] = f"token {settings.github_token}"

    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        # 1. Fetch main repo metadata
        repo_resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
        if repo_resp.status_code != 200:
            raise ValueError(f"GitHub API error ({repo_resp.status_code}): Repo {owner}/{repo} not found")
        repo_info = repo_resp.json()

        metadata = {
            "github_url": repo_info.get("html_url", f"https://github.com/{owner}/{repo}"),
            "repo": repo,
            "owner": owner,
            "description": repo_info.get("description") or "",
            "language": repo_info.get("language") or "",
            "stars": repo_info.get("stargazers_count", 0),
            "updated_at": repo_info.get("updated_at", ""),
            "tech_stack": [],
        }

        # 2. Fetch README
        readme_content = ""
        readme_resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}/readme")
        if readme_resp.status_code == 200:
            raw_b64 = readme_resp.json().get("content", "")
            readme_content = base64.b64decode(raw_b64).decode("utf-8", errors="replace")

        # 3. Fetch Package Manifests (package.json or pyproject.toml)
        manifest_summary = ""
        tech_stack = []
        if metadata["language"]:
            tech_stack.append(metadata["language"])

        # Check package.json
        pkg_resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}/contents/package.json")
        if pkg_resp.status_code == 200:
            try:
                raw_b64 = pkg_resp.json().get("content", "")
                pkg_data = json.loads(base64.b64decode(raw_b64).decode("utf-8", errors="replace"))
                deps = list(pkg_data.get("dependencies", {}).keys())
                dev_deps = list(pkg_data.get("devDependencies", {}).keys())
                manifest_summary = f"Dependencies: {', '.join(deps[:15])}"
                for d in deps + dev_deps:
                    if d in ("react", "next", "vue", "tailwindcss", "typescript", "fastapi"):
                        tech_stack.append(d)
            except Exception as e:
                logger.warning(f"Error parsing package.json: {e}")

        # Check pyproject.toml
        pyproj_resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}/contents/pyproject.toml")
        if pyproj_resp.status_code == 200:
            try:
                raw_b64 = pyproj_resp.json().get("content", "")
                toml_text = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
                manifest_summary += f"\nPyproject TOML config present."
            except Exception as e:
                logger.warning(f"Error reading pyproject.toml: {e}")

        metadata["tech_stack"] = list(dict.fromkeys(tech_stack))

        # 4. Fetch concise File Tree (top 30 items)
        tree_summary = ""
        tree_resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1")
        if tree_resp.status_code == 200:
            tree_data = tree_resp.json().get("tree", [])
            paths = [item["path"] for item in tree_data if not item["path"].startswith(".")]
            tree_summary = "\n".join(paths[:30])

        # 5. Compile Markdown string for RAG knowledge base
        full_text_parts = [
            f"# Repository: {owner}/{repo}",
            f"**Description:** {metadata['description']}",
            f"**GitHub URL:** {metadata['github_url']}",
            f"**Language:** {metadata['language']} | **Stars:** {metadata['stars']}",
            f"**Tech Stack:** {', '.join(metadata['tech_stack'])}",
        ]

        if manifest_summary:
            full_text_parts.append(f"## Dependencies & Manifest\n{manifest_summary}")

        if tree_summary:
            full_text_parts.append(f"## Structure File Tree\n```\n{tree_summary}\n```")

        if readme_content:
            full_text_parts.append(f"## README\n{readme_content}")

        full_text = "\n\n".join(full_text_parts)

        return {
            "metadata": metadata,
            "full_text": full_text,
        }

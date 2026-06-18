"""
CrewAI @tool functions for the Code Context Agent.
Fetches source code from GitHub/GitLab REST APIs:
- test scripts, page objects, framework utilities, config files
- List repo file tree, download file contents, save locally
"""

import base64 as _base64
import json as _json
import logging
import os as _os
import re as _re
from pathlib import Path
from urllib.parse import quote as _quote

import httpx

from crewai.tools import tool

logger = logging.getLogger(__name__)

CODE_SAVE_DIR_NAME = "code"

# Patterns for identifying relevant source files
TEST_FILE_PATTERNS = [
    r".*Test.*\.java$", r".*Spec.*\.java$", r".*IT\.java$",
    r".*Tests?\.java$", r".*test_.*\.py$", r".*_test\.py$",
    r".*\.test\.ts$", r".*\.spec\.ts$",
]

PAGE_OBJECT_PATTERNS = [
    r".*Page\.java$", r".*PageObject\.java$", r".*Screen\.java$",
    r".*View\.java$", r".*Fragment\.java$",
]

UTILITY_PATTERNS = [
    r".*Util\.java$", r".*Helper\.java$", r".*Base\.java$",
    r".*Factory\.java$", r".*Driver\.java$", r".*Manager\.java$",
    r".*Utils\.java$", r".*Constants\.java$", r".*Config\.java$",
]

CONFIG_PATTERNS = [
    r"pom\.xml$", r"testng\.xml$", r"\.properties$", r"\.yaml$",
    r"\.yml$", r"build\.gradle$", r"package\.json$",
    r"Dockerfile.*", r"\.env.*", r"Jenkinsfile.*",
    r"\.toml$", r"\.cfg$", r"\.ini$",
]

ALL_CODE_PATTERNS = TEST_FILE_PATTERNS + PAGE_OBJECT_PATTERNS + UTILITY_PATTERNS + CONFIG_PATTERNS


def _get_github_token() -> str | None:
    token = _os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return None
    if not (token.startswith("ghp_") or token.startswith("github_pat_")):
        logger.warning("GITHUB_TOKEN doesn't look like a valid GitHub token (should start with ghp_ or github_pat_) — ignoring it")
        return None
    return token


def _get_gitlab_token() -> str | None:
    token = _os.getenv("GITLAB_TOKEN", "").strip()
    return token if token else None


def _parse_repo_url(repo_url: str) -> dict:
    """Parse a Git repo URL to determine provider, owner, repo name."""
    url = repo_url.strip().rstrip("/").rstrip(".git")

    if "github.com" in url:
        # https://github.com/owner/repo
        parts = url.split("github.com/")[-1].strip("/").split("/")
        if len(parts) >= 2:
            return {"provider": "github", "owner": parts[0], "repo": parts[1]}
        raise ValueError(f"Cannot parse GitHub URL: {repo_url}")

    if "gitlab" in url:
        # https://gitlab.com/namespace/project
        base = url.split("://")[1] if "://" in url else url
        host = base.split("/")[0]
        path_parts = base.split("/", 1)[1].split("/") if "/" in base else []
        if len(path_parts) >= 2:
            return {"provider": "gitlab", "host": host, "project_path": "/".join(path_parts[-2:])}
        raise ValueError(f"Cannot parse GitLab URL: {repo_url}")

    raise ValueError(f"Unsupported Git provider: {repo_url}")


def _match_any_pattern(filepath: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if _re.search(pat, filepath, _re.IGNORECASE):
            return True
    return False


def _classify_file(filepath: str) -> str:
    if _match_any_pattern(filepath, TEST_FILE_PATTERNS):
        return "test"
    if _match_any_pattern(filepath, PAGE_OBJECT_PATTERNS):
        return "page_object"
    if _match_any_pattern(filepath, UTILITY_PATTERNS):
        return "utility"
    if _match_any_pattern(filepath, CONFIG_PATTERNS):
        return "config"
    return "other"


@tool("Fetch Git Repo File Tree")
async def fetch_repo_file_tree(repo_url: str, branch: str = "main") -> str:
    """
    Fetch the complete file tree from a GitHub or GitLab repository.

    Parameters:
    - repo_url: Full Git repository URL (e.g. https://github.com/owner/repo)
    - branch: Branch name (default: main)

    Returns JSON list of file paths in the repository.
    """
    info = _parse_repo_url(repo_url)
    files = []

    if info["provider"] == "github":
        token = _get_github_token()
        api_url = f"https://api.github.com/repos/{info['owner']}/{info['repo']}/git/trees/{branch}?recursive=1"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(api_url, headers=headers)
            resp.raise_for_status()
            tree_data = resp.json()
            for item in tree_data.get("tree", []):
                if item.get("type") == "blob":
                    files.append(item["path"])

    elif info["provider"] == "gitlab":
        token = _get_gitlab_token()
        if not token:
            raise RuntimeError("Missing GITLAB_TOKEN environment variable — GitLab API requires authentication")
        gitlab_host = info.get("host", "gitlab.com")
        encoded_path = _quote(info["project_path"], safe="")
        api_base = _os.getenv("GITLAB_API_URL", f"https://{gitlab_host}/api/v4")
        api_url = f"{api_base}/projects/{encoded_path}/repository/tree"
        headers = {"PRIVATE-TOKEN": token}
        params = {"ref": branch, "recursive": "true", "per_page": 100}

        async with httpx.AsyncClient(timeout=30) as client:
            page = 1
            while True:
                params["page"] = page
                resp = await client.get(api_url, headers=headers, params=params)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                items = resp.json()
                if not items:
                    break
                for item in items:
                    if item.get("type") == "blob":
                        files.append(item["path"])
                if len(items) < 100:
                    break
                page += 1

    logger.info("Found %d files in repo %s (branch: %s)", len(files), repo_url, branch)
    return _json.dumps(files)


@tool("Filter Relevant Code Files")
def filter_relevant_code_files(file_tree_json: str, max_files: int = 100) -> str:
    """
    Filter a repo file tree to identify test scripts, page objects,
    framework utilities, and config files.

    Parameters:
    - file_tree_json: JSON-encoded list of file paths from fetch_repo_file_tree
    - max_files: Maximum number of files to include (default 100)

    Returns JSON object with categorized file lists.
    """
    all_files = _json.loads(file_tree_json) if isinstance(file_tree_json, str) else file_tree_json

    categorized = {"test": [], "page_object": [], "utility": [], "config": [], "other": []}
    total = 0

    for fpath in all_files:
        if total >= max_files:
            break
        cat = _classify_file(fpath)
        if cat != "other" or _match_any_pattern(fpath, [r"\.java$", r"\.py$", r"\.ts$", r"\.js$"]):
            categorized[cat].append(fpath)
            total += 1

    result = {
        "total_files": len(all_files),
        "selected_files": total,
        "by_category": {k: len(v) for k, v in categorized.items() if v},
        "files": categorized,
    }

    logger.info("Filtered %d/%d relevant code files", total, len(all_files))
    return _json.dumps(result, indent=2)


@tool("Fetch Code Files")
async def fetch_code_files(
    repo_url: str,
    branch: str,
    file_paths_json: str,
    save_dir: str,
) -> str:
    """
    Download the content of specific code files from a GitHub/GitLab repo.

    Parameters:
    - repo_url: Full Git repository URL
    - branch: Branch name
    - file_paths_json: JSON-encoded list of file paths to fetch
    - save_dir: Local directory to save files into a 'code/' subfolder

    Returns JSON list of saved file paths.
    """
    info = _parse_repo_url(repo_url)
    file_paths = _json.loads(file_paths_json) if isinstance(file_paths_json, str) else file_paths_json

    if isinstance(file_paths, dict):
        # Handle categorized format from filter_relevant_code_files
        flat_paths = []
        for cat_files in file_paths.get("files", file_paths).values():
            flat_paths.extend(cat_files)
        file_paths = flat_paths

    code_dir = Path(save_dir) / CODE_SAVE_DIR_NAME
    code_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    if info["provider"] == "github":
        token = _get_github_token()
        async with httpx.AsyncClient(timeout=30) as client:
            for fpath in file_paths:
                try:
                    # Try raw URL first (works for public repos without auth)
                    raw_url = f"https://raw.githubusercontent.com/{info['owner']}/{info['repo']}/{branch}/{fpath}"
                    resp = await client.get(raw_url)
                    if resp.status_code == 200 and resp.text:
                        content = resp.text
                        safe_name = fpath.replace("/", "_").replace("\\", "_")
                        out_path = code_dir / safe_name
                        out_path.write_text(content, encoding="utf-8")
                        saved.append(str(out_path.resolve()))
                        logger.info("Fetched: %s (%d bytes)", fpath, len(content))
                        continue

                    # Fallback: try Contents API with token
                    if token:
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        }
                        api_url = f"https://api.github.com/repos/{info['owner']}/{info['repo']}/contents/{fpath}?ref={branch}"
                        resp2 = await client.get(api_url, headers=headers)
                        if resp2.status_code == 200:
                            data = resp2.json()
                            content = _base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
                            safe_name = fpath.replace("/", "_").replace("\\", "_")
                            out_path = code_dir / safe_name
                            out_path.write_text(content, encoding="utf-8")
                            saved.append(str(out_path.resolve()))
                            logger.info("Fetched: %s (%d bytes)", fpath, len(content))
                            continue

                    logger.warning("GitHub fetch %s: raw=%d, api=skipped", fpath, resp.status_code)
                except Exception as e:
                    logger.warning("Error fetching %s: %s", fpath, e)

    elif info["provider"] == "gitlab":
        token = _get_gitlab_token()
        if not token:
            raise RuntimeError("Missing GITLAB_TOKEN environment variable — GitLab API requires authentication")
        gitlab_host = info.get("host", "gitlab.com")
        encoded_project = _quote(info["project_path"], safe="")
        api_base = _os.getenv("GITLAB_API_URL", f"https://{gitlab_host}/api/v4")
        headers = {"PRIVATE-TOKEN": token}

        async with httpx.AsyncClient(timeout=30) as client:
            for fpath in file_paths:
                try:
                    encoded_path = _quote(fpath, safe="")
                    api_url = f"{api_base}/projects/{encoded_project}/repository/files/{encoded_path}/raw?ref={branch}"
                    resp = await client.get(api_url, headers=headers)
                    if resp.status_code == 200:
                        content = resp.text
                        safe_name = fpath.replace("/", "_").replace("\\", "_")
                        out_path = code_dir / safe_name
                        out_path.write_text(content, encoding="utf-8")
                        saved.append(str(out_path.resolve()))
                        logger.info("Fetched: %s (%d bytes)", fpath, len(content))
                    elif resp.status_code != 404:
                        logger.warning("GitLab fetch %s: HTTP %d", fpath, resp.status_code)
                except Exception as e:
                    logger.warning("Error fetching %s: %s", fpath, e)

    logger.info("Saved %d code files to %s", len(saved), code_dir)
    return _json.dumps(saved, indent=2)

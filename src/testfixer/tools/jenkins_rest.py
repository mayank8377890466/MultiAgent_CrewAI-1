"""
Direct Jenkins REST API helper for operations not available via MCP:
- Resolving symbolic build numbers (latest, lastStable, etc.)
- Downloading workspace files (not archived artifacts)
"""

import os
import logging
import httpx
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _get_env_or_raise(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _build_auth():
    from base64 import b64encode
    username = _get_env_or_raise("JENKINS_USERNAME")
    password = _get_env_or_raise("JENKINS_PASSWORD")
    credentials = b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


def _job_url_path(job_name: str) -> str:
    return quote(job_name.strip(), safe="")


async def resolve_build_number(job_name: str, build_spec: str) -> int:
    """
    Resolve a symbolic or numeric build specifier to an actual build number.
    Accepts: "latest", "lastStable", "lastSuccessful", "lastFailed",
    "lastUnstable", "lastCompleted", or a plain integer string.
    """
    if build_spec.isdigit():
        return int(build_spec)

    job_path = _job_url_path(job_name)

    special = {
        "latest": "lastBuild",
        "laststable": "lastStableBuild",
        "lastsuccessful": "lastSuccessfulBuild",
        "lastfailed": "lastFailedBuild",
        "lastunstable": "lastUnstableBuild",
        "lastcompleted": "lastCompletedBuild",
    }
    tree_key = special.get(build_spec.lower(), "lastBuild")

    jenkins_url = _get_env_or_raise("JENKINS_URL").rstrip("/")
    api_url = f"{jenkins_url}/job/{job_path}/{tree_key}/api/json"
    headers = _build_auth()

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        resp = await client.get(api_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        build_number = data.get("number")
        if build_number is None:
            raise RuntimeError(f"Could not resolve build number from {tree_key} for {job_name}")
        logger.info("Resolved %s -> build #%d", build_spec, build_number)
        return int(build_number)


async def download_workspace_file(
    job_name: str, workspace_path: str, output_filepath: str
) -> str:
    """
    Download a single file from the job workspace via Jenkins REST API.
    workspace_path is relative to the job workspace root (e.g. "Reports/index.html").
    """
    job_path = _job_url_path(job_name)
    jenkins_url = _get_env_or_raise("JENKINS_URL").rstrip("/")
    url = f"{jenkins_url}/job/{job_path}/ws/{workspace_path}"
    headers = _build_auth()

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        content = resp.content

    from pathlib import Path
    out = Path(output_filepath)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(content)
    logger.info("Downloaded workspace file: %s -> %s (%d bytes)",
                workspace_path, output_filepath, len(content))
    return str(out.resolve())


async def download_workspace_dir(
    job_name: str, workspace_path: str, output_dir: str, pattern: str = "*.html"
) -> list[str]:
    """
    Download all matching files from a Jennykins workspace directory.
    Tries common report file patterns.
    """
    job_path = _job_url_path(job_name)
    jenkins_url = _get_env_or_raise("JENKINS_URL").rstrip("/")
    headers = _build_auth()
    downloaded = []

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        for filename in _common_report_files():
            ws_file = f"{workspace_path}/{filename}" if workspace_path else filename
            url = f"{jenkins_url}/job/{job_path}/ws/{ws_file}"
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200 and resp.content:
                    out_path = os.path.join(output_dir, filename)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    from pathlib import Path
                    Path(out_path).write_bytes(resp.content)
                    downloaded.append(str(Path(out_path).resolve()))
                    logger.info("Downloaded: %s", filename)
            except Exception:
                continue

    return downloaded


def _common_report_files() -> list[str]:
    """Common HTML report filenames found in automation workspaces."""
    return [
        "index.html",
        "emailable-report.html",
        "ExtentReport.html",
        "SparkReport.html",
        "overview.html",
        "report.html",
        "summary.html",
        "dashboard.html",
        "test-output/index.html",
        "test-output/emailable-report.html",
    ]

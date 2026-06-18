"""
CrewAI @tool functions for the Fetcher Agent.
All tools are synchronous — they use JenkinsMCPClient.call_tool_sync()
which bridges to the main event loop via run_coroutine_threadsafe.
"""

import json
import logging
from pathlib import Path
from typing import Any

from crewai.tools import tool

from .jenkins_client import get_active_client
from .jenkins_rest import resolve_build_number as _resolve_build_rest
from .artifact_store import (
    get_download_dir,
    save_text_file,
    save_binary_file,
    decode_artifact_content,
    decode_binary_from_base64,
    find_in_list,
    _normalize_artifact_names,
)

logger = logging.getLogger(__name__)


def _build_args(job_name: str, build_number: int) -> dict:
    return {"fullname": job_name.strip(), "number": build_number}


# ---------------------------------------------------------------------------
@tool("Fetch Build Info")
def fetch_build_info(job_name: str, build_number: int) -> str:
    """Fetch build metadata: status, timestamp, duration, URL."""
    client = get_active_client()
    return str(client.call_tool_sync("get_build", _build_args(job_name, build_number)))


@tool("Fetch Console Output")
def fetch_console_output(job_name: str, build_number: int) -> str:
    """Fetch the full console log output for a Jenkins build."""
    client = get_active_client()
    result = client.call_tool_sync("get_build_console_output", _build_args(job_name, build_number))
    text = decode_artifact_content(result)
    return text if text else str(result)


@tool("Fetch Test Report")
def fetch_test_report(job_name: str, build_number: int) -> str:
    """Fetch the TestNG/JUnit test report summary."""
    client = get_active_client()
    return str(client.call_tool_sync("get_build_test_report", _build_args(job_name, build_number)))


@tool("List All Artifacts")
def fetch_all_artifacts(job_name: str, build_number: int) -> str:
    """List all build artifact names. Returns JSON-encoded list."""
    client = get_active_client()
    result = client.call_tool_sync("get_all_build_artifacts", _build_args(job_name, build_number))
    artifacts: list[str] = []
    content = getattr(result, "content", []) or []
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if text is None and isinstance(item, dict):
                text = item.get("text", "")
            if text:
                artifacts.extend(text.strip().splitlines())
            elif isinstance(item, str):
                artifacts.extend(item.strip().splitlines())
    logger.info("Found %d artifacts for %s #%d", len(artifacts), job_name, build_number)
    return json.dumps(artifacts)


@tool("Download Artifact")
def download_artifact(job_name: str, build_number: int, artifact_name: str, save_dir: str) -> str:
    """Download a single named artifact from Jenkins to local disk."""
    client = get_active_client()
    args = {**_build_args(job_name, build_number), "relative_path": artifact_name}
    result = client.call_tool_sync("get_build_artifact", args)
    raw_bytes = decode_binary_from_base64(result)
    if raw_bytes is None:
        raw_bytes = str(result).encode("utf-8")
    return save_binary_file(Path(save_dir), artifact_name, raw_bytes)


@tool("Download Artifacts")
def download_artifacts(job_name: str, build_number: int, artifact_names: str, save_dir: str, artifact_type: str = "all") -> str:
    """Download all relevant build artifacts (screenshots, HTML report, TestNG XML, JUnit XML).
    Pass artifact_names as JSON list. artifact_type: 'all' (default), 'screenshots', 'html', 'testng', 'junit'."""
    names: list[str] = _normalize_artifact_names(artifact_names)
    client = get_active_client()
    downloaded: list[str] = []

    def _download(target_name: str, save_as: str) -> str | None:
        args = {**_build_args(job_name, build_number), "relative_path": target_name}
        result = client.call_tool_sync("get_build_artifact", args)
        raw_bytes = decode_binary_from_base64(result)
        if raw_bytes is None:
            return None
        return save_binary_file(Path(save_dir), save_as, raw_bytes)

    if artifact_type in ("all", "screenshots"):
        for name in [n for n in names if n.lower().endswith(".png")]:
            path = _download(name, name)
            if path:
                downloaded.append(path)

    if artifact_type in ("all", "html"):
        target = find_in_list(names, "emailable-report", "extent", "index.html", ".html")
        if target:
            path = _download(target, "execution-report.html")
            if path:
                downloaded.append(path)

    if artifact_type in ("all", "testng"):
        target = find_in_list(names, "testng-results.xml", "testng-results")
        if target:
            path = _download(target, "testng-results.xml")
            if path:
                downloaded.append(path)

    if artifact_type in ("all", "junit"):
        target = find_in_list(names, "junit", "TEST-")
        if target:
            path = _download(target, "junit-results.xml")
            if path:
                downloaded.append(path)

    return json.dumps(downloaded)


@tool("Resolve Build Number")
def resolve_build_number(job_name: str, build_spec: str = "latest") -> int:
    """Resolve 'latest', 'lastFailed', etc. to a build number."""
    import asyncio as _asyncio
    return _asyncio.run(_resolve_build_rest(job_name, build_spec))


@tool("Download Workspace HTML Report")
def download_workspace_html(job_name: str, workspace_path: str, save_dir: str) -> str:
    """Download HTML report from workspace. workspace_path is relative."""
    import asyncio as _asyncio
    from .jenkins_rest import download_workspace_file as _dl
    import os as _os
    filepath = _os.path.join(save_dir, "execution-report.html")
    return _asyncio.run(_dl(job_name, workspace_path, filepath))


@tool("Download All Workspace HTML Reports")
def download_all_workspace_html(job_name: str, workspace_path: str, save_dir: str) -> str:
    """Download all HTML files from a workspace directory."""
    import asyncio as _asyncio
    from .jenkins_rest import download_workspace_dir as _dl_dir
    return json.dumps(tuple(_asyncio.run(_dl_dir(job_name, workspace_path, save_dir))))


@tool("Save Build Metadata")
def build_metadata_json(
    job_name: str,
    build_number: int,
    status: str,
    timestamp: str,
    duration_ms: int,
    build_url: str,
    test_report_json: str,
    save_dir: str,
) -> str:
    """Aggregate all build metadata into a JSON file saved locally."""
    metadata = {
        "job_name": job_name,
        "build_number": build_number,
        "build_url": build_url,
        "status": status,
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "test_summary": json.loads(test_report_json) if test_report_json else {},
    }
    content = json.dumps(metadata, indent=2, default=str)
    return save_text_file(Path(save_dir), "build-metadata.json", content)

"""
Helpers for downloading build artifacts to local disk and constructing paths.
"""

import json
import os
import base64
import logging
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


def get_download_dir(job_name: str, build_number: int) -> Path:
    base = Path(os.getenv("ARTIFACT_DOWNLOAD_DIR", "./artifacts")).resolve()
    return base / job_name.strip() / str(build_number)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_text_file(directory: Path, filename: str, content: str) -> str:
    ensure_dir(directory)
    filepath = directory / filename
    filepath.write_text(content, encoding="utf-8")
    logger.info("Saved text file: %s", filepath)
    return str(filepath.resolve())


def save_binary_file(directory: Path, filename: str, content: bytes) -> str:
    ensure_dir(directory)
    filepath = directory / filename
    filepath.write_bytes(content)
    logger.info("Saved binary file: %s (%d bytes)", filepath, len(content))
    return str(filepath.resolve())


def decode_artifact_content(mcp_result: Any) -> Optional[str]:
    """
    Extract text content from an MCP CallToolResult (mcp.types.CallToolResult).
    Handles both the object form (.content attr) and dict fallback.
    """
    content = getattr(mcp_result, "content", None)
    if content is None and isinstance(mcp_result, dict):
        content = mcp_result.get("content")

    if not content or not isinstance(content, list):
        return None

    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text", "")
        if text:
            return text
    return None


def decode_binary_from_base64(mcp_result: Any) -> Optional[bytes]:
    text = decode_artifact_content(mcp_result)
    if text is None:
        return None
    try:
        return base64.b64decode(text)
    except Exception:
        return text.encode("utf-8")


def _normalize_artifact_names(raw: str) -> list[str]:
    """Parse artifact_names JSON and flatten to a plain list of filename strings.
    
    Handles both flat string lists and lists of dicts (where each dict has 
    keys like 'fileName', 'relativePath', or 'name').
    """
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    result: list[str] = []
    for item in data:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(item.get("fileName") or item.get("relativePath") or item.get("name") or "")
    return [r for r in result if r]

def find_in_list(items: list[str], *patterns: str) -> Optional[str]:
    """Find the first artifact name matching any of the given substring patterns."""
    for item in items:
        if not isinstance(item, str):
            continue
        for pattern in patterns:
            if pattern.lower() in item.lower():
                return item
    return None

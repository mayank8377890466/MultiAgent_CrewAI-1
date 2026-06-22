"""
CrewAI @tool functions for indexing source code into ChromaDB.
Creates a semantic index of test scripts, page objects, utilities, and configs
so the Analysis Agent can cross-reference failures against actual source code.

Uses a separate 'code_knowledge' ChromaDB collection from the build logs.
Uses batch indexing to avoid opening a new SQLite connection per file.
"""

import json as _json
import logging
import re
from pathlib import Path

from crewai.tools import tool

from .knowledge_store import (
    get_or_create_collection,
    index_documents_batch,
    query_similar,
    count_documents,
)

logger = logging.getLogger(__name__)

CODE_COLLECTION = "code_knowledge"


def _classify_file_by_content(filename: str) -> str:
    name = filename.lower()
    if re.search(r"test|spec|it\.", name):
        return "test"
    if re.search(r"page|screen|view|fragment", name):
        return "page_object"
    if re.search(r"util|helper|base|factory|driver|manager|constants|config", name):
        return "utility"
    if re.search(r"\.xml$|\.properties$|\.yaml$|\.yml$|\.toml$|\.json$", name):
        return "config"
    return "other"


@tool("Index Code Files to Knowledge")
def index_code_to_knowledge(
    repo_url: str,
    branch: str,
    code_files_json: str,
    save_dir: str,
) -> str:
    """
    Index downloaded source code files into the ChromaDB code knowledge store.
    Each file is indexed with metadata: filepath, category, repo, branch.
    This enables semantic search against actual source code during analysis.

    Uses batch indexing — all files are accumulated and added in one
    ChromaDB call, preventing connection-per-file storms on Windows.

    Parameters:
    - repo_url: Git repository URL
    - branch: Branch name
    - code_files_json: JSON list of saved file paths from fetch_code_files
    - save_dir: Directory where code files were saved

    Returns JSON with indexing stats.
    """
    file_paths = _json.loads(code_files_json) if isinstance(code_files_json, str) else code_files_json
    code_dir = Path(save_dir) / "code"

    if not code_dir.exists():
        return _json.dumps({"error": f"Code directory not found: {code_dir}", "indexed": 0})

    # If file_paths is empty or paths don't exist, scan code_dir for files
    all_exist = all(Path(p).exists() for p in file_paths) if file_paths else False
    if not file_paths or not all_exist:
        logger.info("File paths not found locally — scanning code directory: %s", code_dir)
        file_paths = [str(f.resolve()) for f in code_dir.iterdir() if f.is_file()]
        if not file_paths:
            return _json.dumps({"error": f"No files found in {code_dir}", "indexed": 0})

    repo_name = repo_url.split("/")[-1].replace(".git", "") if "/" in repo_url else repo_url

    # Accumulate all documents and batch-index them
    batch_docs: list[tuple[str, str, dict]] = []
    skipped = 0

    for fpath in file_paths:
        file_path = Path(fpath)
        if not file_path.exists():
            skipped += 1
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped += 1
            continue

        if not content or not content.strip():
            skipped += 1
            continue

        # Truncate very large files for embedding
        if len(content) > 8000:
            content = content[:8000]

        local_name = file_path.name
        original_path = local_name.replace("_", "/", 1) if "_" in local_name else local_name

        doc_id = f"code_{file_path.stem}"[:63]
        metadata = {
            "filepath": original_path,
            "repo": repo_name,
            "branch": branch,
            "category": _classify_file_by_content(local_name),
            "source": repo_url,
        }
        batch_docs.append((doc_id, content, metadata))

    indexed = index_documents_batch(batch_docs, CODE_COLLECTION)

    logger.info("Code indexing: %d indexed, %d skipped", indexed, skipped)
    return _json.dumps({
        "indexed": indexed,
        "skipped": skipped,
        "total": len(file_paths),
        "collection": CODE_COLLECTION,
        "collection_size": count_documents(CODE_COLLECTION),
    })


@tool("Query Code Knowledge")
def query_code_knowledge(
    query_text: str,
    n_results: int = 5,
) -> str:
    """
    Search the code knowledge store for semantically related source code.
    Use this to find test scripts, page objects, or utilities related to
    a specific error message or test name.

    Parameters:
    - query_text: Error message, test name, class name, or keyword to search
    - n_results: Number of results (default 5)

    Returns JSON list of matching code documents with metadata.
    """
    results = query_similar(query_text, n_results=n_results, collection_name=CODE_COLLECTION)

    formatted = []
    for r in results:
        meta = r.get("metadata", {})
        formatted.append({
            "file": meta.get("filepath", "unknown"),
            "repo": meta.get("repo", "unknown"),
            "branch": meta.get("branch", "unknown"),
            "category": meta.get("category", "unknown"),
            "similarity": round(1.0 - r.get("distance", 0.0), 4),
            "code_snippet": (r.get("document", "") or "")[:500],
        })

    return _json.dumps(formatted, indent=2, default=str)


@tool("Get Code Knowledge Stats")
def get_code_knowledge_stats(collection_name: str = "code_knowledge") -> str:
    """
    Return statistics about the code knowledge store.

    Args:
        collection_name: The name of the knowledge collection (default: 'code_knowledge')
    """
    count = count_documents(CODE_COLLECTION)
    return _json.dumps({"total_documents": count, "collection": CODE_COLLECTION})

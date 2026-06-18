"""
CrewAI @tool functions for RAG: indexing artifacts and querying knowledge.
These are attached to the Fetcher Agent so it can index build data and
retrieve similar past failures from the ChromaDB vector store.
"""

import json as _json
import logging
from pathlib import Path

from crewai.tools import tool

from .knowledge_store import index_document, query_similar, count_documents

logger = logging.getLogger(__name__)


@tool("Index Build Data for RAG")
async def index_build_to_knowledge(
    job_name: str,
    build_number: int,
    status: str,
    console_log_path: str,
    test_report_json: str,
    build_url: str,
) -> str:
    """
    Index the fetched build artifacts into the ChromaDB knowledge store.
    Splits console output into chunks and stores each with metadata for
    future RAG retrieval.

    Parameters:
    - job_name: Jenkins job name
    - build_number: Build number
    - status: Build status (SUCCESS, FAILURE, UNSTABLE)
    - console_log_path: Path to the downloaded console-output.txt
    - test_report_json: JSON string of the test report dict
    - build_url: Jenkins build URL
    """
    indexed = 0
    job_clean = job_name.strip()
    base_id = f"{job_clean}_{build_number}"
    base_meta = {
        "job_name": job_clean,
        "build_number": str(build_number),
        "status": status,
        "build_url": build_url,
    }

    # Index the full console log as a single doc
    try:
        log_text = Path(console_log_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        log_text = ""

    if log_text:
        if index_document(
            f"{base_id}_console",
            log_text,
            {**base_meta, "artifact_type": "console_log"},
        ):
            indexed += 1

    # Index error lines separately for precision retrieval
    error_lines = [line for line in log_text.splitlines() if "error" in line.lower() or "fail" in line.lower() or "exception" in line.lower() or "stacktrace" in line.lower()]
    if error_lines:
        error_chunks = _chunk_text("\n".join(error_lines), chunk_size=500)
        for i, chunk in enumerate(error_chunks):
            if index_document(
                f"{base_id}_errors_{i}",
                chunk,
                {**base_meta, "artifact_type": "console_errors"},
            ):
                indexed += 1

    # Index test report as structured text
    if test_report_json:
        try:
            report = _json.loads(test_report_json) if isinstance(test_report_json, str) else test_report_json
            report_text = _json.dumps(report, indent=2, default=str)
            if index_document(
                f"{base_id}_testreport",
                report_text,
                {**base_meta, "artifact_type": "test_report"},
            ):
                indexed += 1
        except Exception:
            pass

    logger.info("Indexed %d documents for %s #%s", indexed, job_clean, build_number)
    return _json.dumps({"indexed_documents": indexed, "build_id": base_id})


@tool("Query Knowledge Store")
async def query_flaky_knowledge(
    query_text: str,
    n_results: int = 5,
) -> str:
    """
    Search the ChromaDB knowledge store for similar past failures.

    Parameters:
    - query_text: Error message, log snippet, or description to search
    - n_results: Number of results to return (default 5)
    """
    results = query_similar(query_text, n_results=n_results)

    formatted = []
    for r in results:
        formatted.append({
            "build": r.get("metadata", {}).get("job_name", "unknown") + " #" + r.get("metadata", {}).get("build_number", "?"),
            "artifact_type": r.get("metadata", {}).get("artifact_type", "unknown"),
            "similarity": round(1.0 - r.get("distance", 0.0), 4),
            "snippet": r.get("document", "")[:300] + ("..." if len(r.get("document", "")) > 300 else ""),
        })

    return _json.dumps(formatted, indent=2, default=str)


@tool("Get Knowledge Stats")
async def get_knowledge_stats(collection_name: str = "flaky_test_knowledge") -> str:
    """
    Return statistics about the current knowledge store.

    Args:
        collection_name: The name of the knowledge collection (default: 'flaky_test_knowledge')
    """
    count = count_documents()
    return _json.dumps({"total_documents": count, "collection": "flaky_test_knowledge"})


def _chunk_text(text: str, chunk_size: int = 1000) -> list[str]:
    """Split text into chunks of roughly chunk_size characters."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i:i + chunk_size])
    return chunks

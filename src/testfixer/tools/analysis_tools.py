"""
CrewAI @tool functions for the Analysis Agent.
Parses console logs, test reports, and queries RAG to produce
root cause analysis for flaky test failures.
"""

import json as _json
import logging
import re
from pathlib import Path

from crewai.tools import tool

from .knowledge_store import query_similar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error pattern definitions
# ---------------------------------------------------------------------------
ERROR_PATTERNS = {
    "TimeoutException": {
        "pattern": r"Timeout|timed out|TimeoutException|WebDriverWait",
        "severity": "HIGH",
        "category": "Environment/Timing",
        "fix_hint": "Increase explicit wait timeout, check network latency, verify element locator is correct, or add retry logic with exponential backoff.",
    },
    "NullPointerException": {
        "pattern": r"NullPointerException|NullPointer|null pointer|\.NullPointer",
        "severity": "HIGH",
        "category": "Code Defect",
        "fix_hint": "Add null guards before accessing the element. Verify page object initialization in @BeforeMethod. Check test data provider returns valid data.",
    },
    "StaleElementException": {
        "pattern": r"StaleElement|stale element|not attached to the DOM",
        "severity": "MEDIUM",
        "category": "DOM/State Change",
        "fix_hint": "Re-locate the element after DOM refresh. Use PageFactory with @CacheLookup cautiously. Implement explicit wait with ExpectedConditions.refreshed().",
    },
    "NoSuchElementException": {
        "pattern": r"NoSuchElement|no such element|Unable to locate element",
        "severity": "MEDIUM",
        "category": "Locator/Selector",
        "fix_hint": "Verify the element locator (XPath/CSS). Check for iframe switches. Ensure the correct page is loaded. Add wait for element visibility.",
    },
    "ElementClickIntercepted": {
        "pattern": r"click intercepted|ElementClickIntercepted|other element would receive",
        "severity": "MEDIUM",
        "category": "UI Overlay",
        "fix_hint": "Wait for overlays/modals to close. Scroll element into view before clicking. Use JavaScript click as fallback. Check for fixed header/footer overlap.",
    },
    "BrowserCrash": {
        "pattern": r"chrome not reachable|browser.*crash|session deleted|WebDriverException.*disconnected",
        "severity": "CRITICAL",
        "category": "Infrastructure",
        "fix_hint": "Check browser/driver version compatibility. Increase node memory. Restart WebDriver session on failure. Verify CI agent resources.",
    },
    "AssertionError": {
        "pattern": r"AssertionError|Assertion failed|expected.*but was|assertThat",
        "severity": "MEDIUM",
        "category": "Test Logic",
        "fix_hint": "Review expected vs actual values. Check test data is deterministic. Add tolerance for timing-sensitive assertions. Use awaitility for async checks.",
    },
    "ConnectionRefused": {
        "pattern": r"Connection refused|ConnectionReset|connect timed out|503|502|Bad Gateway",
        "severity": "CRITICAL",
        "category": "Infrastructure",
        "fix_hint": "Verify target service is running before test starts. Add health check precondition. Implement circuit breaker pattern. Check CI network policies.",
    },
}


# ---------------------------------------------------------------------------
# Tool: Parse Console Log for Errors
# ---------------------------------------------------------------------------
@tool("Parse Console Log for Errors")
async def parse_console_errors(console_log_path: str) -> str:
    """
    Parse a console log file and extract all error patterns.

    Parameters:
    - console_log_path: Absolute path to the console-output.txt file

    Returns a JSON string with:
    - total_lines: Total line count
    - error_lines: List of matching error lines
    - error_summary: Categorized error matches with counts and fix hints
    - severity_counts: Counts by severity level
    - has_failures: True if any errors found
    """
    try:
        log_text = Path(console_log_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return _json.dumps({"error": f"Could not read console log: {console_log_path}", "has_failures": False})

    lines = log_text.splitlines()
    total_lines = len(lines)

    error_matches = []
    line_samples = []
    for i, line in enumerate(lines):
        for name, info in ERROR_PATTERNS.items():
            if re.search(info["pattern"], line, re.IGNORECASE):
                error_matches.append({
                    "line_number": i + 1,
                    "type": name,
                    "severity": info["severity"],
                    "category": info["category"],
                    "raw_line": line.strip()[:200],
                })
                line_samples.append(line.strip()[:200])
                break

    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    type_counts = {}
    for m in error_matches:
        severity_counts[m["severity"]] = severity_counts.get(m["severity"], 0) + 1
        type_counts[m["type"]] = type_counts.get(m["type"], 0) + 1

    # Deduplicated top errors with fix hints
    seen_types = set()
    error_summary = []
    for m in error_matches:
        if m["type"] not in seen_types:
            seen_types.add(m["type"])
            error_summary.append({
                "type": m["type"],
                "count": type_counts[m["type"]],
                "severity": m["severity"],
                "category": m["category"],
                "fix_hint": ERROR_PATTERNS.get(m["type"], {}).get("fix_hint", ""),
                "sample": m["raw_line"],
            })

    result = {
        "total_lines": total_lines,
        "total_errors": len(error_matches),
        "error_summary": sorted(error_summary, key=lambda x: (x["severity"], -x["count"])),
        "severity_counts": severity_counts,
        "has_failures": len(error_matches) > 0,
        "error_line_samples": line_samples[:10],
    }
    return _json.dumps(result, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool: Cross-reference with RAG Knowledge
# ---------------------------------------------------------------------------
@tool("Cross-reference with Past Failures")
async def cross_reference_with_past(console_log_path: str, n_results: int = 5) -> str:
    """
    Search the RAG knowledge store for past failures similar to the
    errors in the current console log.

    Extracts the top error lines from the log and queries the ChromaDB
    vector store for semantically similar past failures.

    Parameters:
    - console_log_path: Path to console-output.txt
    - n_results: Max results per query (default 5)

    Returns a JSON list of similar past failures with similarity scores.
    """
    try:
        log_text = Path(console_log_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return _json.dumps({"error": "Could not read console log"})

    # Extract representative error snippets for querying
    error_snippets = []
    for line in log_text.splitlines():
        if re.search(r"error|exception|fail|timeout|crash", line, re.IGNORECASE):
            error_snippets.append(line.strip()[:300])
        if len(error_snippets) >= 3:
            break

    if not error_snippets:
        error_snippets = [log_text[:500]]

    all_matches = {}
    for snippet in error_snippets[:3]:
        results = query_similar(snippet, n_results=n_results)
        for r in results:
            match_id = r["id"]
            if match_id not in all_matches:
                all_matches[match_id] = {
                    "id": match_id,
                    "build": f"{r['metadata'].get('job_name','?')} #{r['metadata'].get('build_number','?')}",
                    "status": r["metadata"].get("status", "?"),
                    "artifact_type": r["metadata"].get("artifact_type", "?"),
                    "similarity": round(1.0 - r["distance"], 4),
                    "snippet": r["document"][:300],
                }

    sorted_matches = sorted(all_matches.values(), key=lambda x: x["similarity"], reverse=True)
    return _json.dumps(sorted_matches[:10], indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool: Generate Root Cause Analysis Report
# ---------------------------------------------------------------------------
@tool("Generate Analysis Report")
async def generate_analysis_report(
    job_name: str,
    build_number: str,
    status: str,
    console_log_path: str,
    parsed_errors_json: str,
    rag_results_json: str,
    test_report_json: str,
    save_dir: str,
) -> str:
    """
    Generate a comprehensive root cause analysis report in markdown format.
    Saves to {save_dir}/analysis-report.md.

    Parameters:
    - job_name: Jenkins job name
    - build_number: Build number
    - status: Build status
    - console_log_path: Path to console log
    - parsed_errors_json: Output from parse_console_errors tool
    - rag_results_json: Output from cross_reference_with_past tool
    - test_report_json: JSON of test report
    - save_dir: Directory to save the report

    Returns path to the saved report file.
    """
    parsed = _json.loads(parsed_errors_json)
    rag = _json.loads(rag_results_json) if isinstance(rag_results_json, str) else rag_results_json

    report_lines = []
    report_lines.append(f"# Flaky Test Analysis Report")
    report_lines.append("")
    report_lines.append(f"**Job:** `{job_name}`  ")
    report_lines.append(f"**Build:** #{build_number}  ")
    report_lines.append(f"**Status:** `{status}`  ")
    report_lines.append(f"**Generated:** {__import__('datetime').datetime.now().isoformat(timespec='seconds')}")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")

    # Quick Summary
    errors_found = parsed.get("total_errors", 0)
    report_lines.append("## Quick Summary")
    report_lines.append("")
    if errors_found == 0:
        report_lines.append("> No errors detected in console output. Build appears clean.")
    else:
        critical = parsed.get("severity_counts", {}).get("CRITICAL", 0)
        high = parsed.get("severity_counts", {}).get("HIGH", 0)
        report_lines.append(f"> **{errors_found} error(s)** found — {critical} CRITICAL, {high} HIGH")
    report_lines.append("")

    # Error Breakdown
    if errors_found > 0:
        report_lines.append("## Error Breakdown")
        report_lines.append("")
        report_lines.append("| Type | Count | Severity | Category | Fix Hint |")
        report_lines.append("|------|-------|----------|----------|----------|")
        for e in parsed.get("error_summary", []):
            fix = e.get("fix_hint", "")[:80]
            report_lines.append(f"| {e['type']} | {e['count']} | {e['severity']} | {e['category']} | {fix} |")
        report_lines.append("")

        # Sample errors
        report_lines.append("## Error Line Samples")
        report_lines.append("")
        report_lines.append("```")
        for s in parsed.get("error_line_samples", [])[:10]:
            report_lines.append(str(s))
        report_lines.append("```")
        report_lines.append("")

    # Similar past failures
    if rag and isinstance(rag, list) and len(rag) > 0:
        report_lines.append("## Similar Past Failures (RAG)")
        report_lines.append("")
        report_lines.append("| Similarity | Build | Type | Snippet |")
        report_lines.append("|------------|-------|------|---------|")
        for r in rag[:5]:
            if not isinstance(r, dict):
                continue
            similarity = r.get("similarity", 0)
            build = r.get("build", r.get("id", "unknown"))
            artifact_type = r.get("artifact_type", "unknown")
            snippet = (r.get("snippet", "") or "")[:100]
            try:
                sim_str = f"{float(similarity):.2%}"
            except (ValueError, TypeError):
                sim_str = str(similarity)
            report_lines.append(f"| {sim_str} | {build} | {artifact_type} | {snippet} |")
        report_lines.append("")

    # Recommendations
    report_lines.append("## Recommendations")
    report_lines.append("")
    if errors_found == 0:
        report_lines.append("1. No immediate action required.")
        report_lines.append("2. Monitor future builds for regression.")
    else:
        seen_fixes = set()
        idx = 1
        for e in parsed.get("error_summary", []):
            fix = e.get("fix_hint", "")
            if fix and fix not in seen_fixes:
                seen_fixes.add(fix)
                report_lines.append(f"{idx}. **[{e['severity']}] {e['type']}**: {fix}")
                idx += 1

        if rag:
            report_lines.append(f"{idx}. **Leverage past fixes**: Review similar failures above for known solutions.")
            idx += 1

        report_lines.append(f"{idx}. Review console log at `{console_log_path}` for full context.")

    report_lines.append("")
    report_lines.append("---")
    report_lines.append("*Report auto-generated by TestFixer Analysis Agent.*")

    report_content = "\n".join(report_lines)
    from .artifact_store import save_text_file
    filepath = save_text_file(Path(save_dir), "analysis-report.md", report_content)
    logger.info("Analysis report saved to %s", filepath)
    return filepath

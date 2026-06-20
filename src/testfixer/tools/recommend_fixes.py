"""
CrewAI @tool functions for the Fix Recommendation Agent.
Analyzes flaky test failures against source code to produce
specific code/configuration fix suggestions with confidence scores.
"""

import json as _json
import logging
from pathlib import Path

from crewai.tools import tool

from .knowledge_store import query_similar as _query_similar
from .code_indexer import CODE_COLLECTION

logger = logging.getLogger(__name__)

FIX_PATTERNS = {
    "TimeoutException": {
        "test_fixes": [
            {
                "description": "Increase WebDriverWait timeout in test method",
                "template": "Replace `new WebDriverWait(driver, Duration.ofSeconds({current}))` with `Duration.ofSeconds({suggested})`",
                "params": {"current": "10", "suggested": "30"},
                "file_hint": "test",
            },
            {
                "description": "Add explicit wait with ExpectedConditions before element interaction",
                "template": 'Add before the failing line:\nWebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(30));\nwait.until(ExpectedConditions.visibilityOf(element));',
                "params": {},
                "file_hint": "test",
            },
            {
                "description": "Add retry logic with exponential backoff",
                "template": 'Wrap the failing operation in a retry loop:\nfor (int attempt = 0; attempt < 3; attempt++) {\n  try { /* failing code */ break; }\n  catch (TimeoutException e) {\n    if (attempt == 2) throw e;\n    Thread.sleep((long) Math.pow(2, attempt) * 1000);\n  }\n}',
                "params": {},
                "file_hint": "test",
            },
        ],
        "config_fixes": [
            {
                "description": "Increase global implicit wait in config.properties",
                "template": "Set `implicit.wait=30` in src/main/resources/config.properties",
                "file_hint": "config",
            },
            {
                "description": "Increase testng.xml parallel thread timeout",
                "template": 'Add `time-out="300000"` to the <suite> or <test> element in testng.xml',
                "file_hint": "config",
            },
        ],
        "framework_fixes": [
            {
                "description": "Update ActionEngine/FluentWait in reusableComponents to use configurable timeout",
                "template": "In ActionEngine.java, make timeout configurable via PropertiesOperations instead of hardcoded value",
                "file_hint": "utility",
            },
        ],
    },
    "NullPointerException": {
        "test_fixes": [
            {
                "description": "Add null check before WebElement interaction",
                "template": 'Add: if (element != null) { element.click(); } else { logger.error("Element not initialized"); }',
                "file_hint": "test",
            },
        ],
        "page_object_fixes": [
            {
                "description": "Verify @FindBy initialization in page object constructor",
                "template": "Ensure PageFactory.initElements(driver, this) is called before any @FindBy element usage",
                "file_hint": "page_object",
            },
        ],
        "config_fixes": [
            {
                "description": "Set retryCount in config.properties",
                "template": "Set `retryCount=1` in src/main/resources/config.properties",
                "file_hint": "config",
            },
        ],
    },
    "NoSuchElementException": {
        "test_fixes": [
            {
                "description": "Add wait for element visibility before interaction",
                "template": "Add before the failing line:\nwait.until(ExpectedConditions.visibilityOfElementLocated(By.{locator_type}(\"{locator}\")));",
                "params": {"locator_type": "id", "locator": "replace_with_actual"},
                "file_hint": "test",
            },
        ],
        "page_object_fixes": [
            {
                "description": "Update @FindBy locator strategy in page object",
                "template": "Replace `@FindBy(id = \"{old}\")` with a more robust selector like `@FindBy(xpath = \"//button[contains(text(),'{label}')]\")`",
                "params": {"old": "old_locator", "label": "Button Label"},
                "file_hint": "page_object",
            },
        ],
        "config_fixes": [
            {
                "description": "Verify URL in config.properties matches current environment",
                "template": "Check `url=http://live.techpanda.org/index.php/` in config.properties is reachable and correct",
                "file_hint": "config",
            },
        ],
    },
    "StaleElementException": {
        "test_fixes": [
            {
                "description": "Re-locate element after DOM refresh",
                "template": "Use PageFactory.refresh() pattern or re-initialize page object after DOM change:\nloginPage = new LoginPage(driver);",
                "file_hint": "test/page_object",
            },
        ],
    },
    "ElementClickIntercepted": {
        "test_fixes": [
            {
                "description": "Scroll element into view before clicking",
                "template": "Add before click:\n((JavascriptExecutor) driver).executeScript(\"arguments[0].scrollIntoView(true);\", element);\nThread.sleep(500);",
                "file_hint": "test",
            },
        ],
        "framework_fixes": [
            {
                "description": "Update ActionEngine.click() to auto-scroll into view",
                "template": "In ActionEngine.java click method, add scrollIntoView before .click(): \njs.executeScript(\"arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'})\", element);",
                "file_hint": "utility",
            },
        ],
    },
    "AssertionError": {
        "test_fixes": [
            {
                "description": "Add awaitility for async assertions",
                "template": "Replace direct assertion with: \nawait().atMost(10, TimeUnit.SECONDS).untilAsserted(() -> assertEquals(expected, actual));",
                "file_hint": "test",
            },
        ],
    },
    "ConnectionRefused": {
        "config_fixes": [
            {
                "description": "Add Selenium Grid health check before test execution",
                "template": "Add to TestBase @BeforeSuite:\ntry { driver.get(\"http://localhost:4444/wd/hub/status\"); }\ncatch (Exception e) { throw new SkipException(\"Grid not available\"); }",
                "file_hint": "config/framework",
            },
        ],
    },
}

# Default fix for unknown error types
_DEFAULT_FIXES = {
    "test_fixes": [
        {
            "description": "Add a try-catch with detailed logging around the failing code",
            "template": "Wrap the test step in:\ntry {\n  // original code\n} catch (Exception e) {\n  logger.error(\"Test step failed\", e);\n  Assert.fail(\"Step failed: \" + e.getMessage());\n}",
            "file_hint": "test",
        },
    ],
}


def _load_code_file(save_dir: str, filename_hint: str) -> str | None:
    """Try to load a code file from the save directory by partial name match."""
    code_dir = Path(save_dir) / "code"
    if not code_dir.exists():
        return None
    filename_hint = filename_hint.lower().replace("/", "_").replace("\\", "_")
    for f in code_dir.iterdir():
        if filename_hint in f.name.lower():
            try:
                return f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return None
    return None


def _compute_confidence(error_type: str, fix_category: str, code_available: bool, past_similarity: float) -> int:
    """Compute a confidence score (0-100) for a fix recommendation."""
    base = 60
    # Known error patterns get higher base
    if error_type in FIX_PATTERNS and fix_category in FIX_PATTERNS[error_type]:
        base = 70
    # Having source code available increases confidence
    if code_available:
        base += 10
    # Past similar failures increase confidence
    if past_similarity > 0.5:
        base += 15
    elif past_similarity > 0.3:
        base += 8
    # Severity adjustments happen at call site
    return min(base, 98)


@tool("Generate Fix Recommendations")
async def generate_fix_recommendations(
    analysis_report_path: str,
    save_dir: str,
    parsed_errors_json: str = "",
    past_similarity: float = 0.0,
) -> str:
    """
    Analyze the flaky test analysis report and generate specific code/configuration
    fix recommendations with confidence scores. Each recommendation targets a
    specific file type (test script, page object, utility, or config).

    Parameters:
    - analysis_report_path: Path to the analysis-report.md file
    - save_dir: Directory containing the code/ subfolder
    - parsed_errors_json: (optional) JSON output from parse_console_errors tool
    - past_similarity: Maximum similarity score from past failure RAG (0.0-1.0)

    Returns JSON with categorized fix recommendations and confidence scores.
    """
    error_summary = []

    # If parsed_errors_json provided, use it directly
    if parsed_errors_json:
        errors = _json.loads(parsed_errors_json) if isinstance(parsed_errors_json, str) else parsed_errors_json
        error_summary = errors.get("error_summary", [])

    # Fallback: parse the analysis report to extract error types
    if not error_summary:
        try:
            report_text = Path(analysis_report_path).read_text(encoding="utf-8", errors="replace")
            import re
            for line in report_text.splitlines():
                for err_type in FIX_PATTERNS:
                    if err_type in line and ("|" in line or "**" in line) and err_type not in [e.get("type") for e in error_summary]:
                        error_summary.append({
                            "type": err_type,
                            "count": 1,
                            "severity": "HIGH",
                            "category": "Auto-detected from report",
                        })
        except Exception:
            pass

    recommendations = []
    fix_id = 0

    for err in error_summary[:5]:  # top 5 error types
        error_type = err.get("type", "Unknown")
        severity = err.get("severity", "MEDIUM")
        count = err.get("count", 1)
        fix_templates = FIX_PATTERNS.get(error_type, _DEFAULT_FIXES)

        for category, fixes in fix_templates.items():
            for fix in fixes:
                fix_id += 1
                file_hint = fix.get("file_hint", "unknown")
                code_content = _load_code_file(save_dir, file_hint)
                code_available = code_content is not None and len(code_content) > 0

                confidence = _compute_confidence(error_type, category, code_available, past_similarity)
                # Boost for critical/high severity
                if severity == "CRITICAL":
                    confidence = min(confidence + 10, 98)
                elif severity == "HIGH":
                    confidence = min(confidence + 5, 98)

                rec = {
                    "id": fix_id,
                    "error_type": error_type,
                    "severity": severity,
                    "error_count": count,
                    "category": category.replace("_", " ").title(),
                    "description": fix["description"],
                    "fix_code": fix["template"],
                    "target_file_type": file_hint,
                    "confidence": confidence,
                    "confidence_label": _confidence_label(confidence),
                    "accepted": False,
                }
                recommendations.append(rec)

    # Sort by confidence descending
    recommendations.sort(key=lambda r: r["confidence"], reverse=True)

    result = {
        "recommendations": recommendations,
        "summary": f"{len(recommendations)} fix recommendations generated for {len(error_summary)} error type(s)",
        "total_fixes": len(recommendations),
        "highest_confidence": recommendations[0]["confidence"] if recommendations else 0,
    }

    # Save to disk
    rec_path = Path(save_dir) / "fix-recommendations.json"
    rec_path.write_text(_json.dumps(result, indent=2), encoding="utf-8")
    logger.info("Fix recommendations saved to %s", rec_path)

    return _json.dumps(result, indent=2)


@tool("Save Accepted Recommendations")
async def save_accepted_recommendations(
    recommendations_json: str,
    accepted_ids_json: str,
    save_dir: str,
) -> str:
    """
    Mark specific recommendations as accepted by the user and save
    the accepted list for the Fixer Agent to apply.

    Parameters:
    - recommendations_json: Full recommendations JSON from generate_fix_recommendations
    - accepted_ids_json: JSON list of recommendation IDs the user accepts (e.g. [1, 3, 5])
    - save_dir: Directory to save accepted-recommendations.json

    Returns JSON with accepted recommendation details.
    """
    all_recs = _json.loads(recommendations_json) if isinstance(recommendations_json, str) else recommendations_json
    accepted_ids = _json.loads(accepted_ids_json) if isinstance(accepted_ids_json, str) else accepted_ids_json
    recs = all_recs.get("recommendations", [])

    accepted = []
    for rec in recs:
        if rec.get("id") in accepted_ids:
            rec["accepted"] = True
            accepted.append(rec)

    accepted_data = {
        "accepted_count": len(accepted),
        "total_recommendations": len(recs),
        "accepted": accepted,
    }

    acc_path = Path(save_dir) / "accepted-recommendations.json"
    acc_path.write_text(_json.dumps(accepted_data, indent=2), encoding="utf-8")
    logger.info("Accepted %d/%d recommendations saved to %s", len(accepted), len(recs), acc_path)

    return _json.dumps(accepted_data, indent=2)


@tool("Generate Recommendations Report")
async def generate_recommendations_report(
    recommendations_json: str,
    analysis_report_path: str,
    save_dir: str,
) -> str:
    """
    Generate a human-readable markdown report of all fix recommendations
    with confidence scores and accept/reject prompts.

    Parameters:
    - recommendations_json: Output from generate_fix_recommendations
    - analysis_report_path: Path to the analysis-report.md for reference
    - save_dir: Directory to save the report

    Returns path to the saved recommendations-report.md.
    """
    data = _json.loads(recommendations_json) if isinstance(recommendations_json, str) else recommendations_json
    recs = data.get("recommendations", [])

    lines = []
    lines.append("# Fix Recommendation Report")
    lines.append("")
    lines.append(f"**Analysis:** `{analysis_report_path}`  ")
    lines.append(f"**Generated:** {__import__('datetime').datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"**Total Recommendations:** {len(recs)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## How to Use")
    lines.append("")
    lines.append("Each recommendation below has a **confidence score** and a **target file type**.")
    lines.append("Review the suggested fix code and either accept (will be auto-applied) or skip.")
    lines.append("")
    lines.append("> **Confidence Legend:**  \n")
    lines.append(">  \U0001f7e2 **90-98%** = High confidence — almost certainly correct  \n")
    lines.append(">  \U0001f7e1 **70-89%** = Medium confidence — review before accepting  \n")
    lines.append(">  \U0001f534 **<70%** = Low confidence — verify manually  \n")
    lines.append("")

    last_type = None
    for rec in recs:
        if rec["error_type"] != last_type:
            last_type = rec["error_type"]
            lines.append(f"## {rec['error_type']} ({rec['severity']}) — {rec['error_count']} occurrence(s)")
            lines.append("")

        emoji = "\U0001f7e2" if rec["confidence"] >= 90 else ("\U0001f7e1" if rec["confidence"] >= 70 else "\U0001f534")
        lines.append(f"### Recommendation #{rec['id']} {emoji} {rec['confidence']}%")
        lines.append("")
        lines.append(f"**Category:** {rec['category']}  ")
        lines.append(f"**Target Files:** {rec['target_file_type']}  ")
        lines.append(f"**Description:** {rec['description']}  ")
        lines.append("")
        lines.append("```java")
        lines.append(rec["fix_code"])
        lines.append("```")
        lines.append("")
        lines.append(f"- [ ] Accept recommendation #{rec['id']}")
        lines.append("")

    lines.append("---")
    lines.append("*Report auto-generated by TestFixer Recommendation Agent.*")

    report = "\n".join(lines)
    from .artifact_store import save_text_file
    filepath = save_text_file(Path(save_dir), "recommendations-report.md", report)
    logger.info("Recommendations report saved to %s", filepath)
    return filepath


def _confidence_label(score: int) -> str:
    if score >= 90:
        return "HIGH"
    if score >= 70:
        return "MEDIUM"
    return "LOW"

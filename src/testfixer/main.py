"""Entry point for the TestFixer multi-agent system.
Provides rich console output showing:
- Which agents are starting/completing
- Which tasks are beginning/finishing
- Step-by-step progress during execution
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Force UTF-8 for Windows — CrewAI's event bus uses emoji that break cp1252
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .crew import TestFixerCrew
from .tools.jenkins_client import managed_jenkins_client

load_dotenv()

# Configure logging — suppress noisy libs, keep our own logs
for lib in ("httpcore", "httpx", "chromadb", "urllib3", "asyncio", "mcp_jenkins", "fastmcp", "openai", "liteLLM", "crewai"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("testfixer")
logger.setLevel(logging.INFO)

# Add a clean console handler for our own logs
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))
logger.handlers.clear()
logger.addHandler(console_handler)

_START_TIME: float = 0.0


# ---------------------------------------------------------------------------
# Console formatting helpers
# ---------------------------------------------------------------------------
SEP = "=" * 64
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _elapsed() -> str:
    return f"{time.time() - _START_TIME:.1f}s"


def print_header(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{SEP}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{SEP}{RESET}")


def print_step(step_num: int, text: str) -> None:
    print(f"\n  {BOLD}{BLUE}--- Step {step_num} ---{RESET}")
    print(f"  {BOLD}{text}{RESET}")


def print_ok(text: str) -> None:
    print(f"  {GREEN}[OK]{RESET} {text}")


def print_warn(text: str) -> None:
    print(f"  {YELLOW}[WARN]{RESET} {text}")


def print_info(text: str) -> None:
    print(f"  {DIM}{text}{RESET}")


def print_result(text: str) -> None:
    print(f"  {GREEN}{BOLD}-> {text}{RESET}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run(
    job_name: str,
    build_spec: str = "latest",
    save_dir: str | None = None,
    workspace_report_path: str | None = None,
) -> str:
    """Synchronous entry point."""
    global _START_TIME
    _START_TIME = time.time()
    return asyncio.run(_arun(job_name, build_spec, save_dir, workspace_report_path))


async def _arun(
    job_name: str,
    build_spec: str = "latest",
    save_dir: str | None = None,
    workspace_report_path: str | None = None,
) -> str:
    from .tools.jenkins_rest import resolve_build_number as resolve_build

    job_name = job_name.strip()

    # Resolve build
    print_step(0, f"Resolving build '{build_spec}' for job '{job_name}'...")
    build_number = await resolve_build(job_name, build_spec)
    print_ok(f"Build resolved: {job_name} #{build_number}")

    if save_dir is None:
        base = Path(os.getenv("ARTIFACT_DOWNLOAD_DIR", "./artifacts")).resolve()
        save_dir = str(base / job_name / str(build_number))
    os.makedirs(save_dir, exist_ok=True)

    print_header(f"TestFixer Crew — {job_name} #{build_number}")
    print(f"  Source    : {build_spec}")
    print(f"  Save dir  : {save_dir}")

    llm_provider = os.getenv("LLM_PROVIDER", "gemini")
    if llm_provider == "gemini":
        llm_model = os.getenv("GEMINI_MODEL", "gemini/gemini-3.1-flash-lite")
        print(f"  LLM       : {llm_model} @ Google AI Studio")
    elif llm_provider == "groq":
        llm_model = os.getenv("GROQ_MODEL", "groq/llama-3.1-8b-instant")
        print(f"  LLM       : {llm_model} @ Groq")
    else:
        print(f"  LLM       : llama3.1:8b @ Ollama (localhost:11434)")
    print(f"  Agents    : Code Context -> Fetcher -> Analysis -> Recommendation")
    print(f"  Tasks     : fetch_code_context -> fetch_build_artifacts -> analyze_flaky_tests -> recommend_fixes")

    result = None
    async with managed_jenkins_client():
        crew = TestFixerCrew().crew()

        git_repo_url = os.getenv("GIT_REPO_URL", "")
        git_branch = os.getenv("GIT_BRANCH", "main")
        max_code_files = int(os.getenv("MAX_CODE_FILES", "100"))

        inputs = {
            "job_name": job_name,
            "build_number": str(build_number),
            "save_dir": save_dir,
            "workspace_report_path": workspace_report_path or "",
            "git_repo_url": git_repo_url,
            "git_branch": git_branch,
            "max_code_files": str(max_code_files),
        }

        print(f"\n  {BOLD}{MAGENTA}[{_ts()}] KICKING OFF CREW...{RESET}")
        result = await crew.kickoff_async(inputs=inputs)
        print(f"  {GREEN}{BOLD}[{_ts()}] CREW COMPLETED ({_elapsed()}){RESET}")

    if result is None:
        raise RuntimeError("Crew run produced no result — check logs above for errors.")

    # Footer
    print(f"\n{BOLD}{GREEN}{SEP}{RESET}")
    print(f"{BOLD}{GREEN}  RUN COMPLETE — {job_name} #{build_number}{RESET}")
    print(f"  Artifacts : {save_dir}")
    report_path = f"{save_dir}/analysis-report.md"
    if os.path.exists(report_path):
        print(f"  Report    : {report_path}")
    rec_path = f"{save_dir}/recommendations-report.md"
    if os.path.exists(rec_path):
        print(f"  Fix Recs  : {rec_path}")
    print(f"{BOLD}{GREEN}{SEP}{RESET}\n")

    return str(result)


def run_cli() -> None:
    """CLI entry point registered in pyproject.toml [project.scripts]."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TestFixer — fix flaky automation tests via multi-agent analysis"
    )
    parser.add_argument("job_name", help="Jenkins job name")
    parser.add_argument(
        "build_spec",
        nargs="?",
        default="latest",
        help="Build specifier: a number, 'latest' (default), 'lastFailed', 'lastStable', 'lastSuccessful', 'lastUnstable', 'lastCompleted'",
    )
    parser.add_argument(
        "--save-dir",
        default=None,
        help="Directory to save artifacts (default: ./artifacts/<job>/<build>)",
    )
    parser.add_argument(
        "--workspace-report",
        default=os.getenv("WORKSPACE_REPORT_PATH", ""),
        help="Workspace-relative path to HTML report. Also set via WORKSPACE_REPORT_PATH in .env",
    )

    args = parser.parse_args()

    try:
        result = run(args.job_name, args.build_spec, args.save_dir, args.workspace_report)
        print(result)
    except KeyboardInterrupt:
        print(f"\n{RED}Interrupted.{RESET}")
        sys.exit(130)
    except Exception:
        logger.exception("Crew run failed")
        sys.exit(1)


if __name__ == "__main__":
    run_cli()

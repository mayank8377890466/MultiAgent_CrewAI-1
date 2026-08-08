import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values, load_dotenv

# Force UTF-8 for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_DIR / ".env"

ENV_DEFAULTS = {
    "JENKINS_URL": "http://localhost:8080",
    "JENKINS_USERNAME": "",
    "JENKINS_PASSWORD": "",
    "LLM_PROVIDER": "gemini",
    "GEMINI_MODEL": "gemini/gemini-3.1-flash-lite",
    "GEMINI_API_KEY": "",
    "GROQ_MODEL": "groq/llama-3.1-8b-instant",
    "GROQ_API_KEY": "",
    "EMBEDDING_MODEL": "all-MiniLM-L6-v2",
    "GIT_REPO_URL": "",
    "GIT_BRANCH": "main",
    "GITHUB_TOKEN": "",
    "CHROMA_DB_DIR": "./chroma_db",
    "ARTIFACT_DOWNLOAD_DIR": "./artifacts",
    "MAX_CODE_FILES": "100",
}

AGENT_NAMES = [
    "code_context_agent",
    "fetcher_agent",
    "analysis_agent",
    "recommendation_agent",
]

TASK_NAMES = [
    "fetch_code_context",
    "fetch_build_artifacts",
    "analyze_flaky_tests",
    "recommend_fixes",
]

AGENT_LABELS = [
    "1. Code Context Agent",
    "2. Fetcher Agent",
    "3. Analysis Agent",
    "4. Recommendation Agent",
]


def _load_env() -> dict:
    if ENV_FILE.exists():
        return dict(dotenv_values(ENV_FILE))
    return {}


def _save_env(values: dict) -> None:
    lines = []
    keys_written: set = set()

    if ENV_FILE.exists():
        existing = ENV_FILE.read_text(encoding="utf-8").splitlines()
        for line in existing:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in values:
                    lines.append(f"{key}={values[key]}")
                    keys_written.add(key)
                else:
                    lines.append(line)

    for key, val in values.items():
        if key not in keys_written:
            lines.append(f"{key}={val}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _env_field(key: str, label: str, password: bool = False) -> str:
    current = st.session_state.get(f"env_{key}", "")
    if password:
        val = st.text_input(
            label,
            value=current,
            type="password",
            key=f"input_{key}",
            placeholder=ENV_DEFAULTS.get(key, ""),
        )
    else:
        val = st.text_input(
            label,
            value=current,
            key=f"input_{key}",
            placeholder=ENV_DEFAULTS.get(key, ""),
        )
    st.session_state[f"env_{key}"] = val
    return val


def _select_field(key: str, label: str, options: list[str]) -> str:
    current = st.session_state.get(f"env_{key}", "")
    idx = 0
    if current in options:
        idx = options.index(current)
    val = st.selectbox(label, options, index=idx, key=f"input_{key}")
    st.session_state[f"env_{key}"] = val
    return val


def _parse_save_dir(output: str, job_name: str) -> str | None:
    for line in output.splitlines():
        m = re.search(r"Save dir\s*:\s*(.+)", line)
        if m:
            return m.group(1).strip()
    for line in output.splitlines():
        m = re.search(r"Artifacts\s*:\s*(.+)", line)
        if m:
            return m.group(1).strip()
    base = Path(ENV_DEFAULTS["ARTIFACT_DOWNLOAD_DIR"]).resolve()
    return str(base / job_name) if job_name else None


def _parse_agent_sections(output: str) -> list[dict]:
    sections = []
    agent_pattern = re.compile(
        r"# Agent:\s*(.+?)\n.*?## Task:\s*(.+?)\n",
        re.DOTALL,
    )
    for m in agent_pattern.finditer(output):
        sections.append({"agent": m.group(1).strip(), "task": m.group(2).strip()})
    return sections


def _run_pipeline_stream(job_name: str, build_spec: str, container: object) -> tuple[str, int]:
    cmd = f'uv run testfixer "{job_name}" "{build_spec}"'

    kwargs: dict = dict(
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["shell"] = True

    proc = subprocess.Popen(cmd, **kwargs)

    output_lines: list[str] = []

    def _reader():
        for line in iter(proc.stdout.readline, ""):
            output_lines.append(line)
        proc.stdout.close()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    last_len = 0
    while proc.poll() is None or t.is_alive():
        if len(output_lines) > last_len:
            new_text = "".join(output_lines[last_len:])
            container.code(new_text, language="ansi")
            last_len = len(output_lines)
        t.join(timeout=0.2)

    t.join()
    if len(output_lines) > last_len:
        container.code("".join(output_lines[last_len:]), language="ansi")

    return "".join(output_lines), proc.returncode


def main() -> None:
    subprocess.run(["streamlit", "run", __file__] + sys.argv[1:])


# ── Streamlit app code ──

st.set_page_config(page_title="TestFixer", layout="wide")
st.title("Automation Test Fixer")

if "env_loaded" not in st.session_state:
    env = _load_env()
    for key in ENV_DEFAULTS:
        st.session_state[f"env_{key}"] = env.get(key, ENV_DEFAULTS[key])
    st.session_state["env_loaded"] = True

col_left, col_right = st.columns([3, 7])

# ── LEFT PANEL (30%) ──
with col_left:
    st.header("Configuration")

    with st.expander("Jenkins", expanded=True):
        _env_field("JENKINS_URL", "URL")
        _env_field("JENKINS_USERNAME", "Username")
        _env_field("JENKINS_PASSWORD", "API Key / Password", password=True)

    with st.expander("LLM", expanded=True):
        provider = _select_field("LLM_PROVIDER", "Provider", ["gemini", "groq", "ollama"])
        if provider == "gemini":
            _env_field("GEMINI_MODEL", "Model")
            _env_field("GEMINI_API_KEY", "API Key", password=True)
        elif provider == "groq":
            _env_field("GROQ_MODEL", "Model")
            _env_field("GROQ_API_KEY", "API Key", password=True)
        else:
            st.info("Ollama uses localhost:11434 — no API key needed.")

    with st.expander("Embedding", expanded=True):
        _env_field("EMBEDDING_MODEL", "Model Name")

    with st.expander("Git", expanded=True):
        _env_field("GIT_REPO_URL", "Repo URL")
        _env_field("GIT_BRANCH", "Branch")
        _env_field("GITHUB_TOKEN", "Token", password=True)

    if st.button("Save Configuration", use_container_width=True):
        values = {}
        for key in ENV_DEFAULTS:
            val = st.session_state.get(f"env_{key}", "")
            if val:
                values[key] = val
        _save_env(values)
        load_dotenv(override=True)
        st.success("Saved to .env")

# ── RIGHT PANEL (70%) ──
with col_right:
    st.header("Run Pipeline")

    job_name = st.text_input("Jenkins Job Name", key="job_name")
    build_spec = st.text_input("Build Spec", value="latest", key="build_spec")

    if st.button("Run TestFixer", type="primary", disabled=not job_name):
        tab_console, tab_results = st.tabs(["Console Logs", "Results"])

        with tab_console:
            log_container = st.empty()

        with tab_results:
            results_container = st.container()

        output, rc = _run_pipeline_stream(str(job_name), str(build_spec), log_container)

        with tab_results:
            results_container.empty()
            if rc != 0:
                results_container.error(f"Pipeline failed with exit code {rc}")
            else:
                results_container.success("Pipeline completed successfully!")

                save_dir = _parse_save_dir(output, str(job_name))
                results_container.caption(f"Output directory: `{save_dir}`")

                for idx, (agent, label) in enumerate(zip(AGENT_NAMES, AGENT_LABELS)):
                    with results_container.expander(label, expanded=(idx == 0)):
                        analysis_path = None
                        rec_path = None
                        if save_dir:
                            analysis_path = Path(save_dir) / "analysis-report.md"
                            rec_path = Path(save_dir) / "recommendations-report.md"

                        if agent == "code_context_agent":
                            st.markdown("**Task:** `fetch_code_context` — Fetches source code from Git repo, filters relevant files, indexes into ChromaDB.")
                            code_dir = Path(save_dir) / "code" if save_dir else None
                            if code_dir and code_dir.exists():
                                code_files = list(code_dir.rglob("*.*"))
                                st.metric("Code files indexed", len([f for f in code_files if f.is_file()]))
                            else:
                                st.info("Code context directory not found — check Console Logs for details.")

                        elif agent == "fetcher_agent":
                            st.markdown("**Task:** `fetch_build_artifacts` — Downloads Jenkins build artifacts (logs, reports, screenshots), indexes into ChromaDB.")
                            if save_dir:
                                console_log = Path(save_dir) / "console-output.txt"
                                meta = Path(save_dir) / "build-metadata.json"
                                if console_log.exists():
                                    with st.expander("Console Output (first 100 lines)"):
                                        st.code(console_log.read_text(encoding="utf-8", errors="replace")[:5000])
                                if meta.exists():
                                    import json
                                    meta_data = json.loads(meta.read_text(encoding="utf-8"))
                                    cols = st.columns(3)
                                    cols[0].metric("Status", meta_data.get("status", "N/A"))
                                    cols[1].metric("Duration", f"{meta_data.get('duration_ms', 0) / 1000:.1f}s")
                                    cols[2].metric("Build #", meta_data.get("build_number", "N/A"))
                                    if "test_summary" in meta_data:
                                        ts = meta_data["test_summary"]
                                        st.json(ts)
                            else:
                                st.info("Artifact directory not found — check Console Logs for details.")

                        elif agent == "analysis_agent":
                            st.markdown("**Task:** `analyze_flaky_tests` — Parses console errors, cross-references with past failures (RAG) and source code, generates analysis report.")
                            if analysis_path and analysis_path.exists():
                                with st.expander("Analysis Report"):
                                    st.markdown(analysis_path.read_text(encoding="utf-8", errors="replace"))
                            else:
                                st.warning("analysis-report.md not found")

                        elif agent == "recommendation_agent":
                            st.markdown("**Task:** `recommend_fixes` — Generates code/config fix recommendations with confidence scores.")
                            if rec_path and rec_path.exists():
                                with st.expander("Recommendations Report"):
                                    st.markdown(rec_path.read_text(encoding="utf-8", errors="replace"))
                            else:
                                st.warning("recommendations-report.md not found")

# Automation Test Fixer — Multi-Agent System

A CrewAI-based multi-agent system that fetches Jenkins build artifacts and
analyzes them to identify root causes of flaky automation test failures.

## Architecture

```
Fetcher Agent  ──(BuildData)──→  Analysis Agent
     │                               (Phase 2)
     │ MCP stdio
     ▼
 mcp-jenkins (subprocess)
     │
     │ REST API
     ▼
 Jenkins Server
```

## Prerequisites

- Python 3.11+
- `uv` package manager (`pip install uv`)
- `uvx` available in PATH (comes with `uv`)
- Access to a Jenkins server with API token authentication
- Groq API key

## Setup

```bash
# Clone and enter project
cd AutomationTestFixer_MultiAgent

# Create virtual environment and install
uv venv
uv pip install -e .

# Configure credentials
cp .env .env  # edit with your values
```

Edit `.env`:
```
JENKINS_URL=https://jenkins.yourcompany.com
JENKINS_USERNAME=your_username
JENKINS_PASSWORD=your_api_token
GROQ_API_KEY=gsk_your_groq_key
ARTIFACT_DOWNLOAD_DIR=./artifacts
```

## Usage

### CLI

```bash
uv run testfixer <job_name> <build_number> [--save-dir ./custom/path]
```

Example:
```bash
uv run testfixer "Selenium-Tests" 142
```

### Python API

```python
from testfixer.main import run

result = run("Selenium-Tests", 142)
print(result)
```

## Project Structure

```
src/testfixer/
├── main.py              # Entry point + CLI
├── crew.py              # CrewAI crew definition
├── config/
│   ├── agents.yaml      # Agent roles and goals
│   └── tasks.yaml       # Task descriptions
├── tools/
│   ├── jenkins_client.py    # MCP stdio client for mcp-jenkins
│   ├── artifact_store.py    # File download + storage helpers
│   └── fetcher_tools.py     # 10 CrewAI @tool functions
├── models/
│   └── build_data.py    # Pydantic BuildData data contract
└── tests/
    └── test_fetcher.py  # Integration tests
```

## How It Works

1. **Fetcher Agent** connects to `mcp-jenkins` via MCP stdio subprocess
2. It calls 10 tools to fetch: build info, console log, test report, screenshots,
   TestNG/JUnit XML, HTML report, and build metadata
3. All artifacts are saved to `./artifacts/<job_name>/<build_number>/`
4. A `BuildData` pydantic object is passed to the **Analysis Agent** (Phase 2 stub)
5. Analysis Agent will parse the artifacts and produce a root cause report

## Testing

Integration tests require a live Jenkins connection configured in `.env`.

Set optional overrides:
```bash
export TEST_JOB_NAME="my-test-job"
export TEST_BUILD_NUMBER="42"
```

Run:
```bash
uv run pytest src/testfixer/tests/test_fetcher.py -v -m integration
```

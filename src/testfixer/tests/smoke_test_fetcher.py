"""
Fetcher Agent smoke test — validates each layer independently:
1. .env configuration
2. Jenkins REST API connectivity (resolve build number)
3. MCP Jenkins stdio connection
4. Individual tool calls (build info, console, test report, artifacts)
5. Artifact downloads to disk
"""

import asyncio
import json as _json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()


def check_env():
    """Validate required environment variables."""
    required = {
        "JENKINS_URL": "Jenkins server URL (e.g. http://localhost:8080)",
        "JENKINS_USERNAME": "Jenkins username",
        "JENKINS_PASSWORD": "Jenkins API token or password",
        "GROQ_API_KEY": "Groq API key for LLM",
    }
    optional = {
        "ARTIFACT_DOWNLOAD_DIR": "Local directory for downloaded artifacts",
        "WORKSPACE_REPORT_PATH": "Workspace-relative path for HTML reports",
    }

    print("\n" + "=" * 60)
    print("  CHECK 1: Environment Configuration")
    print("=" * 60)

    all_ok = True
    for key, desc in required.items():
        value = os.getenv(key, "")
        if value and value not in ("your_username", "your_api_token", "gsk_xxxxxxxxxxxx"):
            print(f"  [OK] {key} = {value[:30]}{'...' if len(value) > 30 else ''}")
        else:
            print(f"  [FAIL] {key} = {value or '(empty)'}  -- {desc}")
            all_ok = False

    print()
    for key, desc in optional.items():
        value = os.getenv(key, "")
        if value:
            print(f"  [OK] {key} = {value}")
        else:
            print(f"  [SKIP] {key} = (not set)  -- {desc}")

    return all_ok


async def check_rest_api(job_name: str, build_spec: str = "latest"):
    """Verify Jenkins REST API can resolve a build number."""
    from testfixer.tools.jenkins_rest import resolve_build_number

    print("\n" + "=" * 60)
    print("  CHECK 2: Jenkins REST API - Resolve Build Number")
    print("=" * 60)

    try:
        num = await resolve_build_number(job_name, build_spec)
        print(f"  [OK] Resolved '{build_spec}' -> build #{num}")
        return num
    except Exception as e:
        print(f"  [FAIL] Could not resolve '{build_spec}' for {job_name}: {e}")
        return None


async def check_mcp_and_tools(job_name: str, build_number: int):
    """Verify MCP connection and run tool calls in a single session."""

    from testfixer.tools.jenkins_client import managed_jenkins_client
    from testfixer.tools.artifact_store import decode_artifact_content

    print("\n" + "=" * 60)
    print("  CHECK 3: MCP Connection + Tool Calls")
    print("=" * 60)

    try:
        async with managed_jenkins_client() as client:

            # List tools
            tools = await client.list_tools()
            print(f"  [OK] Connected. {len(tools)} tools available.")
            for t in sorted(tools, key=lambda x: x["name"])[:5]:
                print(f"       - {t['name']}")
            if len(tools) > 5:
                print(f"       ... and {len(tools) - 5} more")

            job = job_name.strip()
            tool_tests = [
                ("get_build", {"fullname": job, "number": build_number}),
                ("get_build_console_output", {"fullname": job, "number": build_number}),
                ("get_build_test_report", {"fullname": job, "number": build_number}),
                ("get_all_build_artifacts", {"fullname": job, "number": build_number}),
            ]

            print()
            for tool_name, args in tool_tests:
                try:
                    result = await client.call_tool(tool_name, args)
                    text = decode_artifact_content(result) if isinstance(result, dict) else str(result)
                    preview = (text[:100] + "...") if text and len(text) > 100 else text
                    print(f"  [OK] {tool_name} -> {preview}")
                except Exception as e:
                    print(f"  [FAIL] {tool_name}: {e}")

            return True

    except Exception as e:
        print(f"  [FAIL] MCP connection failed: {e}")
        return False


async def check_artifact_download(job_name: str, build_number: int):
    """Test downloading actual artifacts to disk."""
    from testfixer.tools.jenkins_client import managed_jenkins_client
    from testfixer.tools.artifact_store import (
        get_download_dir, save_text_file, save_binary_file,
        decode_artifact_content, decode_binary_from_base64,
    )

    print("\n" + "=" * 60)
    print("  CHECK 4: Artifact Downloads to Disk")
    print("=" * 60)

    dl_dir = get_download_dir(job_name, build_number)
    job = job_name.strip()

    async with managed_jenkins_client() as client:

        # Console output
        try:
            result = await client.call_tool(
                "get_build_console_output",
                {"fullname": job, "number": build_number},
            )
            text = decode_artifact_content(result)
            if text:
                path = save_text_file(dl_dir, "console-output.txt", text)
                size = Path(path).stat().st_size
                print(f"  [OK] Console log saved: {path} ({size} bytes)")
            else:
                print("  [WARN] Console log was empty")
        except Exception as e:
            print(f"  [FAIL] Console log: {e}")

        # Artifacts listing + download first few
        try:
            art_result = await client.call_tool(
                "get_all_build_artifacts",
                {"fullname": job, "number": build_number},
            )
            content = getattr(art_result, "content", []) or []
            artifact_names = []
            if isinstance(content, list):
                for item in content:
                    text = getattr(item, "text", None)
                    if text is None and isinstance(item, dict):
                        text = item.get("text", "")
                    if text:
                        artifact_names.extend(text.strip().splitlines())
                    elif isinstance(item, str):
                        artifact_names.extend(item.strip().splitlines())

            print(f"  [INFO] Found {len(artifact_names)} artifacts: {artifact_names[:5]}{'...' if len(artifact_names) > 5 else ''}")

            html_done, xml_done, screenshot_count = False, False, 0
            for name in artifact_names[:10]:
                try:
                    dl_result = await client.call_tool("get_build_artifact", {
                        "fullname": job,
                        "number": build_number,
                        "relative_path": name,
                    })
                    raw = decode_binary_from_base64(dl_result)
                    if raw is None:
                        continue
                    path = save_binary_file(dl_dir, name, raw)
                    size = Path(path).stat().st_size

                    if name.lower().endswith((".html", ".htm")) and not html_done:
                        print(f"  [OK] HTML report: {path} ({size} bytes)")
                        html_done = True
                    elif name.lower().endswith(".xml") and not xml_done:
                        print(f"  [OK] XML report: {path} ({size} bytes)")
                        xml_done = True
                    elif name.lower().endswith(".png"):
                        screenshot_count += 1
                except Exception:
                    continue

            if screenshot_count:
                print(f"  [OK] Screenshots downloaded: {screenshot_count}")
        except Exception as e:
            print(f"  [FAIL] Artifact listing: {e}")

        # Test report as JSON
        try:
            report = await client.call_tool(
                "get_build_test_report",
                {"fullname": job, "number": build_number},
            )
            content = _json.dumps(report, indent=2, default=str)
            path = save_text_file(dl_dir, "test-report.json", content)
            print(f"  [OK] Test report saved: {path}")
        except Exception as e:
            print(f"  [WARN] Test report: {e}")

    # Try workspace download if configured
    workspace_path = os.getenv("WORKSPACE_REPORT_PATH", "")
    if workspace_path:
        print("\n" + "=" * 60)
        print("  CHECK 5: Workspace HTML Report Download")
        print("=" * 60)

        from testfixer.tools.jenkins_rest import download_workspace_file, download_workspace_dir
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="fetcher_smoke_")

        try:
            if workspace_path.endswith("/") or "." not in workspace_path.split("/")[-1]:
                paths = await download_workspace_dir(job_name, workspace_path, tmpdir)
                if paths:
                    for p in paths[:5]:
                        size = Path(p).stat().st_size
                        print(f"  [OK] Downloaded: {p} ({size} bytes)")
                    if len(paths) > 5:
                        print(f"  [OK] ... and {len(paths) - 5} more files")
                else:
                    print(f"  [WARN] No HTML files found in workspace/{workspace_path}")
            else:
                out = os.path.join(tmpdir, os.path.basename(workspace_path))
                path = await download_workspace_file(job_name, workspace_path, out)
                size = Path(path).stat().st_size
                print(f"  [OK] Downloaded: {path} ({size} bytes)")
        except Exception as e:
            print(f"  [FAIL] Workspace download: {e}")
    else:
        print("\n  [SKIP] No WORKSPACE_REPORT_PATH configured")


async def main():
    print("\n" + "#" * 60)
    print("#  Fetcher Agent Smoke Test")
    print("#" * 60)

    if not check_env():
        print("\n  Please update .env with valid credentials and re-run.")
        return

    job_name = (os.getenv("TEST_JOB_NAME") or input("\n  Enter Jenkins job name: ").strip()).strip()
    if not job_name:
        print("  No job name provided. Exiting.")
        return

    build_spec = os.getenv("TEST_BUILD_SPEC", "latest")

    build_number = await check_rest_api(job_name, build_spec)
    if build_number is None:
        return

    mcp_ok = await check_mcp_and_tools(job_name, build_number)
    if not mcp_ok:
        return

    await check_artifact_download(job_name, build_number)

    dl_dir = Path(os.getenv("ARTIFACT_DOWNLOAD_DIR", "./artifacts")).resolve()
    print("\n" + "=" * 60)
    print("  Smoke test complete!")
    print(f"  Artifacts saved to: {dl_dir / job_name / str(build_number)}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

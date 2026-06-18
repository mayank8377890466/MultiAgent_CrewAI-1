"""Integration test for the Fetcher Agent against a real Jenkins server.
Requires a valid .env file with JENKINS_URL, JENKINS_USERNAME, JENKINS_PASSWORD.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Ensure src/ is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from testfixer.tools.jenkins_client import JenkinsMCPClient
from testfixer.tools.artifact_store import (
    get_download_dir,
    save_text_file,
    decode_artifact_content,
    decode_binary_from_base64,
)

load_dotenv()

JOB_NAME = os.getenv("TEST_JOB_NAME", "test-pipeline")
BUILD_NUMBER = int(os.getenv("TEST_BUILD_NUMBER", "1"))


@pytest.fixture
async def client():
    async with JenkinsMCPClient() as client:
        yield client


@pytest.mark.integration
class TestJenkinsMCPConnection:
    async def test_connection_and_list_tools(self, client):
        tools = await client.list_tools()
        assert len(tools) > 0
        tool_names = {t["name"] for t in tools}
        expected = {"get_build", "get_build_console_output", "get_build_test_report", "get_all_build_artifacts"}
        assert expected.issubset(tool_names)

    async def test_get_build(self, client):
        result = await client.call_tool("get_build", {"job_name": JOB_NAME, "build_number": BUILD_NUMBER})
        assert result is not None

    async def test_console_output(self, client):
        result = await client.call_tool("get_build_console_output", {"job_name": JOB_NAME, "build_number": BUILD_NUMBER})
        text = decode_artifact_content(result)
        assert text is not None
        assert len(text) > 0

    async def test_test_report(self, client):
        result = await client.call_tool("get_build_test_report", {"job_name": JOB_NAME, "build_number": BUILD_NUMBER})
        assert result is not None

    async def test_list_artifacts(self, client):
        result = await client.call_tool("get_all_build_artifacts", {"job_name": JOB_NAME, "build_number": BUILD_NUMBER})
        assert result is not None


@pytest.mark.integration
class TestArtifactDownloads:
    async def test_download_artifact(self, client):
        artifacts_result = await client.call_tool(
            "get_all_build_artifacts", {"job_name": JOB_NAME, "build_number": BUILD_NUMBER}
        )
        # Try to get artifact names
        content = artifacts_result.get("content", []) if isinstance(artifacts_result, dict) else []

        artifact_names = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    lines = item["text"].strip().splitlines()
                    artifact_names.extend(lines)

        if not artifact_names:
            pytest.skip("No artifacts found in build")

        first_artifact = artifact_names[0]
        result = await client.call_tool(
            "get_build_artifact",
            {"job_name": JOB_NAME, "build_number": BUILD_NUMBER, "artifact_name": first_artifact},
        )
        raw = decode_binary_from_base64(result)
        assert raw is not None
        assert len(raw) > 0

        # Save to disk and verify
        dl_dir = get_download_dir(JOB_NAME, BUILD_NUMBER)
        filepath = save_text_file(dl_dir, "test_artifact_download.txt", raw.decode("utf-8", errors="replace"))
        assert Path(filepath).exists()
        assert Path(filepath).stat().st_size > 0


@pytest.mark.integration
class TestBuildDataModel:
    async def test_build_data_validation(self):
        from testfixer.models.build_data import BuildData, ArtifactPaths

        data = BuildData(
            job_name=JOB_NAME,
            build_number=BUILD_NUMBER,
            build_url="https://jenkins.example.com/job/test/1",
            status="SUCCESS",
            timestamp="2026-01-01T00:00:00Z",
            duration_ms=60000,
            artifacts=ArtifactPaths(
                html_report="/tmp/report.html",
                screenshots=["/tmp/screenshot1.png"],
                console_log="/tmp/console.txt",
                testng_xml="/tmp/testng-results.xml",
                metadata_json="/tmp/build-metadata.json",
            ),
            test_summary={"passed": 10, "failed": 0, "skipped": 1},
            console_summary="Build started... tests passed.",
        )

        dumped = data.model_dump_json()
        loaded = BuildData.model_validate_json(dumped)
        assert loaded.job_name == JOB_NAME
        assert loaded.build_number == BUILD_NUMBER


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])

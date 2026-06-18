from pydantic import BaseModel, Field
from typing import Optional


class ArtifactPaths(BaseModel):
    """Local paths to downloaded build artifacts."""
    html_report: Optional[str] = None
    screenshots: list[str] = Field(default_factory=list)
    console_log: Optional[str] = None
    testng_xml: Optional[str] = None
    junit_xml: Optional[str] = None
    metadata_json: Optional[str] = None


class BuildData(BaseModel):
    """Data contract passed from Fetcher Agent to Analysis Agent."""
    job_name: str
    build_number: int
    build_url: str
    status: str
    timestamp: str
    duration_ms: int = 0
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    test_summary: dict = Field(default_factory=dict)
    console_summary: str = ""

"""
Manages the mcp-jenkins subprocess via MCP stdio transport.
Provides a singleton-like async context manager that spawns uvx mcp-jenkins
once per crew run and reuses the session across all tool calls.
"""

import os
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncGenerator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

_client_var: ContextVar["JenkinsMCPClient | None"] = ContextVar("mcp_client", default=None)
_main_loop: asyncio.AbstractEventLoop | None = None


def _get_env_or_raise(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


class JenkinsMCPClient:
    """Holds an active MCP session to mcp-jenkins.
    Created by managed_jenkins_client() — do not instantiate directly.
    """

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._active = True

    @property
    def session(self) -> ClientSession:
        if not self._active or self._session is None:
            raise RuntimeError("JenkinsMCPClient session is not active.")
        return self._session

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._active:
            raise RuntimeError("JenkinsMCPClient session is not active.")
        logger.debug("Calling MCP tool %s with args %s", tool_name, arguments)
        return await self._session.call_tool(tool_name, arguments)

    def call_tool_sync(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Thread-safe sync bridge: dispatches call_tool to the main event loop."""
        if _main_loop is None:
            raise RuntimeError("Main event loop not set. Must be inside managed_jenkins_client().")
        future = asyncio.run_coroutine_threadsafe(
            self.call_tool(tool_name, arguments), _main_loop
        )
        return future.result(timeout=60)

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._session.list_tools()
        return [{"name": t.name, "description": t.description} for t in result.tools]

    def _close(self) -> None:
        self._active = False
        self._session = None


def set_active_client(client: JenkinsMCPClient | None) -> None:
    _client_var.set(client)


def get_active_client() -> JenkinsMCPClient:
    client = _client_var.get()
    if client is None:
        raise RuntimeError(
            "No active JenkinsMCPClient found. Use managed_jenkins_client() context."
        )
    return client


@asynccontextmanager
async def managed_jenkins_client() -> AsyncGenerator[JenkinsMCPClient, None]:
    """Properly nests stdio_client + ClientSession async contexts."""
    url = _get_env_or_raise("JENKINS_URL")
    username = _get_env_or_raise("JENKINS_USERNAME")
    password = _get_env_or_raise("JENKINS_PASSWORD")

    server_params = StdioServerParameters(
        command="uvx",
        args=[
            "mcp-jenkins",
            "--jenkins-url", url,
            "--jenkins-username", username,
            "--jenkins-password", password,
            "--jenkins-timeout", "30",
            "--transport", "stdio",
        ],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            global _main_loop
            _main_loop = asyncio.get_running_loop()
            client = JenkinsMCPClient(session)
            set_active_client(client)
            logger.info("JenkinsMCPClient connected. Available tools: %d", len(await client.list_tools()))
            try:
                yield client
            finally:
                client._close()
                set_active_client(None)
                _main_loop = None
                logger.info("JenkinsMCPClient session closed.")

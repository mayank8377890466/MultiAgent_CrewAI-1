"""Debug script for mcp-jenkins subprocess."""
import asyncio
import os
import sys

sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv
load_dotenv()

async def test_raw_mcp():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    url = os.getenv("JENKINS_URL", "http://localhost:8080")
    user = os.getenv("JENKINS_USERNAME")
    pwd = os.getenv("JENKINS_PASSWORD")

    print(f"URL: {url}")
    print(f"User: {user}")
    print(f"Pass: {'***' if pwd else 'MISSING'}")

    server_params = StdioServerParameters(
        command="uvx",
        args=[
            "mcp-jenkins",
            "--jenkins-url", url,
            "--jenkins-username", user,
            "--jenkins-password", pwd,
            "--transport", "stdio",
        ],
    )

    print("Spawning mcp-jenkins via uvx...")
    try:
        async with stdio_client(server_params) as (read, write):
            print("Streams opened, initializing session...")
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Session initialized!")

                tools = await session.list_tools()
                print(f"Tools: {len(tools.tools)}")
                for t in tools.tools[:5]:
                    print(f"  - {t.name}")

                result = await session.call_tool("get_all_items", {})
                print(f"get_all_items result type: {type(result)}")
                print(f"Keys: {result.keys() if hasattr(result, 'keys') else 'N/A'}")
                print("SUCCESS")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_raw_mcp())

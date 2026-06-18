"""Inspect full mcp-jenkins tool schemas."""
import asyncio, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv; load_dotenv()
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    sp = StdioServerParameters(
        command='uvx', args=['mcp-jenkins',
        '--jenkins-url', os.getenv('JENKINS_URL',''),
        '--jenkins-username', os.getenv('JENKINS_USERNAME',''),
        '--jenkins-password', os.getenv('JENKINS_PASSWORD',''),
        '--jenkins-timeout', '30', '--transport', 'stdio'])
    async with stdio_client(sp) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            tools = await s.list_tools()
            for t in tools.tools:
                if t.name in ('get_build','get_build_console_output','get_build_test_report','get_all_build_artifacts','get_build_artifact'):
                    print(f"\n=== {t.name} ===")
                    if hasattr(t, 'inputSchema') and t.inputSchema:
                        print(json.dumps(t.inputSchema, indent=2, default=str))
                    if hasattr(t, 'description'):
                        print(f"Desc: {t.description}")

asyncio.run(main())

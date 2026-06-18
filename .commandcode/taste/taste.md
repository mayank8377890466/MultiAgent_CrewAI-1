# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# crewai
- CrewAI 1.14.x `@tool` decorator only supports synchronous functions — async `@tool` functions silently hang. Bridge async calls via per-thread event loop: `asyncio.new_event_loop().run_until_complete(coro)` from a sync wrapper. Confidence: 0.80
- Groq rejects `@tool` functions with zero parameters — the generated JSON schema has `required` but no `properties`. Always give `@tool` functions at least one parameter with a default value when targeting Groq. Confidence: 0.75

# llm
See [llm/taste.md](llm/taste.md)
# architecture
- Scaffold full CrewAI project structure (agents + tasks + crew) even when implementing agents incrementally. Confidence: 0.70
- Use mcp-jenkins via MCP stdio subprocess (not direct REST API) for Jenkins integration. Confidence: 0.70
- Nest MCP stdio_client + ClientSession with proper `async with` blocks (never manually call __aenter__/__aexit__ on them). Confidence: 0.70

# mcp-jenkins
- mcp-jenkins build tools expect `fullname` (plain job name) + `number` (build number) as separate params. For get_build_artifact, use `relative_path` not `artifact_name`. Always inspect tool inputSchema at runtime to verify. Confidence: 0.70

# tool-usage
- Avoid multiple simultaneous `edit_file` calls targeting the same file — some edits may silently fail. When editing the same file in multiple places, issue edits sequentially (one per message) and verify with a read after each batch. Confidence: 0.70

# windows
- CrewAI's event bus prints emoji characters that crash Windows' cp1252/Charmap codec. Force UTF-8 at the top of main.py: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` and same for stderr, guarded by `if sys.platform == "win32"`. Confidence: 0.75


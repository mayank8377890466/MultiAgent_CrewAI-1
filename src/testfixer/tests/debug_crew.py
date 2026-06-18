"""Debug: test Groq LLM + crew kickoff without MCP."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv()

from crewai import LLM, Agent, Task, Crew, Process

async def main():
    llm = LLM(model="groq/llama-3.3-70b-versatile", temperature=0.1)
    
    agent = Agent(
        role="Test Agent",
        goal="Say hello and confirm the LLM is working.",
        backstory="You are a simple test agent.",
        llm=llm,
        verbose=True,
    )
    task = Task(
        description="Say 'Hello from Groq! The LLM is working.'",
        expected_output="A greeting confirming LLM works",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
    
    print("Kicking off crew...")
    try:
        result = await crew.kickoff_async()
        print(f"Result: {result}")
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())

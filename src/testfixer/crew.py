"""CrewAI crew definition — ties Code Context, Fetcher and Analysis agents together."""

import os

from dotenv import load_dotenv

# Monkey-patch: CrewAI 1.14.x injects cache_breakpoint into ALL providers,
# but only Anthropic supports it. This strips it for Groq/OpenAI-compatible.
# See: https://github.com/crewAIInc/crewAI/issues/5886
import crewai.llms.cache as _crewai_cache

_crewai_cache.mark_cache_breakpoint = lambda msg: msg

_ = load_dotenv()

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task

from .tools.code_fetcher import (
    fetch_repo_file_tree,
    filter_relevant_code_files,
    fetch_code_files,
)
from .tools.code_indexer import (
    index_code_to_knowledge,
    query_code_knowledge,
    get_code_knowledge_stats,
)
from .tools.fetcher_tools import (
    fetch_build_info,
    fetch_console_output,
    fetch_test_report,
    fetch_all_artifacts,
    download_artifacts,
    download_workspace_html,
    download_all_workspace_html,
    build_metadata_json,
)
from .tools.index_artifacts import (
    index_build_to_knowledge,
    query_flaky_knowledge,
    get_knowledge_stats,
)
from .tools.analysis_tools import (
    parse_console_errors,
    cross_reference_with_past,
    generate_analysis_report,
)
from .tools.recommend_fixes import (
    generate_fix_recommendations,
    generate_recommendations_report,
    save_accepted_recommendations,
)


def _get_llm() -> LLM:
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()

    if provider == "gemini":
        return LLM(
            model=os.getenv("GEMINI_MODEL", "gemini/gemini-3.1-flash-lite"),
            api_key=os.getenv("GEMINI_API_KEY"),
            temperature=0.1,
        )

    if provider == "groq":
        return LLM(
            model=os.getenv("GROQ_MODEL", "groq/llama-3.1-8b-instant"),
            api_key=os.getenv("GROQ_API_KEY"),
            temperature=0.1,
        )

    if provider == "ollama":
        return LLM(
            model="ollama/llama3.1:8b",
            base_url="http://localhost:11434",
            temperature=0.1,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


@CrewBase
class TestFixerCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def code_context_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["code_context_agent"],
            llm=_get_llm(),
            tools=[
                fetch_repo_file_tree,
                filter_relevant_code_files,
                fetch_code_files,
                index_code_to_knowledge,
                query_code_knowledge,
                get_code_knowledge_stats,
            ],
            verbose=True,
        )

    @agent
    def fetcher_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["fetcher_agent"],
            llm=_get_llm(),
            tools=[
                fetch_build_info,
                fetch_console_output,
                fetch_test_report,
                fetch_all_artifacts,
                download_artifacts,
                download_workspace_html,
                download_all_workspace_html,
                build_metadata_json,
                index_build_to_knowledge,
                query_flaky_knowledge,
                get_knowledge_stats,
            ],
            verbose=True,
        )

    @agent
    def analysis_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["analysis_agent"],
            llm=_get_llm(),
            tools=[
                parse_console_errors,
                cross_reference_with_past,
                generate_analysis_report,
                query_flaky_knowledge,
                get_knowledge_stats,
                query_code_knowledge,
                get_code_knowledge_stats,
            ],
            verbose=True,
        )

    @agent
    def recommendation_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["recommendation_agent"],
            llm=_get_llm(),
            tools=[
                generate_fix_recommendations,
                generate_recommendations_report,
                save_accepted_recommendations,
                query_code_knowledge,
                get_code_knowledge_stats,
            ],
            verbose=True,
        )

    @task
    def fetch_code_context(self) -> Task:
        return Task(config=self.tasks_config["fetch_code_context"])

    @task
    def fetch_build_artifacts(self) -> Task:
        return Task(config=self.tasks_config["fetch_build_artifacts"])

    @task
    def analyze_flaky_tests(self) -> Task:
        return Task(config=self.tasks_config["analyze_flaky_tests"])

    @task
    def recommend_fixes(self) -> Task:
        return Task(config=self.tasks_config["recommend_fixes"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )

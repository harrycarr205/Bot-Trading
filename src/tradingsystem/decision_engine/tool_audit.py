"""Audits which optional tools TradingAgents' agents actually request per run.

ARCHITECTURE.md documents Ollama tool-calling reliability as a live concern —
a tool being wired into an analyst's `tools` list (e.g. news_analyst.py) does
not guarantee the model ever calls it. TradingAgentsGraph accepts an optional
`callbacks` list (graph/trading_graph.py) forwarded into every LLM client it
builds; those two LLM instances (deep-think, quick-think) are shared across
every node in the graph, so a callback bound here fires on every single LLM
call in the run — no changes to the pinned tradingagents package needed.
"""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


class ToolCallAuditCallback(BaseCallbackHandler):
    """Collects every tool name any LLM call in the graph requested, in order."""

    def __init__(self) -> None:
        self.requested_tool_names: list[str] = []

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        for generation_list in response.generations:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                for tool_call in getattr(message, "tool_calls", None) or []:
                    self.requested_tool_names.append(tool_call["name"])

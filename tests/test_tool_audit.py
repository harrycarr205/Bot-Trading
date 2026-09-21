from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingsystem.decision_engine.tool_audit import ToolCallAuditCallback


def make_llm_result(tool_calls: list[dict]) -> LLMResult:
    message = AIMessage(content="", tool_calls=tool_calls)
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_records_nothing_when_no_tool_calls_requested():
    callback = ToolCallAuditCallback()
    callback.on_llm_end(make_llm_result([]))
    assert callback.requested_tool_names == []


def test_records_a_single_tool_call_name():
    callback = ToolCallAuditCallback()
    callback.on_llm_end(
        make_llm_result([{"name": "get_macro_indicators", "args": {}, "id": "call_1"}])
    )
    assert callback.requested_tool_names == ["get_macro_indicators"]


def test_records_tool_calls_across_multiple_llm_end_events_in_order():
    callback = ToolCallAuditCallback()
    callback.on_llm_end(make_llm_result([{"name": "get_news", "args": {}, "id": "call_1"}]))
    callback.on_llm_end(
        make_llm_result([{"name": "get_prediction_markets", "args": {}, "id": "call_2"}])
    )
    assert callback.requested_tool_names == ["get_news", "get_prediction_markets"]


def test_plain_text_final_answer_records_nothing():
    # The graph's final "FINAL TRANSACTION PROPOSAL: **BUY**" message has no
    # tool_calls at all — must not raise or record a phantom entry.
    callback = ToolCallAuditCallback()
    message = AIMessage(content="FINAL TRANSACTION PROPOSAL: **BUY**")
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
    assert callback.requested_tool_names == []

"""Token 计数 / 上下文裁剪 / JSON 容错 / 截断器单元测试（对应第2/3课增强）。"""
from litecode.core.context_manager import ContextManager
from litecode.core.json_repair import safe_json_parse
from litecode.core.token_counter import TokenCounter
from litecode.core.truncator import truncate_tool_output
from litecode.core.types import Message, ToolCall


def test_count_text_tokens():
    assert TokenCounter.count_text_tokens("hello world") > 0
    assert TokenCounter.count_text_tokens("中文测试") >= TokenCounter.count_text_tokens("abcd")


def test_count_messages_includes_structure_overhead():
    msgs = [Message(role="user", content="hi")]
    assert TokenCounter.count_messages_tokens(msgs) > TokenCounter.count_text_tokens("hi")


def test_safe_json_parse_fences():
    ok, data, err = safe_json_parse('```json\n{"a": 1}\n```')
    assert ok and data == {"a": 1} and not err


def test_safe_json_parse_failure_reports_error():
    ok, data, err = safe_json_parse('{invalid json')
    assert not ok and data is None and "JSON Parse Failed" in err


def test_truncate_tool_output():
    out = "x" * 10000
    truncated = truncate_tool_output(out, max_characters=1000)
    assert len(truncated) < 1000 + 200
    assert truncated.startswith("x") and truncated.rstrip().endswith("x")
    assert "截断" in truncated
    assert truncate_tool_output("short", 1000) == "short"


def _make_chain() -> list:
    return [
        Message(role="system", content="sys" * 300),
        Message(role="user", content="hello"),
        Message(role="assistant", content=None, tool_calls=[
            ToolCall(id="c1", name="read_file", arguments='{"filePath":"a.ts"}')]),
        Message(role="tool", tool_call_id="c1", content="result" * 50),
        Message(role="user", content="follow up"),
        Message(role="assistant", content="final answer" * 10),
    ]


def test_prune_keeps_system_and_tool_pair_atomic():
    messages = _make_chain()
    # 小预算强制裁剪
    cm = ContextManager(max_allowed_tokens=100)
    pruned = cm.prune_messages(messages)

    assert pruned[0].role == "system"
    # 任何 assistant(tool_calls) 与其 tool 结果要么都在要么都不在
    i = 1
    while i < len(pruned):
        m = pruned[i]
        if m.role == "assistant" and m.tool_calls:
            assert i + 1 < len(pruned)
            assert pruned[i + 1].role == "tool"
            assert pruned[i + 1].tool_call_id == m.tool_calls[0].id
            i += 2
        else:
            i += 1
    # 裁剪后消息数必须少于原始
    assert len(pruned) < len(messages)
    assert TokenCounter.count_messages_tokens(pruned) <= TokenCounter.count_messages_tokens(messages)


def test_prune_noop_within_budget():
    messages = _make_chain()
    cm = ContextManager(max_allowed_tokens=10**6)
    assert cm.prune_messages(messages) == messages
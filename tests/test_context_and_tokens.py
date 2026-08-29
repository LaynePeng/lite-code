"""Token 计数 / 上下文裁剪 / JSON 容错 / 截断器单元测试（对应第2/3课增强）。"""
import os

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
    # 行数未超限 → 不截断
    short = "hello\nworld"
    result = truncate_tool_output(short, max_lines=100, max_bytes=10**6)
    assert not result.truncated
    assert result.content == short

    # 超行数 → 截断，保留头部
    out = "\n".join(f"line_{i}" for i in range(5000))
    result = truncate_tool_output(out, max_lines=50, max_bytes=10**6)
    assert result.truncated
    assert result.content.startswith("line_0")
    assert "lines truncated" in result.content
    assert result.output_path is None  # 无 output_dir 不落盘

    # 超字节 → 截断
    result = truncate_tool_output("x" * 10000, max_lines=10**6, max_bytes=1000)
    assert result.truncated
    assert "bytes truncated" in result.content

    # 短文本不截断
    result = truncate_tool_output("hello")
    assert not result.truncated

    # 带 output_dir 落盘
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = truncate_tool_output("\n".join(f"line_{i}" for i in range(5000)),
                                      max_lines=50, max_bytes=10**6, output_dir=tmp)
        assert result.truncated
        assert result.output_path is not None
        assert os.path.exists(result.output_path)
        assert os.path.getsize(result.output_path) > 0


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
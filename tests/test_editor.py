"""编辑器工具测试（对应第7课：Search-Replace 模糊退避 + Unified Diff 锚点偏移）。"""
from litecode.tools.editor import BlockReplacer, DiffPatcher


def test_block_replacer_exact_match():
    src = "def foo():\n    return 1\n"
    ok, result, _ = BlockReplacer().replace_block(src, "    return 1", "    return 2")
    assert ok and "return 2" in result and "return 1" not in result


def test_block_replacer_fuzzy_with_indent_preservation():
    src = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    # 搜索块缩进错误（少一个空格），模糊匹配应成功并保持原缩进
    ok, result, _ = BlockReplacer().replace_block(src, "   return 1", "   return 99")
    assert ok
    assert "    return 99" in result  # 缩进被还原为 4 空格
    assert "return 1" not in result


def test_block_replacer_no_match_reports_reason():
    ok, _, reason = BlockReplacer().replace_block("aaa", "bbb", "ccc")
    assert not ok and "未找到" in reason


def test_diff_patcher_basic():
    src = "line1\nline2\nline3\nline4\n"
    patch = "@@ -2,2 +2,2 @@\n line2\n-line3\n+line3-modified\n"
    ok, result, _ = DiffPatcher().apply_patch(src, patch)
    assert ok and "line3-modified" in result and "line3\n" not in result


def test_diff_patcher_anchor_offset():
    """LLM 给的行号偏了 3 行，锚点滑动修正仍应应用成功。"""
    src = "".join(f"line{i}\n" for i in range(1, 30))
    patch = "@@ -25,2 +25,2 @@\n line14\n-line15\n+line15-CHANGED\n"
    ok, result, _ = DiffPatcher().apply_patch(src, patch)
    assert ok and "line15-CHANGED" in result


def test_diff_patcher_anchor_not_found():
    src = "a\nb\nc\n"
    patch = "@@ -1,1 +1,1 @@\n-zzzz\n+yyyy\n"
    ok, _, error = DiffPatcher().apply_patch(src, patch)
    assert not ok and "锚点" in error
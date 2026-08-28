"""工具输出截断器（对应课程第2课 truncateToolOutput）。

防止 read_file 读巨型文件 / shell 输出几万行日志导致 Token 爆框。
采用首尾保留策略，并在中间标注省略内容。
"""
from __future__ import annotations


def truncate_tool_output(output: str, max_characters: int = 8000) -> str:
    if len(output) <= max_characters:
        return output

    half = max_characters // 2
    head = output[:half]
    tail = output[-half:]
    omitted = len(output) - max_characters
    return f"{head}\n\n[... ⚠️ 内容已被 Harness 截断 ({omitted} 字符省略) ...]\n\n{tail}"
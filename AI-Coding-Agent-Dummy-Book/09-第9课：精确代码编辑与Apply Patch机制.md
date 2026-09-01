在前面的课程中，我们的 Agent 已经具备了代码感知（rg/Tree-sitter）与安全执行 Shell 的能力。但当 Agent 需要**修改代码**时，我们会遇到 LLM 最常见且头疼的问题：

1. **行号错乱**：LLM 很难精确计算大文件的行号，传递 `insert_at_line: 142` 极其容易插入错位置；
2. **重写全文浪费且不可靠**：让 LLM 直接输出覆盖后的千行文件，既浪费 Token，又常因输出截断破坏语法；
3. **匹配失效**：多行代码中的空格、缩进或微小差别会导致字符串替换匹配失败。

本课我们将手写两种高效且具备**模糊重试容错**的代码编辑工具：**Search-and-Replace 块匹配器** 与 **Unified Diff (Patch) 解析器**。

#### 1. 两种主流编辑机制对比

|编辑机制|核心原理|优点|缺点 / 挑战|
|---|---|---|---|
|**Search-and-Replace Block**|寻找原代码片段 `<SEARCH>`，替换为新代码片段 `<REPLACE>`|LLM 极难写错格式，直观|依赖精确缩进与上下文独特性|
|**Unified Diff (Patch)**|使用 Git 标准 `@@ -L,Count +L,Count @@` 的补丁格式|标准化，支持批量多点改动|LLM 容易算错 `+L,Count` 导致应用失败|

实践证明，**Search-and-Replace Block + 模糊退避重试（Fuzzy Fallback）** 是给 LLM 最稳定、失败率最低的改写方案。

#### 2. 实现 Search-and-Replace 块匹配算法

我们先编写一个容错匹配算法，当精确匹配失败时，自动尝试**去除前后空格/缩进匹配**：

```python
# editor/block_replacer.py
from typing import Tuple

class BlockReplacer:
    """将 source_code 中的 search 块替换为 replace 块，支持模糊匹配。"""

    def replace_block(self, source_code: str, search: str, replace: str) -> Tuple[bool, str, str]:
        # 1. 精确匹配
        if search in source_code:
            return True, source_code.replace(search, replace, 1), ""

        trimmed = search.strip()
        if not trimmed:
            return False, source_code, "搜索块为空。"

        # 2. 模糊匹配（逐行 trim 后匹配）
        source_lines = source_code.split("\n")
        search_lines = [l.strip() for l in search.split("\n")]
        match_start = -1

        for i in range(len(source_lines) - len(search_lines) + 1):
            if all(source_lines[i + j].strip() == search_lines[j]
                   for j in range(len(search_lines))):
                match_start = i
                break

        if match_start == -1:
            return False, source_code, "未找到精确或模糊匹配的 <SEARCH> 块。"

        # 保留原首行缩进
        indent = re.match(r"^\s*", source_lines[match_start]).group()
        indented_replace = "\n".join(
            line if idx == 0 else indent + line.lstrip()
            for idx, line in enumerate(replace.split("\n"))
        )
        new_lines = (source_lines[:match_start]
                     + indented_replace.split("\n")
                     + source_lines[match_start + len(search_lines):])
        return True, "\n".join(new_lines), ""
```

#### 3. 手写 Unified Diff 补丁解析与行号自动偏移修正

当 LLM 算错行号时，通过**上下文锚点行**自动计算偏移量：

```python
# editor/diff_patcher.py
import re
from typing import List, Tuple

class DiffPatcher:
    """应用简化版的 Unified Diff 补丁，带锚点自适应偏移。"""

    def apply_patch(self, source_code: str, patch_str: str) -> Tuple[bool, str, str]:
        lines = source_code.split("\n")
        patch_lines = patch_str.split("\n")
        i = 0

        while i < len(patch_lines):
            m = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
                         patch_lines[i])
            if not m:
                i += 1
                continue

            expected_old_start = int(m.group(1)) - 1  # 转 0-based
            i += 1
            old_lines: List[str] = []
            new_lines: List[str] = []

            while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
                p = patch_lines[i]
                if p.startswith(" "):
                    old_lines.append(p[1:]); new_lines.append(p[1:])
                elif p.startswith("-"):
                    old_lines.append(p[1:])
                elif p.startswith("+"):
                    new_lines.append(p[1:])
                i += 1

            # 自动寻优：锚点行不匹配时前后滑动 15 行
            actual_start = self._find_anchor(lines, old_lines, expected_old_start)
            if actual_start == -1:
                return False, source_code, f"无法在行 {expected_old_start + 1} 附近定位上下文锚点。"
            lines[actual_start:actual_start + len(old_lines)] = new_lines

        return True, "\n".join(lines), ""

    @staticmethod
    def _find_anchor(source_lines: List[str], target_old_lines: List[str], hint: int) -> int:
        if not target_old_lines:
            return hint
        first = target_old_lines[0].strip()
        for offset in range(16):
            for idx in (hint + offset, hint - offset):
                if 0 <= idx < len(source_lines) and source_lines[idx].strip() == first:
                    return idx
        return -1
```

#### 4. 封装应用 Patch 工具与自愈重试

当匹配失败时，最关键的是**将失败原因返回给 LLM**，触发 Agent 的自主修正循环：

```python
# tools/editor_tools.py
import os, asyncio

editor_tools = [
    ToolDefinition(
        name="apply_search_replace",
        description="通过 SEARCH/REPLACE 块精确更新文件代码（缩进需完全一致；模糊匹配失败会返回原因）",
        parameters={"type": "object", "properties": {
            "filePath": {"type": "string", "description": "文件相对路径"},
            "searchBlock": {"type": "string", "description": "被替换的完整原始代码片段（含原缩进）"},
            "replaceBlock": {"type": "string", "description": "写入的新代码片段"},
        }, "required": ["filePath", "searchBlock", "replaceBlock"]},
    ),
    ToolDefinition(
        name="apply_unified_diff",
        description="应用标准 Unified Diff 补丁",
        parameters={"type": "object", "properties": {
            "filePath": {"type": "string", "description": "文件相对路径"},
            "diff": {"type": "string", "description": "Unified Diff 补丁文本"},
        }, "required": ["filePath", "diff"]},
    ),
]

replacer = BlockReplacer()
patcher = DiffPatcher()

async def execute_editor_tool(name: str, args: dict, cwd: str) -> str:
    full_path = os.path.join(cwd, args["filePath"])
    if not os.path.exists(full_path):
        return f"[Edit Error]: 文件不存在: {args['filePath']}"
    with open(full_path, "r", encoding="utf-8") as f:
        source = f.read()

    if name == "apply_search_replace":
        ok, result, reason = replacer.replace_block(
            source, args.get("searchBlock", ""), args.get("replaceBlock", ""))
        if not ok:
            return f"[Patch Failed]: {reason}\n建议：重新 read_file 获取精确内容后重试。"
    elif name == "apply_unified_diff":
        ok, result, reason = patcher.apply_patch(source, args.get("diff", ""))
        if not ok:
            return f"[Patch Failed]: {reason}\n建议：重新 read_file 获取精确上下文后重试。"
    else:
        raise ValueError(f"Unknown Editor Tool: {name}")

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(result)
    return _diff_summary(args["filePath"], source, result)

def _diff_summary(rel_path: str, source: str, result: str) -> str:
    """返回带文件路径与增删行数的结果（+N -M），并附 Unified Diff 供 Agent 自检。"""
    import difflib
    diff = list(difflib.unified_diff(
        source.splitlines(), result.splitlines(),
        fromfile=rel_path, tofile=rel_path, lineterm="",
    ))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    head = f"[Patch Success]: 已更新 {rel_path} (+{added} -{removed})"
    if not diff:
        return head
    body = "\n".join(diff)
    if len(body) > 4000:
        body = body[:4000] + "\n...(diff 过长已截断)"
    return f"{head}\n\n{body}"
```

**为什么成功回执要附上 Diff？** 编辑工具的成功回执不只是"告知写好了"，而是携带了**完整的 Unified Diff 正文与 `(+N -M)` 增删统计**（`_diff_summary`）：

1. **Agent 自检**：模型拿到 diff 后可以"复核"自己刚做的修改是否符合预期，发现多余改动时主动补救，形成闭环；
2. **前端渲染**：`(+N -M)` 徽标与 diff 高亮，会在第 20 课的 Web UI 中渲染成 opencode 风格的"文件修改卡片"——`[Patch Success]: 已更新 xxx (+N -M)` 就是前后端约定的契约格式；
3. **Token 可控**：diff 正文超过 4000 字符自动截断，避免长文件回执把上下文撑爆。

### 本课小结

在本课中，我们补齐了 Agent 在大型代码库中精确改写代码的核心拼图：

1. 深入理解了 **Search-and-Replace Block** 与 **Unified Diff** 的各自适用场景；
2. 实现了包含 **缩进保持** 与 **模糊行匹配** 的块替换器；
3. 实现了带 **锚点自适应偏移** 的 Diff 应用器；
4. 建立了 Patch 失败时的 **Agent 上下文自愈反馈链条**，成功时返回 `(+N -M)` + Unified Diff 供自检与前端渲染。

下一次我们将开启 **第10课：项目指令文件与Skills系统** —— 学习如何让 Harness 读取 `AGENTS.md` / `CLAUDE.md` 项目指令，并通过"索引常驻 + 按需加载"的 Skills 机制接入专项工作流！
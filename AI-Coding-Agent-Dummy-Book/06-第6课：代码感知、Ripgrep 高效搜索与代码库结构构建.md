在前五课中，我们实现了具备自我修复与上下文管理的 Agent 主循环。然而，软件开发场景中真正的难点在于：**一个大型代码库通常包含几十万甚至上百万行代码**，而大模型的上下文窗口（Context Window）不仅容量有限，且全量填入会导致推理成本飙升与"在针堆中找针"（Haystack Effect）的注意力衰减。

本课我们将编写 Code Agent 的核心眼睛 —— **代码检索与结构感知系统**：
1. 实现 **Ripgrep 集成**，提供毫秒级全文正则搜索；
2. 实现 **文件树生成与 Gitignore 过滤**（防止 `.git` 或 `node_modules` 爆框）；
3. 编写符合 LLM Tool Protocol 的代码感知工具集。

#### 1. Ripgrep 高效集成与安全封装

在底层命令行工具中，`ripgrep` (`rg`) 是目前效率最高的代码搜索工具。比普通 Python 实现快 10-100 倍。

编写 `rg` 封装时，必须进行**参数转义**与**搜索结果截断**，防止注入攻击与匹配过多导致的输出爆炸。

```python
# tools/ripgrep_runner.py
import asyncio
import shutil
from typing import Optional

class RipgrepRunner:
    """使用 rg 进行高性能正则表达式搜索。"""

    @staticmethod
    async def search(
        query: str,
        cwd: str,
        include_pattern: Optional[str] = None,
        max_results: int = 50,
    ) -> str:
        rg = shutil.which("rg")
        if not rg:
            return "[Error]: 未检测到 ripgrep (rg)"

        args = [
            rg, "--line-number", "--column", "--color=never", "--smart-case",
            "--max-count", str(max_results), "--no-messages", "--hidden",
            "--glob", "!.git", "--glob", "!node_modules", "--glob", "!.venv",
            "--glob", "!dist", "--glob", "!build",
        ]
        if include_pattern:
            args += ["--glob", include_pattern]
        args.append(query)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            text = stdout.decode("utf-8", errors="replace").strip()
            if not text:
                return f'未找到匹配: "{query}"'
            lines = text.split("\n")
            note = f"\n[... 仅显示前 {max_results} 条匹配]" if len(lines) >= max_results else ""
            return f'搜索 "{query}" 找到 {len(lines)} 处匹配:{note}\n{text}'
        except asyncio.TimeoutError:
            return "[Error]: 搜索超时（30s）。"
        except Exception as e:
            return f"[Error]: 搜索执行失败: {e}"
```

#### 2. 代码库文件树与 Gitignore 过滤

当 Agent 进入一个陌生项目时，它首先需要**查看目录树结构**。

我们使用 `pathspec` 库解析 `.gitignore` 文件，实现过滤掉 build 产物、依赖包和二进制文件的本地文件树列表器：

```python
# tools/file_tree_walker.py
import os
import pathspec
from typing import List

class FileTreeWalker:
    """递归生成项目结构树，自动遵循 .gitignore。"""

    @staticmethod
    def load_gitignore(root_dir: str) -> pathspec.PathSpec:
        patterns: List[str] = [
            ".git", "node_modules", "dist", "build", "coverage",
            ".venv", "venv", "__pycache__", "*.pyc", ".DS_Store",
        ]
        gitignore_path = os.path.join(root_dir, ".gitignore")
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                patterns.extend(
                    l for l in f.read().splitlines()
                    if l.strip() and not l.startswith("#")
                )
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    @classmethod
    def get_project_tree(cls, dir: str, max_depth: int = 3) -> str:
        spec = cls.load_gitignore(dir)
        lines: List[str] = []

        def walk(current: str, depth: int, prefix: str):
            if depth > max_depth:
                return
            try:
                entries = sorted(os.listdir(current),
                                 key=lambda e: (not os.path.isdir(os.path.join(current, e)), e.lower()))
            except OSError:
                return
            filtered = []
            for name in entries:
                if name.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(current, name), dir)
                if spec.match_file(rel):
                    continue
                filtered.append(name)

            for idx, name in enumerate(filtered):
                is_last = idx == len(filtered) - 1
                connector = "└── " if is_last else "├── "
                full = os.path.join(current, name)
                lines.append(f"{prefix}{connector}{name}{'/' if os.path.isdir(full) else ''}")
                if os.path.isdir(full):
                    walk(full, depth + 1,
                         prefix + ("    " if is_last else "│   "))

        lines.append(os.path.basename(os.path.abspath(dir)) + "/")
        walk(dir, 1, "")
        return "\n".join(lines)
```

#### 3. 组装代码感知 Tools (Code Perception Tools)

现在我们将上述功能封装成大模型可调用的标准 Tool JSON Schema，并实现执行器：

```python
# tools/codebase_tools.py
import os, asyncio
from typing import Any, Dict

# 1. 工具定义
codebase_tools = [
    ToolDefinition(
        name="file_tree",
        description="查看当前项目的目录树结构（自动过滤 gitignore 忽略文件）",
        parameters={"type": "object", "properties": {
            "maxDepth": {"type": "number", "description": "遍历深度，默认为 3"},
        }},
    ),
    ToolDefinition(
        name="search_code",
        description="使用 Ripgrep 高速全局正则搜索代码文本或符号名称",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "正则表达式或搜索关键字"},
            "includePattern": {"type": "string", "description": "可选文件匹配格式，例如 '*.ts'"},
        }, "required": ["query"]},
    ),
    ToolDefinition(
        name="read_file",
        description="读取指定路径的文件内容，支持按行读取（带行号标记）",
        parameters={"type": "object", "properties": {
            "filePath": {"type": "string", "description": "文件的相对路径"},
            "startLine": {"type": "number", "description": "起始行号（可选）"},
            "endLine": {"type": "number", "description": "结束行号（可选）"},
        }, "required": ["filePath"]},
    ),
]

# 2. Tool 分发执行器
async def execute_codebase_tool(name: str, args: dict, cwd: str) -> str:
    if name == "file_tree":
        return FileTreeWalker.get_project_tree(cwd, args.get("maxDepth") or 3)

    if name == "search_code":
        return await RipgrepRunner.search(
            query=args["query"],
            include_pattern=args.get("includePattern"),
            cwd=cwd,
        )

    if name == "read_file":
        target_path = os.path.abspath(os.path.join(cwd, args["filePath"]))
        if not target_path.startswith(os.path.abspath(cwd)):
            raise PermissionError("路径穿越被拒绝")
        if not os.path.exists(target_path):
            return f'Error: File not found at "{args["filePath"]}"'

        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")

        start = max(0, int(args.get("startLine") or 1) - 1)
        end = min(len(lines), int(args.get("endLine") or len(lines)))
        formatted = "\n".join(
            f"{start + i + 1} | {l}" for i, l in enumerate(lines[start:end])
        )
        return f'File: {args["filePath"]} (行 {start+1}-{end} / 共 {len(lines)} 行)\n{formatted}'

    raise ValueError(f"Unknown tool: {name}")
```

#### 4. 运行效果展示

基于本课封装的代码感知工具，当用户向 Agent 提出复杂代码定位任务时：

> **用户**: "帮我定位这个项目中处理 API Token 解析的代码在哪个文件，并输出那个函数的所在行号。"

Agent 的感知链路如下：
1. **Turn 1**: 自动发起 `file_tree({})` 了解整个项目结构；
2. **Turn 2**: 根据树结构发现 `src/auth`，调用 `search_code({ query: "verifyToken|decodeToken", includePattern: "*.py" })`；
3. **Turn 3**: Ripgrep 返回 `src/auth/jwt.py:42:10` 结果；
4. **Turn 4**: 发起 `read_file({ filePath: "src/auth/jwt.py", startLine: 35, endLine: 60 })` 读取精确上下文并向用户给出终态解答。

### 本课小结

在这节课中，我们为 Agent 装上了高效读取大型代码库的眼睛：
- 实现了 **Ripgrep 底层加速与正则搜索**；
- 实现了 **自动 Gitignore 过滤的文件树遍历器**；
- 增加了 **带行号标记与路径穿越防护** 的文件读取机制。

下一次我们将进入 **第7课：基于 Tree-sitter 的抽象语法树（AST）分析与代码依赖图构建** —— 学习如何超越简单的文本搜索，从结构语义层面准确提取类、函数签名与调用链！
在第六课中，我们通过 Ripgrep 和文件读取工具让 Agent 具备了"文本级"的代码查找能力。但在处理大型工程（如包含复杂的类继承、多层函数调用链）时，仅仅靠正则搜索文本仍然存在严重的缺陷：

1. **语义模糊**：搜索 `parse` 可能会匹配到几十个不相干文件里的同名方法；
2. **上下文过载**：大模型不需要看完整几千行的文件，只需要看与目标修改直接相关的**函数签名、接口定义与依赖结构**。

本课引入 **Tree-sitter** 语法分析引擎，为 Harness 增加 AST 级的代码语义感知能力，实现**精确的上下文压缩**与**依赖图谱提取**。

#### 1. 为什么选择 Tree-sitter？

在 Agent Harness 体系中，Tree-sitter 相比传统的 AST 解析器（如 Python 的 `ast`、`libcst`）具有三个决定性优势：
- **增量解析与容错（Error Tolerance）**：即使代码当前有语法错误（例如用户写到一半的项目），Tree-sitter 依然能够解析出绝大部分合法的语法节点；
- **极速性能（C/Rust 底层）**：解析几万行代码仅需数毫秒；
- **统一的 S-expression 查询语法（Tree-sitter Query）**：用同一套 DSL 即可精准提取 TypeScript、Python、Go、Rust 等不同语言的函数与类结构。

#### 2. 安装与解析器封装

在项目中，Tree-sitter 用于 TypeScript/JavaScript、Java 和 Go；Python 使用标准库 `ast`，避免再引入一个 Python grammar wheel：

```Bash
pip install tree-sitter tree-sitter-typescript tree-sitter-java tree-sitter-go
```

> 注意：tree-sitter 0.26+ 的新 API 需要先 `tree_sitter.Language()` 包装，再传给 `Parser`。

下面我们编写 `ASTAnalyzer` 模块，用于解析代码文件并提取代码的大纲（Outline / Symbol Graph）：

```python
# ast/ast_analyzer.py
from dataclasses import dataclass
from typing import List, Optional

import tree_sitter
import tree_sitter_typescript

@dataclass
class SymbolOutline:
    name: str
    kind: str          # "function" | "class" | "interface" | "method"
    start_line: int
    end_line: int
    signature: str

class ASTAnalyzer:
    """解析 TS/JS 文件提取符号大纲。"""

    def __init__(self):
        self.lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())

    def parse(self, code: str) -> Optional[tree_sitter.Tree]:
        parser = tree_sitter.Parser(self.lang)
        return parser.parse(code.encode("utf-8"))

    def extract_outline(self, code: str) -> List[SymbolOutline]:
        tree = self.parse(code)
        if tree is None:
            return []
        symbols: List[SymbolOutline] = []
        code_bytes = code.encode("utf-8")

        def visit(node: tree_sitter.Node):
            node_type = node.type
            kind = None
            if node_type in ("function_declaration", "generator_function_declaration"):
                kind = "function"
            elif node_type == "class_declaration":
                kind = "class"
            elif node_type == "interface_declaration":
                kind = "interface"
            elif node_type == "method_definition":
                kind = "method"

            if kind is not None:
                name_node = node.child_by_field_name("name")
                name = (name_node.text.decode("utf-8", "replace")
                        if name_node else "<anonymous>")
                first_line_end = code_bytes.find(b"\n", node.start_byte)
                first_line = code_bytes[node.start_byte:first_line_end if first_line_end != -1 else node.end_byte]
                symbols.append(SymbolOutline(
                    name=name, kind=kind,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=first_line.decode("utf-8", "replace").strip(),
                ))

            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return symbols
```

**与 TS 版的对应关系**：
- `node.childForFieldName("name")` → `node.child_by_field_name("name")`
- `node.startPosition.row` → `node.start_point[0]`
- `node.startIndex` → `node.start_byte`（字节偏移，注意要按 bytes 切片再解码，避免中文截断）

#### 3. 实现精确上下文压缩器（Context Shrinker）

当 Agent 需要修改某个函数（比如第 120 行的 `handlePayment`）时，给 LLM 发送包含 3000 行代码的完整文件是非常浪费且容易产生干扰的。

利用 AST，我们编写一个 **Skeleton View 提取工具**：它会保留文件的 Import 语句、全局类型定义，并将目标函数之外的所有函数体替换为占位注释。

```python
# ast/context_shrinker.py
import tree_sitter
import tree_sitter_typescript

class ContextShrinker:
    """骨架抽取：只完整保留 target_symbol 对应的函数体，其他函数体均裁剪掉。"""

    _FUNCTION_NODES = {"function_declaration", "method_definition",
                       "arrow_function", "function_expression"}

    def __init__(self):
        self.lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())

    def generate_skeleton(self, code: str, target_symbol: str) -> str:
        parser = tree_sitter.Parser(self.lang)
        tree = parser.parse(code.encode("utf-8"))
        code_bytes = code.encode("utf-8")
        replacements: List[List[int]] = []   # [start, end]

        def walk(node: tree_sitter.Node):
            if node.type in self._FUNCTION_NODES:
                name_node = node.child_by_field_name("name")
                name = (name_node.text.decode("utf-8", "replace")
                        if name_node else None)
                body = node.child_by_field_name("body")
                if name and body and name != target_symbol:
                    replacements.append([body.start_byte, body.end_byte])
            for child in node.children:
                walk(child)

        walk(tree.root_node)

        # 从后往前替换，避免 offset 错位
        result = code
        for start, end in sorted(replacements, reverse=True):
            result = (result[:start]
                      + "{ /* ... body omitted for context saving ... */ }"
                      + result[end:])
        return result
```

#### 4. 封装 AST 语义工具集成至 Harness

现在，我们将 Tree-sitter 提供的 AST 语义识别能力暴露给 Agent Tool Protocol：

```python
# tools/ast_tools.py
import os, asyncio

ast_tools = [
    ToolDefinition(
        name="get_file_outline",
        description="通过 AST 解析获取文件的结构大纲（函数、类、接口列表与所在行号）",
        parameters={"type": "object", "properties": {
            "filePath": {"type": "string", "description": "文件相对路径"},
        }, "required": ["filePath"]},
    ),
    ToolDefinition(
        name="read_focused_symbol",
        description="针对大型文件，仅提取指定目标函数/方法的骨架上下文，隐藏其余不相干代码",
        parameters={"type": "object", "properties": {
            "filePath": {"type": "string", "description": "文件相对路径"},
            "symbolName": {"type": "string", "description": "需要聚焦查看的函数或类方法名"},
        }, "required": ["filePath", "symbolName"]},
    ),
]

analyzer = ASTAnalyzer()
shrinker = ContextShrinker()

async def execute_ast_tool(name: str, args: dict, cwd: str) -> str:
    full_path = os.path.join(cwd, args["filePath"])
    if not os.path.exists(full_path):
        return f"Error: File missing at {args['filePath']}"
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        raw_code = f.read()

    if name == "get_file_outline":
        symbols = await asyncio.to_thread(analyzer.extract_outline, raw_code)
        if not symbols:
            return f"No top-level function/class symbols found in {args['filePath']}"
        summary = "\n".join(
            f"[{s.kind.upper()}] {s.name} (Lines {s.start_line}-{s.end_line}) -> {s.signature}"
            for s in symbols)
        return f"Symbols in {args['filePath']}:\n{summary}"

    if name == "read_focused_symbol":
        skeleton = await asyncio.to_thread(shrinker.generate_skeleton, raw_code, args["symbolName"])
        return f'Focused view for symbol "{args["symbolName"]}" in {args["filePath"]}:\n\n{skeleton}'

    raise ValueError(f"Unknown AST Tool: {name}")
```

### 本课小结

在本课中，我们实现了从"字符搜索"到"语法结构感知"的跃迁：

1. 理解并封装了 **Tree-sitter C 语法树解析引擎**（Python 绑定）；
2. 实现了毫秒级的 **文件符号大纲提取（Symbol Outline）**；
3. 编写了 **AST 骨架压缩机制（Skeleton Extraction）**，在保持代码语境完整的前提下，节约了 60%~80% 的上下文 Token 消耗。

至此，**模块二：代码感知** 的前半部分（检索与 AST 分析）已完结。

下一课开启 **第8课：沙箱隔离技术（Execution Sandbox）** —— 学习基于进程隔离的代码沙箱环境与路径安全隔离。

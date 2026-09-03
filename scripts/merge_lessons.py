# -*- coding: utf-8 -*-
"""合并教程文件：原6+7→新6、原8+9→新7、原12+13→新10，调整过渡段后写入新文件，并删除旧文件。"""
import os

BOOK = "AI-Coding-Agent-Dummy-Book"

def read(name):
    with open(os.path.join(BOOK, name), encoding="utf-8") as f:
        return f.read()

def write(name, content):
    with open(os.path.join(BOOK, name), "w", encoding="utf-8") as f:
        f.write(content)

# ---------- 新6 = 原6 + 原7 ----------
f6 = read("06-第6课：代码感知、Ripgrep 高效搜索与代码库结构构建.md")
f7 = read("07-第7课：基于Tree-sitter的抽象语法树（AST）分析与代码依赖图构建.md")

# 原6 结尾过渡段 → 本节预告
old_tail_6 = (
    '下一课进入 **第7课：基于 Tree-sitter 的抽象语法树（AST）分析与代码依赖图构建**'
    ' —— 学习如何超越简单的文本搜索，从结构语义层面准确提取类、函数签名与调用链。'
)
new_tail_6 = (
    '> **本节预告**：以上完成了\u201c文本检索\u201d层；接下来将从\u201c文本检索\u201d跃迁到'
    '\u201c结构语义\u201d\u2014\u2014利用 Tree-sitter 语法分析引擎，从 AST 层面准确提取类、函数签名与调用链。'
)
f6_new = f6.replace(old_tail_6, new_tail_6)

# 原7 开头 → 上一节
f7_new = f7.replace(
    '在第六课中，我们通过 Ripgrep 和文件读取工具让 Agent 具备了\u201c文本级\u201d的代码查找能力。',
    '在上一节中，我们通过 Ripgrep 和文件读取工具让 Agent 具备了\u201c文本级\u201d的代码查找能力。'
)
# 原7 结尾过渡段
old_tail_7 = (
    '至此，**模块二：代码感知** 的前半部分（检索与 AST 分析）已完结。\n\n'
    '下一课开启 **第8课：沙箱隔离技术（Execution Sandbox）**'
    ' —— 学习基于进程隔离的代码沙箱环境与路径安全隔离。'
)
new_tail_7 = (
    '下一课进入 **第7课：安全代码操作：执行隔离与原子编辑**'
    ' —— 学习基于进程隔离的代码沙箱环境与路径安全隔离，以及精确编辑与原子补丁机制。'
)
f7_new = f7_new.replace(old_tail_7, new_tail_7)

new6 = f6_new.rstrip() + '\n\n---\n\n## 第二部分：基于 Tree-sitter 的 AST 语法分析\n\n' + f7_new.lstrip()
write('06-第6课：代码理解：检索机制与结构分析.md', new6)

# ---------- 新7 = 原8 + 原9 ----------
f8 = read('08-第8课：沙箱隔离技术（Execution Sandbox）.md')
f9 = read('09-第9课：精确代码编辑与Apply Patch机制.md')

old_tail_8 = (
    '下一课进入 **第9课：精确代码编辑与 Apply Patch 机制**'
    ' —— 学习解决 LLM 频繁写错代码行号的问题，手写高效且具备重试能力的代码编辑与 Patch 校验工具。'
)
new_tail_8 = (
    '> **本节预告**：安全执行底座就绪后，接下来解决 Agent \u201c改代码\u201d的问题'
    '\u2014\u2014当行号错乱、全文重写不可靠时，如何用 Search-Replace 与 Unified Diff 实现精确、可自愈的原子编辑。'
)
f8_new = f8.replace(old_tail_8, new_tail_8)

f9_new = f9.replace(
    '在前面的课程中，我们的 Agent 已经具备了代码感知（rg/Tree-sitter）与安全执行 Shell 的能力。'
    '但当 Agent 需要**修改代码**时，我们会遇到 LLM 最常见且头疼的问题：',
    '在上一节中，我们为 Agent 构建了安全执行底座。但当 Agent 需要**修改代码**时，'
    '我们会遇到 LLM 最常见且头疼的问题：'
)
old_tail_9 = (
    '下一次我们将开启 **第10课：项目指令文件与Skills系统**'
    ' —— 学习如何让 Harness 读取 `AGENTS.md` / `CLAUDE.md` 项目指令，'
    '并通过\u201c索引常驻 + 按需加载\u201d的 Skills 机制接入专项工作流！'
)
new_tail_9 = (
    '下一课进入 **第8课：Skills 系统：注入机制与权限治理**'
    ' —— 学习如何让 Harness 读取 `AGENTS.md` / `CLAUDE.md` 项目指令，'
    '并通过\u201c索引常驻 + 按需加载\u201d的 Skills 机制接入专项工作流！'
)
f9_new = f9_new.replace(old_tail_9, new_tail_9)

new7 = f8_new.rstrip() + '\n\n---\n\n## 第二部分：精确代码编辑与 Apply Patch 机制\n\n' + f9_new.lstrip()
write('07-第7课：安全代码操作：执行隔离与原子编辑.md', new7)

# ---------- 新10 = 原12 + 原13 ----------
f12 = read('12-第12课：Cordis插件内核设计（Spatiotemporal Composability）.md')
f13 = read('13-第13课：依赖注入与插件生命周期管理.md')

old_tail_12 = (
    '下一次我们将进入 **第13课：依赖注入与插件生命周期管理**'
    ' —— 深入探讨 Interceptor 拦截器管道（中间件模式），'
    '学习如何通过插件在 Tool 调用前后实现无感修改 Prompt 和安全审查拦截！'
)
new_tail_12 = (
    '> **本节预告**：内核骨架就绪后，接下来深入 Interceptor 拦截器管道（洋葱模型中间件）'
    '与生命周期钩子，让插件在 Tool 调用前后实现无感修改 Prompt 和安全审查拦截。'
)
f12_new = f12.replace(old_tail_12, new_tail_12)

f13_new = f13.replace(
    '在第十二课中，我们实现了基石级的 **Cordis 插件内核** 与 **Trajectory 轨迹记录服务**。',
    '在上一节中，我们实现了基石级的 **Cordis 插件内核** 与 **Trajectory 轨迹记录服务**。'
)
old_tail_13 = (
    '下一次我们将进入 **第14课：多 Agent 协作与子 Agent 调度 (Sub-agent Orchestration)**'
    ' —— 学习如何通过 Harness 的嵌套实例与并行任务分发，'
    '让主 Agent 组装\u201c专门的子 Agent\u201d独立完成特定的复杂任务！'
)
new_tail_13 = (
    '下一课进入 **第11课：多 Agent 协作：编排、通信与持久化**'
    ' —— 学习如何通过 Harness 的嵌套实例与并行任务分发，'
    '让主 Agent 组装\u201c专门的子 Agent\u201d独立完成特定的复杂任务！'
)
f13_new = f13_new.replace(old_tail_13, new_tail_13)

new10 = f12_new.rstrip() + '\n\n---\n\n## 第二部分：依赖注入与插件生命周期管理\n\n' + f13_new.lstrip()
write('10-第10课：插件架构：内核设计与依赖注入.md', new10)

print('合并完成：新6、新7、新10')
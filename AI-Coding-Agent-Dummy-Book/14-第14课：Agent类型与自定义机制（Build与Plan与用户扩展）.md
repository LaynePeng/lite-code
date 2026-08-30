在前面的课程中，我们构建的 Harness 始终是"单 Agent 循环"：一个 System Prompt、一套工具、一个循环。但现实中的 Code Agent 需要**面向不同场景、不同角色**工作。OpenCode 的做法非常值得参考：

- **Build**（构建）：默认 Agent，拥有全部工具，负责实际开发；
- **Plan**（规划）：只读 Agent，禁止改文件与执行命令，只做分析与出方案；
- **Subagent**（子 Agent）：主 Agent 通过 Task 工具按需派生，上下文隔离。

本课我们将把 Harness 从"单 Agent"升级为"**多 Agent + 可自定义**"架构，回答两个问题：
1. 怎么内置 **Build/Plan 两种默认 Agent**？
2. 怎么让**用户自己添加 Agent**（改 System Prompt、裁工具、配权限）？

#### 1. 多 Agent 架构设计

```
                    +----------------------------------+
                    |  AgentRegistry（Agent 注册表）     |
                    |  - build   (primary, 全工具)      |
                    |  - plan    (primary, 只读)        |
                    |  - reviewer(subagent, 自定义)     |
                    +----------------------------------+
                                   |
          Tab 切换主 Agent          | @ 引用子 Agent
                   v                v
        +---------------+   +-----------------+
        |  Build Agent   |   |  Plan Agent     |   用户自定义...
        |  所有工具       |   |  只读/禁止编辑   |
        +---------------+   +-----------------+
```

**两种 Agent 类型**：
- `primary`：主 Agent，用户用 Tab 切换，直接与之对话；
- `subagent`：子 Agent，主 Agent 用 `spawn_sub_agent` 工具按需派生（上下文隔离）。

#### 2. AgentProfile：Agent 的完整描述

我们把一个 Agent 描述为一个数据类 `AgentProfile`，字段对齐 OpenCode 的 agent 配置：

```python
# core/agent_profile.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class AgentProfile:
    id: str                    # agent 唯一标识："build" / "plan" / "reviewer"
    mode: str = "primary"      # "primary" 或 "subagent"
    description: str = ""      # 作用描述（供 UI 与 LLM 决策）
    system_prompt: Optional[str] = None   # 自定义 System Prompt
    model: Optional[str] = None           # 覆盖模型
    temperature: Optional[float] = None   # 覆盖 temperature
    tools: Optional[List[str]] = None     # 允许的工具（None=全部）
    permissions: Dict[str, str] = field(default_factory=dict)  # 工具权限覆盖
    hidden: bool = False
```

#### 3. 内置默认 Agent：Build 与 Plan

**Build**（默认，全工具）：

```python
def default_build_agent() -> AgentProfile:
    return AgentProfile(
        id="build", mode="primary",
        description="默认开发 Agent：拥有全部工具，负责实际的编码与执行。",
        permissions={},  # 无限制
    )
```

**Plan**（只读，禁止编辑与执行）：

```python
PLAN_PROMPT = """你是一个「规划型」AI 软件工程师（Plan Agent）。
当前模式只做分析与规划，**禁止修改任何文件、禁止执行命令**。
你的职责：
1. 理解用户需求，先探查代码库结构与相关文件内容；
2. 输出一份清晰、可执行、分步骤的实现计划；
3. 除非用户明确要求，否则不要动手改代码。"""

def default_plan_agent() -> AgentProfile:
    return AgentProfile(
        id="plan", mode="primary",
        description="规划 Agent：只读分析与方案设计，禁止修改文件与执行命令。",
        system_prompt=PLAN_PROMPT,
        # 只读工具白名单
        tools=["read_file", "list_dir", "file_tree", "search_code",
               "get_file_outline", "read_focused_symbol",
               "git_status", "git_diff", "git_log", "git_branch", "review_code"],
        # 权限兜底：即使授予写工具也强制 deny
        permissions={"write_file": "deny", "apply_search_replace": "deny",
                     "apply_unified_diff": "deny", "execute_command": "deny"},
    )
```

**核心思想**：Plan 不只靠 `tools` 白名单限制，还用 `permissions` 做了**双保险**——即使工具被注入，执行时也会被权限系统拦截。这与 OpenCode 的 Plan 一致：所有文件编辑和 bash 命令默认 `ask`/`deny`。

#### 4. AgentRegistry：注册表 + 自定义加载

```python
# core/agent_profile.py
class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, AgentProfile] = {}
        self._register_defaults()   # build + plan

    def get(self, agent_id: str) -> AgentProfile: ...
    def list_primary(self) -> List[str]: ...
    def list_subagents(self) -> List[str]: ...

    def load_dir(self, agents_dir: str) -> None:
        """扫描 .harness/agents/*.json 加载用户自定义 agent。"""
        for fname in os.listdir(agents_dir):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(agents_dir, fname)) as f:
                data = json.load(f)
            self.register(AgentProfile.from_dict(data))
```

#### 5. 用户如何自定义 Agent？

**方式一：配置文件**（`.harness/config.json` 的 `agents` 段）：

```json
{
  "agents": {
    "reviewer": {
      "mode": "subagent",
      "description": "代码评审：只读检查，禁止修改",
      "tools": ["read_file", "search_code", "git_diff", "review_code"],
      "permissions": {"write_file": "deny", "execute_command": "deny"},
      "system_prompt": "你是一名资深代码评审员，关注安全、性能与可维护性。"
    }
  }
}
```

**方式二：agents 目录**（`.harness/agents/reviewer.json`），文件名即 Agent ID：

```json
{
  "mode": "subagent",
  "description": "代码评审 Agent",
  "tools": ["read_file", "search_code", "review_code"],
  "permissions": {"write_file": "deny"}
}
```

**方式三（对齐 OpenCode）：Markdown 文件**（`.harness/agents/reviewer.md`）

完全参考 OpenCode 的 markdown agent 设计——YAML frontmatter 放配置，正文放 system prompt，文件名即 agent id：

```markdown
---
description: 代码评审 Agent
mode: subagent
tools:
  - read_file
  - search_code
  - review_code
permissions:
  write_file: deny
---
你是一名资深代码评审员，只读检查代码质量。
```

`AgentRegistry.load_dir` 自动扫描 `.json` 和 `.md` 两种格式。`.md` 的 frontmatter 解析器是轻量自实现的（不依赖 PyYAML），支持标量、列表、嵌套 map：

```python
def parse_frontmatter(text: str):
    """解析 YAML frontmatter（--- 包裹的头部），返回 (dict, 正文)。"""
    data, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if m:
        data = _parse_yaml_body(m.group(1))
        body = text[m.end():]
    return data, body
```

AgentApp 在启动时会自动扫描 `agents` 目录并注册这两种格式。

#### 6. 按 Agent 裁剪工具集并运行

`AgentApp` 提供 agent 感知的注册表构建与循环创建：

```python
# app.py
def create_agent_registry(self, agent_id: str) -> ToolRegistry:
    profile = self.get_agent(agent_id)
    return self.build_registry(
        allowed=profile.tools,          # 白名单裁剪
        exclude=["spawn_sub_agent"] if agent_id == "plan" else None,
        permissions=profile.permissions,  # 权限 deny 兜底
    )

def create_loop(self, kernel, registry, agent_id=None) -> AgentLoop:
    profile = self.get_agent(agent_id)
    # 支持 per-agent 的模型/温度覆盖
    overrides = {}
    if profile.model: overrides["model"] = profile.model
    if profile.temperature is not None: overrides["temperature"] = profile.temperature
    adapter = self.llm_registry.build_adapter(overrides=overrides) if overrides else self.adapter
    ...
```

**验证**：

```python
app = AgentApp(workspace="/path")
print(app.get_agent("plan").tools)      # 只读工具
print(len(app.create_agent_registry("build").get_tools()))  # 19 个工具（含 webfetch / webfetch_batch）
```

#### 7. 与子 Agent 编排打通

第 13 课的 `SubAgentRunner` 现在也接入 AgentRegistry：用户自定义的 `subagent` 也能被 `spawn_sub_agent` 直接派生。

```python
# orchestration/sub_agent.py
def _resolve_role(self, role: str):
    try:
        profile = self.app.agent_registry.get(role)
        if profile.mode in ("subagent", "all"):
            return profile
    except KeyError:
        pass
    return None

async def run_task(self, task_description, role="general", ...):
    profile = self._resolve_role(role)
    if profile is not None:
        allowed = profile.tools
        permissions = profile.permissions
        base_prompt = profile.system_prompt or ROLE_PROMPTS["general"]
    else:
        allowed = ROLE_TOOLS.get(role)
        ...
```

这样，用户只需要写一个 JSON 文件，就能为 Harness 添加一个全新的 Agent 角色。

#### 本课小结

1. 理解了 **Primary Agent（Build/Plan）与 Subagent** 的架构区别；
2. 用 `AgentProfile` + `AgentRegistry` 实现了 **Build/Plan 两种默认 Agent**；
3. 打通了 **用户自定义 Agent** 的两条路径：`config.json` 的 `agents` 段 + `agents/*.json` 目录；
4. 学会了 **permissions 双保险**（工具白名单 + 权限 deny）；
5. 让 `SubAgentRunner` 支持用户自定义的 subagent 角色。

至此，**模块三：核心架构** 全部完结。下一步我们将进入 **模块四：手写实战**，从零构建完整的桌面 Code Agent 应用。在实战第一篇（第 15 课）中，我们会把前 14 课学到的全部机制整合进一个可运行的工程。
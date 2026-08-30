"""Agent 类型与注册机制（对应课程第14课，参考 OpenCode Agent 设计）。

OpenCode 内置两种 primary agent：
- build：默认，拥有全部工具，负责实际的开发工作；
- plan：只读、禁止编辑与执行命令，只做分析与规划。

用户还可以通过配置文件自定义 agent（参考 opencode.json 的 agent 段），
每个 agent 可以指定：system prompt、模型、temperature、可用工具（裁剪）、
以及权限覆盖（deny/allow/ask）。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("litecode.agent")

# 工具权限取值
PERM_DENY = "deny"
PERM_ALLOW = "allow"
PERM_ASK = "ask"


def parse_frontmatter(text: str):
    """解析 Markdown 文件的 YAML frontmatter（--- 包裹的头部）。

    返回 (frontmatter_dict, 正文)。
    仅支持 Agent 配置常用的子集：
    - 标量（字符串 / 数字 / 布尔）
    - 列表（- item）
    - 嵌套 map（key: value）
    不依赖 PyYAML。
    """
    data: Dict[str, Any] = {}
    m = re.match(r"^\ufeff?---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return data, text
    body = m.group(1)
    rest = text[m.end():]

    def parse_value(raw: str):
        raw = raw.strip()
        if not raw:
            return None
        if raw == "true":
            return True
        if raw == "false":
            return False
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            pass
        return raw.strip('"').strip("'")

    # 逐行解析，支持列表与嵌套 map
    lines = body.split("\n")
    stack: List[Dict[str, Any]] = [data]
    list_indent: Dict[int, int] = {}
    last_key: List[Optional[str]] = [None]

    for line in lines:
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if stripped.startswith("- "):
            item = parse_value(stripped[2:])
            parent = stack[indent // 2] if indent // 2 < len(stack) else data
            parent.setdefault("__list__", []).append(item)
            continue

        if ":" not in stripped:
            continue
        key, _, raw_val = stripped.partition(":")
        key = key.strip()
        val = parse_value(raw_val)

        # 收缩栈：缩进减小则回退
        while len(stack) > 1 and indent // 2 < len(stack) - 1:
            stack.pop()
            last_key.pop()
        current = stack[-1]

        if val is None and raw_val.strip() == "":
            # 子 map 开始
            child: Dict[str, Any] = {}
            current[key] = child
            stack.append(child)
            last_key.append(key)
        else:
            current[key] = val
            last_key[-1] = key

    # 把 __list__ 转成真列表（inline 用 dict 简化，这里直接保留）
    def normalize(node: Any) -> Any:
        if isinstance(node, dict):
            if "__list__" in node:
                return node["__list__"]
            return {k: normalize(v) for k, v in node.items() if k != "__list__"}
        return node

    return normalize(data), rest


@dataclass
class AgentProfile:
    """一个 Agent 的完整描述。

    字段对齐 OpenCode 的 agent 配置：
    - id:            agent 唯一标识（文件名或配置 key）
    - mode:          "primary"（Tab 切换的主 agent）| "subagent"（可被 @ 引用）
    - description:   agent 作用描述
    - system_prompt: 自定义 System Prompt（None 则用全局默认）
    - model:         覆盖模型（None 用全局模型）
    - temperature:   覆盖 temperature（None 用全局）
    - tools:         允许使用的工具列表（None = 全部；[] = 只读）
    - permissions:   工具权限覆盖 {工具名或通配: "deny"|"allow"|"ask"}
    - hidden:        是否在 UI 隐藏
    """

    id: str
    mode: str = "primary"
    description: str = ""
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    tools: Optional[List[str]] = None
    permissions: Dict[str, str] = field(default_factory=dict)
    hidden: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "description": self.description,
            "model": self.model,
            "temperature": self.temperature,
            "tools": self.tools,
            "permissions": self.permissions,
            "hidden": self.hidden,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentProfile":
        return cls(
            id=data.get("id", ""),
            mode=data.get("mode", "primary"),
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt"),
            model=data.get("model"),
            temperature=data.get("temperature"),
            tools=data.get("tools"),
            permissions=data.get("permissions") or {},
            hidden=bool(data.get("hidden", False)),
        )


# ---------------------------------------------------------------- 内置默认 agent

PLAN_PROMPT = """你是一个「规划型」AI 软件工程师（Plan Agent）。当前模式只做分析与规划，**禁止修改任何文件、禁止执行命令**。

你的职责：
1. 理解用户需求，先探查代码库结构与相关文件内容；
2. 输出一份清晰、可执行、分步骤的实现计划（含涉及文件、改动点、验证方式）；
3. 除非用户明确要求，否则不要动手改代码。

可用工具受限为只读（读文件、搜索、git 状态查看等）。"""


def default_build_agent() -> AgentProfile:
    return AgentProfile(
        id="build",
        mode="primary",
        description="默认开发 Agent：拥有全部工具，负责实际的编码与执行。",
        permissions={},
    )


def default_plan_agent() -> AgentProfile:
    return AgentProfile(
        id="plan",
        mode="primary",
        description="规划 Agent：只读分析与方案设计，禁止修改文件与执行命令。",
        system_prompt=PLAN_PROMPT,
        # 只读工具白名单
        tools=[
            "read_file", "list_dir", "file_tree", "search_code",
            "get_file_outline", "read_focused_symbol",
            "git_status", "git_diff", "git_log", "git_branch", "review_code",
            "webfetch", "webfetch_batch",
        ],
        # 权限兜底：即使被授予写工具也强制 deny
        permissions={"write_file": PERM_DENY, "apply_search_replace": PERM_DENY,
                     "apply_unified_diff": PERM_DENY, "execute_command": PERM_DENY},
    )


# ---------------------------------------------------------------- Agent 注册表

class AgentRegistry:
    """管理内置 + 用户自定义的 Agent。

    自定义 agent 来源（参考 OpenCode）：
    1. config.json 的 "agents" 段（JSON 格式）；
    2. .lite-code/agents/*.json 目录下的 agent 描述文件。
    """

    def __init__(self) -> None:
        self._agents: Dict[str, AgentProfile] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self._agents["build"] = default_build_agent()
        self._agents["plan"] = default_plan_agent()

    # ------------------------------------------------------------ 查询

    def get(self, agent_id: str) -> AgentProfile:
        if agent_id not in self._agents:
            raise KeyError(f"未知 Agent: {agent_id}（可用: {', '.join(self.list_primary())}）")
        return self._agents[agent_id]

    def list_primary(self) -> List[str]:
        return [a.id for a in self._agents.values()
                if a.mode in ("primary", "all") and not a.hidden]

    def list_subagents(self) -> List[str]:
        return [a.id for a in self._agents.values()
                if a.mode in ("subagent", "all") and not a.hidden]

    def all(self) -> Dict[str, AgentProfile]:
        return dict(self._agents)

    def to_config(self) -> Dict[str, Any]:
        return {aid: p.to_dict() for aid, p in self._agents.items()}

    # ------------------------------------------------------------ 加载自定义

    def load_dir(self, agents_dir: str) -> None:
        """扫描 agents 目录下的 *.json / *.md 文件并注册自定义 agent。

        - .json：AgentProfile 的字段
        - .md：YAML frontmatter（AgentProfile 字段）+ 正文（system_prompt），
          文件名即 agent id（对齐 OpenCode 的 markdown agent）。
        """
        if not os.path.isdir(agents_dir):
            return
        for fname in sorted(os.listdir(agents_dir)):
            path = os.path.join(agents_dir, fname)
            base, ext = os.path.splitext(fname)
            try:
                if ext == ".json":
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    profile = AgentProfile.from_dict(data)
                    if not profile.id:
                        profile.id = base
                elif ext == ".md":
                    with open(path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    data, body = parse_frontmatter(raw)
                    profile = AgentProfile.from_dict(data)
                    profile.id = profile.id or base
                    if body.strip() and not profile.system_prompt:
                        profile.system_prompt = body.strip()
                else:
                    continue
                self.register(profile)
                logger.info("[Agent] 已加载自定义 agent: %s (%s)", profile.id, path)
            except Exception:
                logger.exception("[Agent] 加载 agent 失败: %s", path)

    def load_config(self, agents_cfg: Optional[Dict[str, Any]]) -> None:
        """从 config.json 的 "agents" 段加载自定义 agent。"""
        if not agents_cfg:
            return
        for aid, data in agents_cfg.items():
            if not isinstance(data, dict):
                continue
            try:
                profile = AgentProfile.from_dict({**data, "id": data.get("id", aid)})
                self.register(profile)
                logger.info("[Agent] 已从配置注册 agent: %s", profile.id)
            except Exception:
                logger.exception("[Agent] 注册 agent 失败: %s", aid)

    def register(self, profile: AgentProfile) -> None:
        self._agents[profile.id] = profile

    def save(self, profile: AgentProfile, agents_dir: str) -> str:
        """持久化一个自定义 agent 到 agents 目录（JSON 文件）。"""
        os.makedirs(agents_dir, exist_ok=True)
        path = os.path.join(agents_dir, f"{profile.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        self.register(profile)
        return path

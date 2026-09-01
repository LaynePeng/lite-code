"""AgentApp 装配层：把内核 / LLM / 工具 / 安全 / 会话组装为可运行的 Agent 应用。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from .core.agent_loop import AgentLoop
from .core.agent_profile import AgentProfile, AgentRegistry
from .core.context_manager import ContextManager
from .core.kernel import Kernel
from .core.session_store import SessionStore
from .core.types import Plugin
from .llm.base import BaseLLMAdapter
from .llm.registry import LLMRegistry
from .mcp import MCPManager
from .security.approval import ApprovalGate
from .security.guard import SecurityGuard
from .security.plugin import SecurityPlugin
from .tools.plugin import (
    ASTPlugin,
    CodebasePlugin,
    EditorPlugin,
    FileSystemPlugin,
    GitPlugin,
    ReviewPlugin,
    ShellPlugin,
    SkillsPlugin,
    SubAgentPlugin,
    TOOL_FILTER_SERVICE,
    TOOLS_SERVICE,
    WebFetchPlugin,
)
from .tools.registry import ToolRegistry

logger = logging.getLogger("litecode.app")

DEFAULT_CONFIG: Dict[str, Any] = {
    "max_steps": 100,
    "token_budget": 48000,
    "tool_timeout": 120,
    "llm_timeout": 180,
    "auto_approve": False,
    "approval_timeout": 600,
    "context_full_turns": 2,
    "mcp_servers": {},
    "pricing": {"input_per_mtok": 1.6, "output_per_mtok": 4.8},
}

TOOL_NAMES = [
    "read_file", "write_file", "list_dir", "file_tree",
    "search_code", "get_file_outline", "read_focused_symbol",
    "apply_search_replace", "apply_unified_diff",
    "execute_command", "git_status", "git_diff", "git_log",
    "git_commit", "git_branch", "review_code", "spawn_sub_agent",
        "webfetch", "webfetch_batch", "load_skill",
]


class AgentApp:
    def __init__(
        self,
        workspace: Optional[str] = None,
        config_dir: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        # Desktop application starts without a project. Keep this as an explicit
        # state instead of silently treating the application's cwd as a workspace.
        self.workspace = os.path.abspath(workspace) if workspace else None
        if self.workspace:
            os.makedirs(self.workspace, exist_ok=True)

        self.config_dir = os.path.abspath(os.path.expanduser(config_dir or "~/.lite-code"))
        self.config_path = os.path.join(self.config_dir, "config.json")
        os.makedirs(self.config_dir, exist_ok=True)

        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)

        # 安全组件（先于配置加载，供默认配置落盘引用）
        self.guard = SecurityGuard()
        self.approval_gate = ApprovalGate(timeout_seconds=600)
        self.llm_registry = LLMRegistry(config_dir=self.config_dir)
        self.agent_registry = AgentRegistry()
        self._context_session_stats: Dict[str, Dict[str, Any]] = {}
        self._load_config()
        self.mcp_manager = MCPManager(self.config.get("mcp_servers") or {})
        self.approval_gate = ApprovalGate(
            timeout_seconds=self.config.get("approval_timeout", 600)
        )

        # 会话存储
        self.session_store = SessionStore(os.path.join(self.config_dir, "sessions"))

        # 兼容旧配置：base_url/model 回填
        if base_url and not self.llm_registry.get_active_provider_settings().get("base_url"):
            self.llm_registry.providers["deepseek"]["base_url"] = base_url
        if model and not self.llm_registry.get_active_provider_settings().get("model"):
            self.llm_registry.providers["deepseek"]["model"] = model
        # 环境变量/key 回填
        self._apply_env_api_key(api_key)

        # 子 Agent 运行器（延迟绑定）
        from .orchestration.sub_agent import SubAgentRunner
        self.sub_agent_runner = SubAgentRunner(self)

    def _apply_env_api_key(self, cli_key: Optional[str] = None) -> None:
        """CLI 传入的 --api-key 回填到所有未配置的供应商。"""
        if cli_key:
            for pid in self.llm_registry.providers:
                p = self.llm_registry.providers[pid]
                if not p.get("api_key"):
                    p["api_key"] = cli_key
        # 确保环境变量中的 key 也注入（registry 已处理，但以防配置被后续覆盖）
        self.llm_registry._apply_env_defaults()

    # ------------------------------------------------------------ 配置

    def _load_config(self) -> None:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.config.update({k: v for k, v in loaded.items() if v is not None})
                if self.config.get("max_steps") == 25:
                    self.config["max_steps"] = 100
                security_cfg = loaded.get("security")
                if isinstance(security_cfg, dict):
                    self.guard.apply_config(security_cfg)
                llm_cfg = loaded.get("llm")
                if isinstance(llm_cfg, dict):
                    self.llm_registry.apply_config(llm_cfg)
                agents_cfg = loaded.get("agents")
                if isinstance(agents_cfg, dict):
                    self.agent_registry.load_config(agents_cfg)
                logger.info("[App] 配置文件已加载: %s", self.config_path)
            except Exception:
                logger.exception("[App] 配置文件解析失败，使用默认配置")
        else:
            self._write_default_config()
        # 扫描 agents 目录下的自定义 agent 文件
        self.agent_registry.load_dir(os.path.join(self.config_dir, "agents"))

    def _write_default_config(self) -> None:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(
                    {**DEFAULT_CONFIG, "llm": self.llm_registry.to_config(),
                     "security": self.guard.to_dict()},
                    f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def save_config(self, updates: Dict[str, Any]) -> None:
        self.config.update({k: v for k, v in updates.items() if v is not None})
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def update_security_rules(self, rules: Dict[str, Any]) -> None:
        """热更新安全规则（动态黑白名单）并落盘。"""
        self.guard.apply_config(rules)
        self.config["security"] = rules
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def mcp_status(self) -> Dict[str, Any]:
        return self.mcp_manager.status()

    async def update_mcp_servers(self, servers: Dict[str, Any]) -> Dict[str, Any]:
        """更新 MCP Server 配置：落盘 + 热重连。"""
        self.config["mcp_servers"] = servers
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
        return await self.mcp_manager.reload(servers)

    # ------------------------------------------------------------ LLM

    def _persist_config(self) -> None:
        self.config["llm"] = self.llm_registry.to_config(persist_key=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    async def close_adapter(self) -> None:
        """异步关闭当前 LLM 适配器，供配置热更新时释放连接。"""
        try:
            adapter = self.llm_registry.get_adapter()
            await adapter.close()
        except Exception:
            pass
        self.llm_registry.reset_adapter()

    @property
    def adapter(self) -> BaseLLMAdapter:
        if getattr(self, "_mock_adapter", None) is not None:
            return self._mock_adapter
        try:
            return self.llm_registry.get_adapter()
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    def get_llm_config(self) -> Dict[str, Any]:
        return self.llm_registry.to_config()

    def update_llm_config(
        self,
        active: Optional[str] = None,
        providers: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """更新 LLM 配置并重建适配器。providers 中 api_key 为脱敏值时忽略。"""
        if active:
            self.llm_registry.active = active
        if providers:
            for pid, settings in providers.items():
                if pid not in self.llm_registry.providers:
                    if not pid.startswith("custom_"):
                        continue
                    self.llm_registry.providers[pid] = {
                        "name": settings.get("name") or pid, "api_key": "",
                        "base_url": "", "model": "", "models": [], "temperature": 0.2,
                    }
                current = self.llm_registry.providers[pid]
                # 跳过脱敏 api_key（含 … 或 **** 视为未修改）
                if settings.get("api_key") and ("…" in settings["api_key"] or settings["api_key"] == "****"):
                    settings.pop("api_key")
                elif "api_key" in settings and not settings["api_key"]:
                    settings.pop("api_key")
                # 前端回传的 has_key 是 UI 展示用标记，不并入 provider 配置
                settings.pop("has_key", None)
                # 空/无效的 context_window（手动覆盖）视为未设置
                if settings.get("context_window") in (None, "", 0):
                    settings.pop("context_window", None)
                # custom_headers 必须是 str:str 字典；非法或空字典视为未设置
                raw_headers = settings.get("custom_headers")
                if not isinstance(raw_headers, dict) or not raw_headers:
                    settings.pop("custom_headers", None)
                else:
                    settings["custom_headers"] = {
                        str(k).strip(): str(v).strip()
                        for k, v in raw_headers.items()
                        if str(k).strip() and str(v).strip()
                    }
                merged = {**current, **settings}
                if pid.startswith("custom_"):
                    merged["name"] = str(merged.get("name") or pid).strip()
                self.llm_registry.providers[pid] = merged
        # UI 提交的是完整列表；未提交的自定义实例表示用户删除了它。
        if providers is not None:
            kept = set(providers)
            for pid in list(self.llm_registry.providers):
                if pid.startswith("custom_") and pid not in kept:
                    del self.llm_registry.providers[pid]
        if self.llm_registry.active not in self.llm_registry.providers:
            self.llm_registry.active = "deepseek"
        self.llm_registry.reset_adapter()
        self._persist_config()
        return self.llm_registry.to_config()

    async def test_llm(self, provider_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ok, msg, elapsed = await self.llm_registry.test_connection(provider_id, overrides)
        return {"ok": ok, "message": msg, "latency_ms": int(elapsed)}

    def llm_provider_meta(self) -> List[Dict[str, Any]]:
        return self.llm_registry.provider_meta()

    def refresh_model_meta(self) -> bool:
        """同步 models.dev 模型元数据（启动时调用，失败静默降级）。"""
        return self.llm_registry.refresh_models_dev()

    # ------------------------------------------------------------ 上下文统计

    def accumulate_context_stats(self, session_id: str, task_stats: Dict[str, Any]) -> Dict[str, Any]:
        """把一次任务内的上下文统计累加进会话级累计，返回最新会话累计。"""
        acc = self._context_session_stats.setdefault(session_id, {
            "prompt_tokens": 0, "output_tokens": 0,
            "cache_hit_tokens": 0, "cache_miss_tokens": 0,
            "compression_count": 0, "compressed_tokens": 0,
        })
        for key in ("prompt_tokens", "output_tokens", "cache_hit_tokens",
                    "cache_miss_tokens", "compression_count", "compressed_tokens"):
            acc[key] += int(task_stats.get(key, 0) or 0)
        hit = acc["cache_hit_tokens"]
        miss = acc["cache_miss_tokens"]
        acc["cache_hit_rate"] = round(hit / (hit + miss), 4) if (hit + miss) > 0 else None
        return dict(acc)

    def get_context_session_stats(self, session_id: str) -> Dict[str, Any]:
        return dict(self._context_session_stats.get(session_id, {}))

    # ------------------------------------------------------------ 工具

    def tool_plugins(self) -> List[Plugin]:
        """Cordis 风格工具插件清单（空间解耦：工具能力全部由插件提供）。"""
        return [
            FileSystemPlugin(self.workspace),
            CodebasePlugin(self.workspace),
            ASTPlugin(self.workspace),
            EditorPlugin(self.workspace),
            ShellPlugin(self.workspace),
            GitPlugin(self.workspace),
            ReviewPlugin(self.workspace),
            WebFetchPlugin(cache_dir=os.path.join(self.config_dir, "webfetch_cache")),
            SubAgentPlugin(self),
            SkillsPlugin(self.workspace),
        ]

    @staticmethod
    def _tool_filter(
        allowed: Optional[List[str]],
        exclude: Optional[List[str]],
        permissions: Optional[Dict[str, str]],
    ):
        """构造工具裁剪策略（Agent 配置：allowed / exclude / deny 权限）。"""

        def allow(name: str) -> bool:
            if allowed is not None and name not in allowed:
                return False
            if exclude and name in exclude:
                return False
            if permissions and permissions.get(name) == "deny":
                return False
            return True

        return allow

    def build_registry(
        self,
        allowed: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        permissions: Optional[Dict[str, str]] = None,
    ) -> ToolRegistry:
        """通过 Cordis 内核组装工具集：插件安装到引导内核，注册进 tools 服务。

        与 AgentLoop 一致：内核持有 tools 服务（ToolRegistry），
        插件通过依赖注入获取并注册自己的工具。
        """
        kernel = Kernel(session_id="tool-bootstrap")
        registry = ToolRegistry()
        kernel.register_service(TOOLS_SERVICE, registry)
        kernel.register_service(TOOL_FILTER_SERVICE, self._tool_filter(allowed, exclude, permissions))
        for plugin in self.tool_plugins():
            kernel.use(plugin)
        self.mcp_manager.register_tools(registry, allowed=allowed, exclude=exclude)
        return registry

    # ------------------------------------------------------------ 内核

    def create_kernel(self, session_id: str, registry: Optional[ToolRegistry] = None) -> Kernel:
        """Cordis 内核装配：工具插件 + 安全插件全部挂载，服务进入依赖注入容器。

        - registry 为 None：挂载全量工具（默认内核）。
        - registry 已传入（build_registry 按 Agent 裁剪后的）：直接挂为 tools 服务，
          不再重复安装工具插件——否则插件会把被裁剪掉的工具重新注册进
          registry（无 tool_filter 服务时插件全量注册），plan 只读模式失效。
        """
        kernel = Kernel(session_id)
        if registry is not None:
            kernel.register_service(TOOLS_SERVICE, registry)
            if registry.has("spawn_sub_agent"):
                from .tools.sub_agent import make_sub_agent_handler

                registry.set_handler(
                    "spawn_sub_agent", make_sub_agent_handler(self, kernel.events)
                )
        else:
            kernel.register_service(TOOLS_SERVICE, ToolRegistry())
            for plugin in self.tool_plugins():
                kernel.use(plugin)
        kernel.use(SecurityPlugin(self.guard, self.approval_gate, self.workspace))
        kernel.register_service("app", self)
        return kernel

    # ------------------------------------------------------------ Agent

    def get_agent(self, agent_id: Optional[str] = None) -> AgentProfile:
        """获取 Agent 配置：不传则返回默认 build agent。"""
        return self.agent_registry.get(agent_id or "build")

    def agents_meta(self) -> List[Dict[str, Any]]:
        """供 UI/CLI 列出全部可选 agent。"""
        return [p.to_dict() for p in self.agent_registry.all().values()]

    def create_agent_registry(self, agent_id: str) -> ToolRegistry:
        """按 Agent 配置裁剪工具集（参考 OpenCode：plan 只读、build 全量）。"""
        profile = self.get_agent(agent_id)
        registry = self.build_registry(
            allowed=profile.tools,
            exclude=["spawn_sub_agent"] if agent_id == "plan" else None,
            permissions=profile.permissions,
        )
        return registry

    def create_loop(self, kernel: Kernel, registry: ToolRegistry, agent_id: Optional[str] = None,
                    model_override: Optional[Dict[str, str]] = None) -> AgentLoop:
        profile = self.get_agent(agent_id)
        adapter = self.adapter
        if model_override or profile.model or profile.temperature is not None:
            overrides: Dict[str, Any] = {}
            provider_id = model_override.get("provider") if model_override else None
            if model_override and model_override.get("model"):
                overrides["model"] = model_override["model"]
            elif profile.model:
                overrides["model"] = profile.model
            if profile.temperature is not None:
                overrides["temperature"] = profile.temperature
            adapter = self.llm_registry.build_adapter(provider_id=provider_id, overrides=overrides)
        # 上下文压缩：策略 B（保留最近 N 轮完整细节），预算 = min(token_budget, 90%×窗口)
        token_budget = int(self.config.get("token_budget", 48000))
        context_manager = ContextManager(
            token_budget,
            keep_recent_full_turns=int(self.config.get("context_full_turns", 2)),
        )
        context_window = self.llm_registry.get_context_window(
            getattr(adapter, "provider_id", None) or self.llm_registry.active,
            getattr(adapter, "model", None),
        )
        loop = AgentLoop(
            kernel=kernel,
            adapter=adapter,
            registry=registry,
            session_store=self.session_store,
            context_manager=context_manager,
            max_steps=int(self.config.get("max_steps", 100)),
            tool_timeout=float(self.config.get("tool_timeout", 120)),
            llm_timeout=float(self.config.get("llm_timeout", 180)),
            token_budget=token_budget,
            pricing=self.config.get("pricing", DEFAULT_CONFIG["pricing"]),
            auto_approve=bool(self.config.get("auto_approve", False)),
            context_window=context_window,
        )
        loop.workspace = self.workspace
        loop.truncation_dir = os.path.join(self.config_dir, "truncations")
        return loop

    async def close(self) -> None:
        await self.mcp_manager.close()
        await self.close_adapter()

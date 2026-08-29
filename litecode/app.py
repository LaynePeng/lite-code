"""AgentApp 装配层：把内核 / LLM / 工具 / 安全 / 会话组装为可运行的 Agent 应用。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from .core.agent_loop import AgentLoop
from .core.agent_profile import AgentProfile, AgentRegistry
from .core.kernel import Kernel
from .core.session_store import SessionStore
from .llm.base import BaseLLMAdapter
from .llm.registry import LLMRegistry
from .security.approval import ApprovalGate
from .security.guard import SecurityGuard
from .security.plugin import SecurityPlugin
from .tools.ast_tools import ASTTools
from .tools.codebase import CodebaseTools
from .tools.editor import EditorTools
from .tools.filesystem import FileSystemTools
from .tools.git import GitTools
from .tools.registry import ToolRegistry
from .tools.review import ReviewTools
from .tools.shell import ShellTools

logger = logging.getLogger("litecode.app")

DEFAULT_CONFIG: Dict[str, Any] = {
    "max_steps": 25,
    "token_budget": 48000,
    "tool_timeout": 120,
    "auto_approve": False,
    "approval_timeout": 600,
    "pricing": {"input_per_mtok": 1.6, "output_per_mtok": 4.8},
}

TOOL_NAMES = [
    "read_file", "write_file", "list_dir", "file_tree",
    "search_code", "get_file_outline", "read_focused_symbol",
    "apply_search_replace", "apply_unified_diff",
    "execute_command", "git_status", "git_diff", "git_log",
    "git_commit", "git_branch", "review_code", "spawn_sub_agent",
]


class AgentApp:
    def __init__(
        self,
        workspace: Optional[str] = None,
        config_dir: str = ".lite-code",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.workspace = os.path.abspath(workspace or os.getcwd())
        os.makedirs(self.workspace, exist_ok=True)

        self.config_dir = os.path.abspath(config_dir)
        self.config_path = os.path.join(self.config_dir, "config.json")
        os.makedirs(self.config_dir, exist_ok=True)

        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)

        # 安全组件（先于配置加载，供默认配置落盘引用）
        self.guard = SecurityGuard()
        self.approval_gate = ApprovalGate(timeout_seconds=600)
        self.llm_registry = LLMRegistry()
        self.agent_registry = AgentRegistry()
        self._load_config()
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
                    continue
                current = self.llm_registry.providers[pid]
                # 跳过脱敏 api_key（含 … 或 **** 视为未修改）
                if settings.get("api_key") and ("…" in settings["api_key"] or settings["api_key"] == "****"):
                    settings.pop("api_key")
                elif "api_key" in settings and not settings["api_key"]:
                    settings.pop("api_key")
                # 前端回传的 has_key 是 UI 展示用标记，不并入 provider 配置
                settings.pop("has_key", None)
                self.llm_registry.providers[pid] = {**current, **settings}
        self.llm_registry.reset_adapter()
        self._persist_config()
        return self.llm_registry.to_config()

    async def test_llm(self, provider_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ok, msg, elapsed = await self.llm_registry.test_connection(provider_id, overrides)
        return {"ok": ok, "message": msg, "latency_ms": int(elapsed)}

    def llm_provider_meta(self) -> List[Dict[str, Any]]:
        return self.llm_registry.provider_meta()

    # ------------------------------------------------------------ 工具

    def build_registry(
        self,
        allowed: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        permissions: Optional[Dict[str, str]] = None,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        fs_tools = FileSystemTools(self.workspace)
        codebase = CodebaseTools(self.workspace)
        ast_tools = ASTTools(self.workspace)
        editor = EditorTools(self.workspace)
        shell = ShellTools(self.workspace)
        git = GitTools(self.workspace)
        review = ReviewTools(self.workspace)

        def allow(name: str) -> bool:
            if allowed is not None and name not in allowed:
                return False
            if exclude and name in exclude:
                return False
            if permissions and permissions.get(name) == "deny":
                return False
            return True

        if allow("read_file") or allow("write_file") or allow("list_dir") or allow("file_tree"):
            for t in fs_tools.get_tools():
                if allow(t.name):
                    registry.register(t.name, t.description, t.parameters,
                                      lambda args, n=t.name: fs_tools.execute(n, args))
        if allow("search_code"):
            registry.register("search_code", codebase.get_tools()[0].description,
                              codebase.get_tools()[0].parameters,
                              lambda args: codebase.execute("search_code", args))
        if allow("get_file_outline") or allow("read_focused_symbol"):
            for t in ast_tools.get_tools():
                if allow(t.name):
                    registry.register(t.name, t.description, t.parameters,
                                      lambda args, n=t.name: ast_tools.execute(n, args))
        if allow("apply_search_replace") or allow("apply_unified_diff"):
            for t in editor.get_tools():
                if allow(t.name):
                    registry.register(t.name, t.description, t.parameters,
                                      lambda args, n=t.name: editor.execute(n, args))
        if allow("execute_command"):
            registry.register("execute_command", shell.get_tools()[0].description,
                              shell.get_tools()[0].parameters,
                              lambda args: shell.execute("execute_command", args))
        for t in git.get_tools():
            if allow(t.name):
                registry.register(t.name, t.description, t.parameters,
                                  lambda args, n=t.name: git.execute(n, args))
        if allow("review_code"):
            registry.register("review_code", review.get_tools()[0].description,
                              review.get_tools()[0].parameters,
                              lambda args: review.execute("review_code", args))
        if allow("spawn_sub_agent"):
            from .tools.sub_agent import make_sub_agent_handler
            t = registry.__class__.__name__  # noqa: F841
            registry.register(
                "spawn_sub_agent",
                "派生一个独立且上下文隔离的子 Agent 执行耗时的调研/测试/重构子任务，返回汇总报告",
                {
                    "type": "object",
                    "properties": {
                        "taskDescription": {"type": "string", "description": "指派给子 Agent 的具体任务指令"},
                        "roleType": {
                            "type": "string",
                            "enum": ["explorer", "tester", "refactor", "general"],
                            "description": "子 Agent 角色：explorer(只读调研)/tester(测试执行)/refactor(完整重构)",
                        },
                    },
                    "required": ["taskDescription"],
                },
                make_sub_agent_handler(self),
            )
        return registry

    # ------------------------------------------------------------ 内核

    def create_kernel(self, session_id: str) -> Kernel:
        kernel = Kernel(session_id)
        kernel.use(SecurityPlugin(self.guard, self.approval_gate))
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
        return self.build_registry(
            allowed=profile.tools,
            exclude=["spawn_sub_agent"] if agent_id == "plan" else None,
            permissions=profile.permissions,
        )

    def create_loop(self, kernel: Kernel, registry: ToolRegistry, agent_id: Optional[str] = None) -> AgentLoop:
        profile = self.get_agent(agent_id)
        adapter = self.adapter
        if profile.model or profile.temperature is not None:
            overrides = {}
            if profile.model:
                overrides["model"] = profile.model
            if profile.temperature is not None:
                overrides["temperature"] = profile.temperature
            adapter = self.llm_registry.build_adapter(overrides=overrides)
        loop = AgentLoop(
            kernel=kernel,
            adapter=adapter,
            registry=registry,
            session_store=self.session_store,
            max_steps=int(self.config.get("max_steps", 25)),
            tool_timeout=float(self.config.get("tool_timeout", 120)),
            token_budget=int(self.config.get("token_budget", 48000)),
            pricing=self.config.get("pricing", DEFAULT_CONFIG["pricing"]),
            auto_approve=bool(self.config.get("auto_approve", False)),
        )
        loop.workspace = self.workspace
        loop.truncation_dir = os.path.join(self.config_dir, "truncations")
        return loop

    async def close(self) -> None:
        await self.close_adapter()
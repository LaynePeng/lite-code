"""LLM 供应商注册表：管理多供应商配置、构建适配器、测试连接。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .anthropic import AnthropicAdapter
from .base import BaseLLMAdapter
from .openai_compat import OpenAICompatAdapter

logger = logging.getLogger("litecode.llm")

# 预置供应商元数据
PROVIDER_META: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "name": "DeepSeek",
        "kind": "openai",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
        "env_key": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "name": "OpenAI",
        "kind": "openai",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini"],
        "env_key": "OPENAI_API_KEY",
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "kind": "openai",
        "default_base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-32k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-k2-0711-preview"],
        "env_key": "MOONSHOT_API_KEY",
    },
    "qwen": {
        "name": "通义千问 (DashScope)",
        "kind": "openai",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
        "env_key": "DASHSCOPE_API_KEY",
    },
    "glm": {
        "name": "智谱 GLM",
        "kind": "openai",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air", "glm-4-long"],
        "env_key": "ZHIPUAI_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "kind": "anthropic",
        "default_base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-20250514",
        "models": ["claude-sonnet-4-20250514", "claude-3-7-sonnet-20250219",
                   "claude-3-5-sonnet-20241022", "claude-opus-4-20250514"],
        "env_key": "ANTHROPIC_API_KEY",
    },
    "custom": {
        "name": "自定义 (OpenAI 兼容)",
        "kind": "openai",
        "default_base_url": "",
        "default_model": "",
        "models": [],
        "env_key": "",
    },
}


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}…{key[-4:]}"


def _is_masked_key(key: str) -> bool:
    """判断是否是脱敏 key（含省略号或全星号），不能作为真实 key 使用。"""
    return "…" in key or key == "****"


class LLMRegistry:
    """管理多供应商配置并构建适配器。

    配置结构（config.json 的 "llm" 段）:
    {
      "active": "deepseek",
      "providers": {
        "deepseek": {"api_key": "...", "base_url": "...", "model": "...", "temperature": 0.2},
        ...
      }
    }
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None) -> None:
        self.providers: Dict[str, Dict[str, Any]] = {
            pid: {
                "api_key": "",
                "base_url": meta["default_base_url"],
                "model": meta["default_model"],
                "temperature": 0.2,
            }
            for pid, meta in PROVIDER_META.items()
        }
        self.active = "deepseek"
        self._adapter: Optional[BaseLLMAdapter] = None
        self._apply_env_defaults()
        if llm_config:
            self.apply_config(llm_config)

    def _apply_env_defaults(self) -> None:
        """从环境变量兜底注入 API Key。"""
        import os

        for pid, meta in PROVIDER_META.items():
            env_key = meta.get("env_key")
            if env_key:
                val = os.environ.get(env_key, "").strip()
                if val:
                    self.providers[pid]["api_key"] = val

    # ------------------------------------------------------------ 配置

    def apply_config(self, config: Dict[str, Any]) -> None:
        if config.get("active"):
            self.active = config["active"]
        for pid, settings in (config.get("providers") or {}).items():
            if pid not in self.providers:
                continue
            # 跳过脱敏 / 空的 api_key，防止用「sk-c…1f74」这种脱敏值覆盖真实 key
            api_key = settings.get("api_key")
            if api_key is None or api_key == "" or _is_masked_key(api_key):
                settings = {k: v for k, v in settings.items() if k != "api_key"}
            merged = {**self.providers[pid], **{k: v for k, v in settings.items() if v is not None}}
            self.providers[pid] = merged
        # 环境变量兜底（配置为空时）
        self._apply_env_defaults()

    def to_config(self, persist_key: bool = False) -> Dict[str, Any]:
        """导出配置。

        persist_key=False（API 返回）：api_key 不回写，仅保留 has_key 标记，避免泄露真实 key。
        persist_key=True（落盘）：写入真实 api_key，保证重启后 key 不丢失。
        """
        providers = {}
        for pid, p in self.providers.items():
            providers[pid] = {
                "api_key": p.get("api_key", "") if persist_key else "",
                "has_key": bool(p.get("api_key")),
                "base_url": p.get("base_url", ""),
                "model": p.get("model", ""),
                "temperature": p.get("temperature", 0.2),
            }
        return {"active": self.active, "providers": providers}

    def provider_meta(self) -> List[Dict[str, Any]]:
        out = []
        for pid, meta in PROVIDER_META.items():
            p = self.providers.get(pid, {})
            out.append({
                "id": pid,
                "name": meta["name"],
                "kind": meta["kind"],
                "models": meta["models"],
                "default_base_url": meta["default_base_url"],
                "has_key": bool(p.get("api_key")),
                "model": p.get("model", ""),
            })
        return out

    # ------------------------------------------------------------ 适配器

    def get_active_provider_settings(self) -> Dict[str, Any]:
        return self.providers.get(self.active, {})

    def build_adapter(self, provider_id: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> BaseLLMAdapter:
        pid = provider_id or self.active
        meta = PROVIDER_META.get(pid, PROVIDER_META["custom"])
        settings = {**self.providers.get(pid, {}), **(overrides or {})}
        api_key = settings.get("api_key", "")
        # 兜底：overrides 传入脱敏 key 视为未配置，避免「sk-c…1f74」进入真实请求
        if api_key and _is_masked_key(api_key):
            api_key = ""
        if not api_key:
            raise ValueError(f"供应商「{meta['name']}」未配置 API Key")

        common = dict(
            api_key=api_key,
            base_url=settings.get("base_url") or meta["default_base_url"],
            model=settings.get("model") or meta["default_model"],
            temperature=float(settings.get("temperature", 0.2)),
            provider_id=pid,
            enable_cache=bool(settings.get("enable_cache", True)),
        )
        if meta["kind"] == "anthropic":
            return AnthropicAdapter(**common)
        return OpenAICompatAdapter(**common)

    def get_adapter(self) -> BaseLLMAdapter:
        if self._adapter is None:
            self._adapter = self.build_adapter()
        return self._adapter

    def reset_adapter(self) -> None:
        self._adapter = None

    async def test_connection(
        self, provider_id: str, overrides: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, float]:
        try:
            adapter = self.build_adapter(provider_id, overrides)
            return await adapter.test_connection()
        except ValueError as exc:
            return False, str(exc), 0
        except Exception as exc:
            logger.exception("[LLM] 测试连接异常")
            return False, str(exc)[:150], 0
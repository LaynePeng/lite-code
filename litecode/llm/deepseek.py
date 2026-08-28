"""DeepSeek 适配器（向后兼容，委托给 OpenAICompatAdapter）。"""
from __future__ import annotations

from .openai_compat import OpenAICompatAdapter


class DeepSeekAdapter(OpenAICompatAdapter):
    name = "deepseek-adapter"
    provider_id = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        **kwargs,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider_id="deepseek",
            **kwargs,
        )
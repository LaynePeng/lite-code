"""LLM 供应商自定义 Header 测试：合并/覆盖/清洗 + 端到端请求头 + 配置 round-trip。"""
from __future__ import annotations

import httpx
import pytest

from litecode.llm.anthropic import AnthropicAdapter
from litecode.llm.base import clean_custom_headers
from litecode.llm.openai_compat import OpenAICompatAdapter
from litecode.llm.registry import LLMRegistry


# ---------------------------------------------------------------- 清洗

def test_clean_custom_headers_filters_invalid():
    assert clean_custom_headers(None) == {}
    assert clean_custom_headers("not-a-dict") == {}
    assert clean_custom_headers([("X-A", "1")]) == {}
    # 空键/空值/非 str 值被丢弃，其余 strip
    assert clean_custom_headers({
        "": "v", "X-Empty": "  ", "X-Ok": " value ", "X-Num": 123, None: "x",
    }) == {"X-Ok": "value"}


# ---------------------------------------------------------------- 合并语义

def test_openai_headers_merge_and_override():
    adapter = OpenAICompatAdapter(
        api_key="sk-test",
        custom_headers={"X-Title": "My App", "Authorization": "Bearer gateway-token"},
    )
    headers = adapter._headers()
    assert headers["Content-Type"] == "application/json"
    # 自定义头追加
    assert headers["X-Title"] == "My App"
    # 同名覆盖默认认证头（网关自定义鉴权场景）
    assert headers["Authorization"] == "Bearer gateway-token"


def test_anthropic_headers_merge_and_keep_defaults():
    adapter = AnthropicAdapter(
        api_key="sk-ant",
        custom_headers={"anthropic-beta": "output-128k-2025-02-19"},
    )
    headers = adapter._headers()
    assert headers["x-api-key"] == "sk-ant"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-beta"] == "output-128k-2025-02-19"


def test_no_custom_headers_keeps_defaults():
    oa = OpenAICompatAdapter(api_key="sk-test")._headers()
    assert oa == {"Content-Type": "application/json", "Authorization": "Bearer sk-test"}
    an = AnthropicAdapter(api_key="sk-ant")._headers()
    assert an == {
        "Content-Type": "application/json",
        "x-api-key": "sk-ant",
        "anthropic-version": "2023-06-01",
    }


# ---------------------------------------------------------------- 端到端请求头

def _sse_ok(request) -> httpx.Response:
    return httpx.Response(
        200,
        text='data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n',
        headers={"content-type": "text/event-stream"},
    )


async def test_openai_request_carries_custom_headers(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return _sse_ok(request)

    adapter = OpenAICompatAdapter(
        api_key="sk-test",
        custom_headers={"X-Title": "My App", "Authorization": "Bearer gw"},
    )
    monkeypatch.setattr(
        adapter, "_get_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    content, calls, _ = await adapter.chat_stream([], [])
    assert content == "hi"
    assert seen.get("x-title") == "My App"          # httpx 头名小写化
    assert seen.get("authorization") == "Bearer gw"  # 覆盖默认认证头


async def test_anthropic_request_carries_custom_headers(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(
            200,
            text='event: message_start\ndata: {"type":"message_start"}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    adapter = AnthropicAdapter(
        api_key="sk-ant",
        custom_headers={"anthropic-beta": "output-128k"},
    )
    monkeypatch.setattr(
        adapter, "_get_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    # Mock 流不完整没关系，headers 已捕获
    try:
        await adapter.chat_stream([], [])
    except Exception:
        pass
    assert seen.get("anthropic-beta") == "output-128k"
    assert seen.get("x-api-key") == "sk-ant"


# ---------------------------------------------------------------- registry round-trip

def _registry_with_headers() -> LLMRegistry:
    reg = LLMRegistry()
    reg.providers["deepseek"]["api_key"] = "sk-test"
    reg.providers["deepseek"]["custom_headers"] = {"X-Title": "My App"}
    return reg


def test_registry_build_adapter_passes_headers():
    reg = _registry_with_headers()
    adapter = reg.build_adapter("deepseek")
    assert adapter.custom_headers == {"X-Title": "My App"}
    assert adapter._headers()["X-Title"] == "My App"


def test_registry_to_config_exports_headers():
    reg = _registry_with_headers()
    cfg = reg.to_config()  # API 返回（脱敏 key）
    assert cfg["providers"]["deepseek"]["custom_headers"] == {"X-Title": "My App"}
    # 落盘同样保留
    persisted = reg.to_config(persist_key=True)
    assert persisted["providers"]["deepseek"]["custom_headers"] == {"X-Title": "My App"}


def test_registry_apply_config_round_trip():
    reg = _registry_with_headers()
    # to_config → apply_config 完整回路不丢
    cfg = reg.to_config(persist_key=True)
    reg2 = LLMRegistry()
    reg2.apply_config(cfg)
    assert reg2.providers["deepseek"]["custom_headers"] == {"X-Title": "My App"}
    # 空字典（清空所有自定义头）也应被保留，视为显式清空
    reg2.providers["deepseek"]["custom_headers"] = {}
    cfg2 = reg2.to_config(persist_key=True)
    reg3 = LLMRegistry()
    reg3.apply_config(cfg2)
    assert reg3.providers["deepseek"]["custom_headers"] == {}
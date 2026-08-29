在前三课中，我们已经构建了一个完整的 Agent 控制循环：LLM 流式调用、JSON 自愈、死循环检测、Token 预算控制、滑动窗口裁剪、动态 System Prompt。但在实际生产环境中，有一个被严重低估的优化手段——**Prompt 缓存（Prompt Caching）**。

本课我们会深入：
1. 什么是 Prompt 缓存？为什么它能带来 **90% 的输入 Token 成本削减**？
2. 主流供应商（Anthropic、DeepSeek、OpenAI）的缓存机制对比；
3. 缓存命中的关键：**稳定前缀（Stable Prefix）**；
4. 如何在我们的 Harness 中接入缓存，并设计缓存友好的代码结构。

#### 1. 为什么需要 Prompt 缓存？

在 Code Agent 的典型工作流中，**每次 LLM 请求的输入**大致包含：

```
[System Prompt（~1K token）] + [Tool Definitions（~2K token）] + [对话历史]
```

其中 **System Prompt + Tool Definitions** 占 3K+ token，且每次请求**几乎一模一样**。如果每次都为这 3K 稳定前缀付费，一个 50 轮的对话会多花 150K 的输入 Token 费用。

**缓存机制**让供应商在服务器端缓存稳定前缀的 KV 状态。命中后：
- 输入 Token 只收 **10% 的费用**（Anthropic 定价）；
- 首次延迟略高（写缓存），后续请求的 **TTFT（Time to First Token）大幅降低**。

**收益最大化原则**：把不变的部分（System Prompt、工具定义）放在消息列表的最前面，让它们成为缓存键的稳定前缀。

#### 2. 主流供应商缓存方案对比

| 供应商 | 缓存机制 | 断点标注方式 | 缓存窗口 | 定价 |
|--------|----------|-------------|---------|------|
| Anthropic | `cache_control`（ephemeral） | system 字段 + 工具列表最后一个元素 | 5 分钟不命中则过期 | 写缓存 1.25×，命中 0.1× |
| DeepSeek | 自动缓存（无需标注） | 自动检测长前缀 | 5 分钟 | 命中小于 0.1× |
| OpenAI | Prompt Caching | 自动检测，或 `cache_control` 标注 | 5-10 分钟 | 命中 0.5× |
| Google Gemini | Context Caching | 创建显式缓存对象 | 可配置 TTL | 缓存存储费 + 命中 0.25× |

在后续实战中，我们主要对接 **Anthropic 和 DeepSeek/OpenAI 兼容接口**，所以重点实现前两种方案。

#### 3. 缓存命中的关键：稳定前缀设计

要让缓存最大化命中，核心约束只有一条：

> **稳定前缀必须字节一致**——任何在缓存断点之前的插入、删除、修改都会导致缓存 Miss。

这意味着：

**❌ 错误做法**（每次 System Prompt 都重新渲染，导致缓存 Miss）：

```python
# 每次请求前都重新生成 System Prompt
system_prompt = SystemPromptBuilder.build(workspace, tools)
payload["messages"][0] = {"role": "system", "content": system_prompt}
```

**✅ 正确做法**（System Prompt 只构建一次，复用缓存）：

```python
# 首次构建后缓存下来，后续只更新动态部分
class PayloadBuilder:
    def __init__(self, system_prompt: str, tools: List[ToolDefinition]):
        self._system = {"role": "system", "content": system_prompt, "cache_control": {"type": "ephemeral"}}
        self._tools = tools  # 工具定义一般不变化

    def build(self, messages: List[Message]):
        # 稳定前缀（system + tools）不变，变化的部分在对话历史中
        return [self._system] + self._to_api_messages(messages)
```

**注意**：缓存断点（Cache Breakpoint）标注在**稳定前缀的最后一个元素**上，告诉供应商"从这里开始后面的内容不缓存"。

#### 4. 在 Harness 中接入缓存

我们以 Anthropic 适配器为例，实现缓存标注：

```python
# llm/anthropic.py — 用 cache_control 标注 system 和 tools
def _build_payload(self, messages, tools, system, enable_cache=True):
    payload = {
        "model": self.model,
        "max_tokens": self.max_tokens,
        "stream": True,
        "messages": self._to_anthropic_messages(messages),
    }
    if system:
        if enable_cache:
            payload["system"] = [
                {"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}
            ]
        else:
            payload["system"] = [{"type": "text", "text": system}]
    if tools:
        payload["tools"] = [{"name": t.name, "description": t.description,
                             "input_schema": t.parameters} for t in tools]
        if enable_cache and payload["tools"]:
            # 最后一个工具标注缓存断点（Anthropic 最佳实践）
            payload["tools"][-1]["cache_control"] = {"type": "ephemeral"}
    return payload
```

对于 OpenAI 兼容接口（DeepSeek / OpenAI / Kimi 等），标注在 system 消息上：

```python
# llm/openai_compat.py
if self.enable_cache:
    for msg in payload["messages"]:
        if msg.get("role") == "system":
            msg["cache_control"] = {"type": "ephemeral"}
            break
```

#### 5. 缓存感知的 Token 预算管理

缓存命中后，**实际扣费远少于按 Token 数估算的费用**。我们需要在 Token 预算计算中考虑到这一点：

```python
# core/token_counter.py
class TokenCounter:
    @staticmethod
    def estimate_cost(input_tokens: int, output_tokens: int,
                      pricing: dict, cache_hit: bool = False) -> float:
        input_cost = input_tokens / 1_000_000 * pricing.get("input_per_mtok", 0)
        if cache_hit:
            # 缓存命中只收 10% 输入费用
            input_cost *= 0.1
        output_cost = output_tokens / 1_000_000 * pricing.get("output_per_mtok", 0)
        return round(input_cost + output_cost, 4)
```

**重要**：调整 Token 预算策略时，**不能破坏缓存断点**。例如：
- 裁剪历史消息时，只能裁剪**缓存断点之后**的消息；
- 动态 System Prompt 中**不能插入**会改变前缀的消息；
- 工具定义列表的顺序和内容必须稳定。

#### 6. 验证缓存命中

在开发时，可以通过响应头验证缓存是否命中：

```python
# Anthropic 在响应头中返回 cache 状态
response.headers.get("x-request-cache-hit")  # "true" / "false"
```

DeepSeek 的响应中也会包含缓存命中信息：

```json
{
  "usage": {
    "prompt_cache_hit_tokens": 1024,
    "prompt_cache_miss_tokens": 0
  }
}
```

#### 7. 度量缓存：估算与真实 usage 混用

缓存命中率不是玄学，必须用模型返回的 **usage 字段**精确度量。我们的做法是「首次估算兜底，之后真实回填」：

```python
# AgentLoop 内（D2 阶段，core/agent_loop.py）
# 第一次调用前还没有 usage，用 TokenCounter 估算 input_tokens
if not self._last_usage:
    stats["input_tokens"] += TokenCounter.count_messages_tokens(processed)

# 拿到模型返回的 usage 后，用真实值累加（估算永远不准，只做兜底）
self._last_usage = usage or self._last_usage
if usage:
    stats["input_tokens"] += usage.get("prompt_tokens", 0)
    stats["output_tokens"] += usage.get("completion_tokens", 0)
    hit = usage.get("prompt_cache_hit_tokens", 0)      # 缓存命中的部分
    prompt = usage.get("prompt_tokens", 0)
    stats["cache_hit_tokens"] += hit
    stats["cache_miss_tokens"] += max(0, prompt - hit)
```

命中率 = `cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens)`。这个指标同时回答三个问题：

1. 我的**稳定前缀设计是否有效**（命中率低 → system / tools 前缀在漂移）；
2. 我的**裁剪策略是否破坏了缓存断点**（每次裁剪后命中率骤降 → 裁剪越界了）；
3. **省钱效果到底多少**（命中 90% 意味着输入成本只剩 10%）。

> **重要**：估算与真实值**不要混进同一个计数器**做预算判断——`TokenCounter` 是给「裁剪时机」用的（便宜、快速、前置），usage 是给「统计展示」用的（准确、事后）。两者职责分离，在实战篇的 AgentLoop 中，我们会看到这个 D2 阶段如何落地。

#### 本课小结

1. 理解了 **Prompt 缓存**的原理与收益——最多可节省 **90% 的输入 Token 费用**；
2. 掌握了 **缓存断点（Cache Breakpoint）** 的标注方法，以及**稳定前缀**的设计原则；
3. 在 Anthropic 和 OpenAI 兼容适配器中实际接入了缓存标注；
4. 学会了**缓存感知的 Token 预算管理**，以及**缓存友好**的代码设计约束。

在下一课中，我们将更进一步，学习**Token 节省策略**——从 OpenSquilla 的 13 层 Token 节省机制中提炼最适合我们的 Harness 的模式，并在不破坏缓存的前提下实现多级压缩。
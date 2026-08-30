在上一课中，我们学会了用 **Prompt 缓存** 把稳定前缀的成本砍到 10%。但缓存只能解决"重复"的部分，对于**真正增长的对话历史**，我们还需要主动节省 Token。

本课参考 OpenSquilla（Apache 2.0 的 Python AI Agent 运行时）的 **13 层 Token 节省机制**，提炼出最适合本 Harness 的 5 个模式，并强调一条**核心铁律**：

> **省 Token 不能影响缓存命中**——任何改动都不能破坏缓存断点之前的稳定前缀。

#### 1. 多层级上下文预算治理

OpenSquilla 不是设一个总上限，而是把上下文窗口按功能拆成多个**独立的池子**：

| 池子 | 比例 | 说明 |
|------|------|------|
| 保留（输出 + 思考） | ~25K tokens | 留给模型回复，不参与压缩 |
| 工具参数 | 预算的 16% | 传给工具的指令 |
| 工具结果（本地） | 预算的 50% | shell、文件操作等 |
| 工具结果（外部） | 预算的 25% | web 搜索、HTTP 请求等 |

```python
# context_budget.py
class ContextBudget:
    def __init__(self, max_tokens: int = 48000):
        self.max_tokens = max_tokens

    def tool_result_budget(self, is_external: bool = False) -> int:
        # 外部工具结果噪音更大，给更小的预算
        ratio = 0.25 if is_external else 0.50
        return int(self.max_tokens * ratio)
```

**可借鉴处**：外部工具结果自然比本地工具结果噪音大，应该给更小的预算。这是纯业务判断，API 层不会帮你区分。

#### 2. 四层负载压缩

当请求超预算时，不是直接一刀切，而是从温和到激进**逐步升级**：

```
第1级·温和 — 工具结果截断（行/字节双上限，保留头部）
    ↓ 仍超预算
第2级·收紧 — 压缩推理内容，工具参数换成 SHA256 摘要
    ↓ 仍超预算
第3级·紧急 — 所有角色用更小的窗口（头 180 字符 + 尾 40 字符）
    ↓ 仍超预算
第4级·底线 — 所有内容替换成 96 字符的哈希引用
```

```python
# request_proof.py（简化）
def compress_to_budget(messages, budget):
    for level in range(1, 5):
        candidate = apply_level(messages, level)
        if estimate_tokens(candidate) <= budget:
            return candidate
    return last_resort(messages)
```

**可借鉴处**：大部分情况在 1-2 级解决，极端长对话才到 4 级。每升一级重新验证一次预算。

#### 3. 轮次边界感知压缩（对齐缓存断点）

压缩旧消息时，从预算截断点向后遍历，找到一个**不破坏 `tool_call → tool_result` 配对关系**的"干净边界"。这个边界同时要**位于缓存断点之后**——因为缓存断点之前的稳定前缀不能被压缩。

```
删除区           |  保留区
-----------------|-----------
user: 查文件...    |
assistant: tool_call(id=1)  ← 如果删了这个...
tool_result(id=1)            ← 这个也必须删，否则成"孤儿结果"
assistant: 总结...  |  ← 从这里切才安全（同时也是缓存断点）
```

我们在第三课的 `ContextManager.prune_messages` 中已经实现了 tool 对的原子性。本课在此基础上增加一个约束：**裁剪范围永远从缓存断点之后开始**。

```python
# context_manager.py（增强）
class ContextManager:
    def prune_messages(self, messages, cache_breakpoint_idx=0):
        # 保护 Index 0 (System Prompt) + 缓存断点前的稳定前缀
        removable = messages[cache_breakpoint_idx + 1:]
        # 其余逻辑与第3课一致：tool 对原子性、从最早丢弃...
```

#### 4. 区分性工具结果预算

给每个工具打上预算类别标签，不同类别给不同的参数上限和结果上限：

| 类别 | 例子 | 结果限制 | 特点 |
|------|------|----------|------|
| LOCAL | shell, file, git | 慷慨（50%） | 本地操作，噪音可控 |
| EXTERNAL | web_fetch, web_search | 保守（25%） | 外部噪音多 |
| ERROR | 任何工具的错误输出 | 最低保障线 | **绝不截断** |
| CONTROL | 系统控制工具 | 最低保障线 | 最小化 |

**关键原则：错误信息必须有最低保障线**——LLM 必须看到完整错误才能修。这是第二课"截断"的进阶版。

#### 5. 带外结果存储（Out-of-Band Result Storage）

大型工具输出（如 `cat 大文件`、长日志）**不存入上下文**，而是存到磁盘，上下文中只放一个轻量句柄。需要时通过句柄检索。

这就是我们在第二课截断器中实现的**落盘机制**：

```
上下文中：  "输出已保存: tool_1699999999_ab12cd34.txt (1.2MB)"
磁盘上：    .harness/truncations/tool_1699999999_ab12cd34.txt  ← 完整内容
```

**约束**：单结果最大 8MB，总磁盘预算 256MB，7 天自动清理。

**可借鉴处**：把上下文从"数据容器"变成"数据索引"，开销从 O(内容大小) 降到 O(1)。对于会产生大量输出的场景（日志分析、数据导出）特别有用。

**实现细节（`core/truncator.py`）**：

- **7 天自动清理**：落盘文件名带时间戳 `tool_{int(time.time())}_{uuid4.hex[:8]}.txt`，每次写入前扫描目录、按 mtime 删除超过 `RETENTION_SECONDS = 7 * 24 * 3600` 的旧文件——否则每个大输出 50KB，几百次调用就会占满磁盘：

```python
def _cleanup_expired(output_dir: str) -> None:
    """删除超过保留期的落盘文件，避免无限膨胀。"""
    cutoff = time.time() - RETENTION_SECONDS
    for entry in os.listdir(output_dir):
        if not entry.startswith("tool_"):
            continue
        fp = os.path.join(output_dir, entry)
        try:
            if os.path.getmtime(fp) < cutoff:
                os.remove(fp)
        except OSError:
            pass
```

- **tail 模式**：默认 `direction="head"` 保留开头（命令回显、错误上下文在开头），但脚本化输出（生成代码、报表）用 `direction="tail"` 保留结尾更有用；
- **引导模型的提示文案**：截断内容里明确告诉模型"完整输出已保存到磁盘，用 read_file/search 按需读取，不要主动读完整文件"——把"节省上下文"变成模型可见的行为约束，否则模型可能再次读取完整文件，前面节省的上下文前功尽弃。

#### 6. 省 Token 但不能破坏缓存的"红线"

本课最关键的约束如下，以下操作**会导致缓存 Miss**，必须避免：

- ❌ 在 System Prompt 中插入动态内容（如时间戳、随机 ID）；
- ❌ 每次请求重新排序工具定义列表；
- ❌ 在缓存断点**之前**插入任何新消息；
- ❌ 修改已发送过的 System Prompt 文本（哪怕一个空格）。

**缓存断点之后的操作完全不受限**——对话历史可以任意裁剪、压缩、替换，因为后面是动态区。

```python
# 推荐：把「稳定前缀」与「动态历史」显式分开
def build_request(stable_prefix, history):
    # stable_prefix 只构建一次（可缓存）
    # history 每次裁剪 / 压缩
    return stable_prefix + compress(history)
```

#### 本课小结

1. 掌握了 **OpenSquilla 式多层级预算治理**，把上下文拆成独立池子；
2. 实现了 **四层负载压缩**，从温和到激进逐步降级；
3. 强化了 **轮次边界感知压缩**，在缓存断点之后安全裁剪；
4. 学会了**区分性工具结果预算**（错误输出绝不截断）与**带外结果存储**；
5. 掌握了**省 Token 不破坏缓存**的五条红线原则。

下一课进入 **模块二：代码感知**，学习如何让 Agent 高效感知大型代码库。
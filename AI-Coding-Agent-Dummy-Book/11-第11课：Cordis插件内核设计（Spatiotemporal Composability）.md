在模块二与模块三中，我们为 Harness 编写了 AST 分析、沙箱运行、代码 Patch 和 MCP 扩展等一系列强大的底层能力。

然而，随着系统功能不断增加，如果继续把所有逻辑硬编码在主循环 `run_agent()` 里，代码很快就会变得不可维护。

在 DeepSeek Harness 等工业级 Agent 框架中，采用了类似 **Cordis**（一种现代微内核插件框架）的设计思想：通过**空间解耦（Spatial Decoupling）** 与 **时间解耦 / 轨迹可重放（Temporal Composability / Trajectory Architecture）**，把 Agent 改造为一个完全由插件驱动的自律系统。

#### 1. 什么是"时空解耦"架构？

- **空间解耦 (Spatial Decoupling)**：Agent 的核心（Core）**完全不包含具体的 LLM 厂商逻辑或任何 Tool 逻辑**。Core 只负责维持一个上下文容器、事件管道与上下文状态。所有的 LLM 接入、文件读写、沙箱执行乃至 Prompt 渲染，都是独立的**插件（Plugin）**。
- **时间解耦 (Temporal Composability)**：Agent 运行的所有动作、输入、输出、Tool 调用与环境变量状态，都被抽象为严格的**Append-only 时间序列轨迹（Trajectory Log）**。这使得任何历史时刻的 Agent 状态都可以被**暂停、序列化落盘、分支演化（Fork）或完全重放（Replay）**。

```text
+-------------------------------------------------------------------+
|                        Harness Core Kernel                        |
|                                                                   |
|   +------------------+   EventBus      +---------------------+   |
|   | Context Store    | <-------------> | Lifecycle Hooks     |   |
|   +------------------+                 +---------------------+   |
+-------------------------------------------------------------------+
         ^                         ^                        ^
         |                         |                        |
+--------+--------+       +--------+--------+      +--------+--------+
|   LLM Plugin    |       |   Tools Plugin  |      | Trajectory Log |
| (DeepSeek/OpenAI|       | (Editor/Sandbox)|      |   (Replay/Fork)|
+-----------------+       +-----------------+      +----------------+
```

#### 2. Cordis 风格的插件接口定义

插件的核心思想是：**依赖注入（Context Injection）** 与 **生命周期钩子（Lifecycle Hooks）**。

我们先定义插件在 Context 中可以访问的服务与事件：

```python
# core/types.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class Message:
    role: str                      # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None

# Harness 核心状态上下文
@dataclass
class Context:
    session_id: str
    messages: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    services: Dict[str, Any] = field(default_factory=dict)   # 依赖注入容器

    def register_service(self, name: str, service: Any) -> None:
        self.services[name] = service

    def get_service(self, name: str) -> Any:
        if name not in self.services:
            raise KeyError(f'Service "{name}" not found in Context.')
        return self.services[name]


# 插件生命周期钩子
class Plugin:
    name: str = "plugin"

    def apply(self, ctx: Context) -> None:   # 兼容旧式
        pass

    def install(self, kernel: Any) -> None:  # 兼容 Kernel.use()
        pass
```

#### 3. 手写 Core 插件内核（Kernel）

内核负责维护事件管道、管理插件接入并提供安全钩子拦截（Interceptor Pattern）：

```python
# core/kernel.py
from typing import Any, Dict, Optional

class HarnessKernel:
    def __init__(self, session_id: str = "default"):
        self.ctx = Context(session_id=session_id)
        self.ctx.events = TypedEventBus()          # 事件管道（第12课）
        self.plugins: Dict[str, Plugin] = {}

    def use(self, plugin: Plugin) -> "HarnessKernel":
        if plugin.name in self.plugins:
            print(f'[Kernel]: Plugin "{plugin.name}" already installed.')
            return self
        self.plugins[plugin.name] = plugin
        # 优先 install（可访问 kernel），否则回退 apply（只访问 ctx）
        if hasattr(plugin, "install") and callable(getattr(plugin, "install")):
            plugin.install(self)
        else:
            plugin.apply(self.ctx)
        print(f'[Kernel]: Plugin "{plugin.name}" loaded successfully.')
        return self

    def get_context(self) -> Context:
        return self.ctx
```

#### 4. 可重放轨迹（Trajectory Log）的插件化实现

为了实现"时间解耦"，我们编写一个 **TrajectoryPlugin**。它会无感监听到 Context 内部发生的所有事件（如 `message:added`、`tool:executed`），并将其记录为不可变的时间序列项：

```python
# plugins/trajectory_plugin.py
import json, time
from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class TrajectoryEntry:
    timestamp: int
    type: str              # "MESSAGE" | "TOOL_CALL" | "TOOL_RESULT" | "STATE_CHANGE"
    payload: Any

class TrajectoryPlugin(Plugin):
    name = "trajectory-logger"

    def __init__(self, log_file_path: str = "./trajectory.json"):
        self.trajectory: List[TrajectoryEntry] = []
        self.log_file_path = log_file_path

    def install(self, kernel: Any) -> None:
        events = kernel.ctx.events

        # 监听新消息产生
        @events.on("message:added")
        def _on_message(msg):
            self._record("MESSAGE", msg)

        # 监听 Tool 调用发起
        @events.on("tool:call")
        def _on_tool_call(data):
            self._record("TOOL_CALL", data)

        # 监听 Tool 运行结果
        @events.on("tool:result")
        def _on_tool_result(data):
            self._record("TOOL_RESULT", data)

        # 提供重放（Replay）能力
        kernel.ctx.register_service("trajectory", {
            "get_history": lambda: [e for e in self.trajectory],
            "export_to_file": lambda: self._flush(),
            # 关键能力：传入历史轨迹，反序列化并重现现场
            "replay_to_context": lambda snapshot: self._replay(snapshot),
        })

    def _record(self, etype: str, payload: Any) -> None:
        self.trajectory.append(TrajectoryEntry(
            timestamp=int(time.time() * 1000), type=etype, payload=payload))
        self._flush()

    def _flush(self) -> None:
        with open(self.log_file_path, "w", encoding="utf-8") as f:
            json.dump([e.__dict__ for e in self.trajectory], f,
                      ensure_ascii=False, indent=2)

    def _replay(self, snapshot: List[Dict[str, Any]]) -> None:
        self.trajectory = [TrajectoryEntry(**e) for e in snapshot]
```

#### 5. 组装：使用 Cordis 模式跑通内核

现在，所有的模块（LLM 通信、轨迹记录、工具集成）都变成了可以任意插拔、自由组合的组件：

```python
# main.py
kernel = HarnessKernel("session_001")

# 1. 装载轨迹插件（时间解耦）
kernel.use(TrajectoryPlugin("./logs/session_001.json"))

ctx = kernel.get_context()

# 2. 模拟触发消息事件
ctx.messages.append(Message(role="user", content="修复 bug"))
await ctx.events.emit("message:added", Message(role="user", content="修复 bug"))

await ctx.events.emit("tool:call", {"toolName": "read_file", "args": {"filePath": "src/index.py"}})

# 3. 从 Service 获取轨迹能力
trajectory = ctx.get_service("trajectory")
print("Recorded steps count:", len(trajectory["get_history"]()))
```

### 本课小结

在第九课中，我们掌握了微内核 Harness 的精髓设计：

1. 理解了 **空间解耦（内核只留管道，业务功能全部外包给插件）** 的好处；
2. 实现了基于 **Cordis 依赖注入思想** 的 Context 与 Service 机制；
3. 设计了基于 **Append-only Trajectory 的时间解耦方案**，为调试、测试重放与断点续做打下基础。

下一次我们将进入 **第12课：依赖注入与插件生命周期管理** —— 深入探讨 Interceptor 拦截器管道（中间件模式），学习如何通过插件在 Tool 调用前后实现无感修改 Prompt 和安全审查拦截！
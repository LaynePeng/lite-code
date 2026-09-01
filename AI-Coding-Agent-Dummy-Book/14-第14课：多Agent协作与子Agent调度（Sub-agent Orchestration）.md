在前面的课程中，我们构建的 Harness 都是围绕"单 Agent 循环"展开的。但在面对大型工程任务（例如"给整个项目编写单元测试"、"重构所有模块的 API"或"全盘定位安全漏洞"）时，单 Agent 会暴露出极大的瓶颈：

1. **上下文爆满**：在一个对话中排查 10 个文件，上下文 Token 会迅速触顶并引发性能下降；
2. **任务注意力分散**：Agent 在执行复杂多步骤时容易"走神"，遗忘最初的目标；
3. **单线程效率低下**：独立子任务（如搜索不同子模块的代码）无法并行化执行。

本课我们将实现 **Sub-agent Orchestration（子 Agent 编排架构）**：允许主 Harness 根据需要动态 **派生（Spawn）** 隔离的子 Harness 去完成独立任务，并将结果与 Token 消耗精准归集到主进程中。

#### 1. 编排架构设计

在子 Agent 模式中，主 Agent 扮演"项目经理（Orchestrator）"，而子 Agent 是"专家工人（Worker）"。

```text
                        +----------------------------+
                        |     Main Agent Harness     |
                        |   (Orchestrator Context)   |
                        +--------------+-------------+
                                       |
                       spawns via tool | task delegation
                                       v
         +-----------------------------+-----------------------------+
         |                                                           |
         v                                                           v
+------------------------+                                  +------------------------+
|   Sub-Agent: Explorer  |                                  |   Sub-Agent: Tester    |
| (Isolated Context)     |                                  | (Isolated Context)     |
+-----------+------------+                                  +-----------+------------+
            |                                                           |
            +----------------------------+------------------------------+
                                         v
                         Returns Concise Summary Result
                          + Merged Token Usage Stats
```

**子 Harness 的核心隔离原则**：

- **上下文隔离**：子 Agent 拥有独立的 `messages` 数组，主 Agent 的杂乱历史不会污染它；
- **权限与工具裁剪**：子 Agent 可以只配备特定工具（如"只读搜索"子 Agent 不赋予写文件与 Shell 权限）；
- **结果压缩**：子 Agent 运行几十轮后的最终产出，会被压缩为一段精简的文本报告返还给主 Agent。

#### 2. 实现 Sub-Agent 启动器

我们编写一个 `SubAgentRunner` 模块，负责创建子 Harness 实例并驱动其运行（对应 `litecode/orchestration/sub_agent.py` 的真实实现）：

```python
# orchestration/sub_agent.py
import uuid
from typing import Dict, List, Optional

# 角色 → 预置 Prompt 与工具集
ROLE_PROMPTS = {
    "explorer": "你是一名只读调研员。只能查看代码与搜索，禁止修改文件或执行破坏性命令。",
    "tester": "你是一名测试执行员。负责运行测试并分析结果。",
    "refactor": "你是一名重构工程师。拥有完整工具集，负责完成指定的重构任务并验证。",
    "general": "你是一名专注的专家工人，聚焦你的任务并返回简洁总结。",
}

# explorer 角色只授予只读工具
ROLE_TOOLS: Dict[str, Optional[List[str]]] = {
    "explorer": ["read_file", "list_dir", "file_tree", "search_code",
                 "get_file_outline", "read_focused_symbol",
                 "git_status", "git_diff", "git_log", "git_branch",
                 "review_code", "webfetch", "webfetch_batch"],
    "tester": ["read_file", "list_dir", "file_tree", "search_code",
               "execute_command", "git_status", "git_diff", "git_log"],
    "refactor": None,   # 全部工具
    "general": None,
}

class SubAgentRunner:
    """动态派生独立的子 Harness 运行任务，结果压缩后归集到父级。"""

    def __init__(self, app):
        self.app = app   # 持有 AgentApp（共享 LLM 注册表、安全组件等）

    async def run_task(self, task_description: str, role: str = "general",
                       system_prompt: Optional[str] = None, max_steps: int = 12) -> Dict:
        # 1. 创建全新的子 Kernel 实例（隔离的 Context）
        sub_kernel = Kernel(session_id=f"sub_{uuid.uuid4().hex[:8]}")

        # 2. 挂载安全插件（复用父级 SecurityGuard）
        sub_kernel.use(SecurityPlugin(self.app.guard, self.app.approval_gate))

        # 3. 按角色裁剪工具集（explorer 屏蔽写文件与 Shell）
        allowed = ROLE_TOOLS.get(role)
        registry = self.app.build_registry(allowed=allowed, exclude=["spawn_sub_agent"])

        # 4. 构建子 Agent 专用 System Prompt
        base_prompt = system_prompt or ROLE_PROMPTS.get(role, ROLE_PROMPTS["general"])
        system = (f"{base_prompt}\n\n[你的具体任务]\n{task_description}\n\n"
                  f"工作目录: {self.app.workspace}")

        # 5. 用独立 Kernel 运行 AgentLoop（不落盘）
        loop = AgentLoop(kernel=sub_kernel, adapter=self.app.adapter,
                         registry=registry, session_store=None, max_steps=max_steps)
        summary, stats = await loop.run_task(
            f"请完成以下子任务并输出精炼总结：\n{task_description}",
            system_prompt=system, store_snapshot=False)

        # 6. 将子 Agent 的 Token 归集到父级事件
        await sub_kernel.events.emit("subagent:completed", {
            "task": task_description, "role": role,
            "tokens_used": stats["input_tokens"] + stats["output_tokens"],
            "turns": stats["turns"], "summary": summary,
        })

        return {
            "summary": summary,
            "total_tokens_used": stats["input_tokens"] + stats["output_tokens"],
            "completed": stats["status"] == "SUCCESS",
            "role": role,
        }
```

#### 3. 将 `spawn_sub_agent` 封装为父 Agent 的可调用 Tool

现在我们把派生子 Agent 的能力暴露为父 Agent 的一个 Tool，让 LLM 可以在运行期自主决定什么时候"分工协作"：

```python
# tools/orchestration_tools.py
async def spawn_sub_agent_handler(app, args: dict) -> str:
    task = args.get("taskDescription", "").strip()
    if not task:
        return "[Error]: taskDescription 不能为空。"
    role = args.get("roleType") or "general"
    if role not in ("explorer", "tester", "refactor", "general"):
        role = "general"

    result = await app.sub_agent_runner.run_task(task, role=role)
    return (f"[Sub-Agent 执行报告]\n"
            f"状态: {'SUCCESS' if result['completed'] else 'FAILED'}\n"
            f"角色: {result['role']}\n"
            f"Token 消耗: {result['total_tokens_used']}\n"
            f"报告:\n{result['summary']}")
```

#### 4. 在 Harness 中验证并行编排与结果汇总

通过并发 `asyncio.gather`，主 Agent 甚至可以同时发起多个子 Agent 任务：

```python
# main.py
async def main():
    runner = SubAgentRunner(parent_app)

    # 监听父内核归集事件（闭包内不能对 int 重新赋值，用列表收集）
    token_log: List[int] = []
    parent_kernel.events.on("subagent:completed",
                            lambda d: token_log.append(d["tokens_used"]))

    print("=== Demonstrating Parallel Sub-Agents ===")

    # 并行派生两个子 Agent，分别探索 API 模块与 Database 模块
    task1 = runner.run_task("Inspect API routing endpoints in src/api/", role="explorer")
    task2 = runner.run_task("Check database connection pool config in src/db/", role="explorer")

    results = await asyncio.gather(task1, task2)

    print("\n=== Final Aggregated Results ===")
    for idx, res in enumerate(results):
        print(f"\nSubAgent #{idx+1} Summary:\n{res['summary']}")
```

### 本课小结

在本课中，我们掌握了复杂 Agent 架构设计的高级模式：

1. 理解了 **Sub-agent Orchestration（子 Agent 编排）** 在处理大工程任务时的优势与上下文隔离原则；
2. 实现了基于 **内核派生的 SubAgentRunner** 运行逻辑；
3. 实现了 **工具集动态裁剪（Role-based Capability Control）** 与 **Token/事件归集管道**；
4. 将派生能力封装为 `spawn_sub_agent` Tool，使 Agent 具备了"自我分发与并行化工作"的能力。

至此，**模块三：核心架构** 的前半部分（微内核与子 Agent 编排）已全部完结！

下一步我们将开启 **第15课：Agent 类型与自定义机制（Build/Plan 与用户扩展）** —— 参考 OpenCode，为 Harness 内置 Build/Plan 两种默认 Agent，并开放用户自定义 Agent 的配置通道！
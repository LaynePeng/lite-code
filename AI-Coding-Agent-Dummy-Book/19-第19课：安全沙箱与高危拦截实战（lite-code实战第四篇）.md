在前面的课程中，我们为 `lite-code` 实现了强类型的 Core 内核、核心 API/文件/Shell 插件以及驱动 ReAct 闭环的 `AgentLoop`。

但作为一个运行在真实宿主环境或开发机上的自主 Agent，如果缺乏严密的**安全沙箱与高危指令拦截机制**，诸如 `rm -rf /`、删库指令、敏感文件读取或越权网络请求等误操作与注入攻击（Prompt Injection）将带来毁灭性后果。

本课我们将为 `lite-code` 打造安全防御层：手写 **AST/正则表达式高危命令拦截器**、实现 **动态黑白名单与确认机制（User Authorization Strategy）**，并将其无缝接入 Kernel 的 `beforeTool` 钩子中。

#### 1. 沙箱与安全拦截架构

在 `lite-code` 中，安全防护采用**多重防御体系（Defense-in-Depth）**：

```text
                             Tool Call Request
                                     |
                                     v
                  +-----------------------------------+
                  |   Kernel.beforeTool Interceptor   |
                  +------------------+----------------+
                                     |
                                     v
                  +-----------------------------------+
                  |  Layer 1: Path Traversal Check    |
                  +------------------+----------------+
                                     | Passes
                                     v
                  +-----------------------------------+
                  |  Layer 2: Dangerous AST/Regex     |
                  +------------------+----------------+
                                     | Threat Level?
               +---------------------+---------------------+
               | High                | Medium              | Safe
               v                     v                     v
     +-------------------+ +--------------------+ +------------------+
     | Block & Return    | | Ask Human via Web   | | Allow Execution  |
     | Error to Agent    | | (审批卡确认)         | | Directly         |
     +-------------------+ +--------------------+ +------------------+
```

#### 2. 手写命令安全检查器 (`litecode/security/guard.py`)

我们定义规则引擎，识别高危 Shell 命令与敏感文件路径：

```python
# litecode/security/guard.py
import re
from enum import Enum
from typing import Any, Dict, List

class ThreatLevel(str, Enum):
    SAFE = "SAFE"
    MEDIUM = "MEDIUM"   # 需要用户手动确认
    HIGH = "HIGH"       # 直接拒绝

class SecurityCheckResult:
    def __init__(self, level: ThreatLevel, reason: str = ""):
        self.level = level
        self.reason = reason

# 禁止访问的敏感系统路径
DEFAULT_FORBIDDEN_PATHS = [
    "/etc/shadow", "/etc/passwd", "/etc/sudoers",
    "~/.ssh", "~/.bashrc", "~/.zshrc", "~/.aws",
    ".env", "id_rsa", "id_ed25519",
]

# 严格拦截的高危 Shell 命令模式 (High Threat)
DEFAULT_HIGH_RISK_PATTERNS = [
    r"\brm\s+-[rRfF]+\s+[/\*]",      # rm -rf / 或 rm -rf *
    r">\s*/dev/sd[a-z]",             # 覆盖块设备
    r"\bmkfs\b",                     # 格式化磁盘
    r"\bdd\b.*\bof=/dev/",           # 写入物理设备
    r":\(\)\{\s*:\|\:&\s*\};:",      # Fork 炸弹
    r"git\s+push\s+.*--force",       # 强制推送
    r"git\s+reset\s+--hard",         # 硬重置
]

# 中风险命令，需触发人工二次确认 (Medium Threat)
DEFAULT_MEDIUM_RISK_PATTERNS = [
    r"\brm\b",                       # 普通删除操作
    r"\bkill\s+-9\b",                # 强制终止进程
    r"\bsudo\b",                     # 提权操作
    r"\bgit\s+push\b",
]

# 动态白名单：精确前缀放行（覆盖默认检查）
DEFAULT_WHITELIST = [
    "git status", "git log", "git diff", "git branch",
    "ls", "pwd", "whoami", "echo", "cat ", "head ", "tail ",
    "npm run test", "pytest", "python3 -m pytest", "rg ", "tree ",
    "cd ", "mkdir ", "touch ", "cp ",
]

class SecurityGuard:
    """动态黑白名单规则引擎，支持热加载。"""

    def __init__(self, config: Dict[str, Any] = None):
        self.forbidden_paths = list(DEFAULT_FORBIDDEN_PATHS)
        self.high_risk_patterns = list(DEFAULT_HIGH_RISK_PATTERNS)
        self.medium_risk_patterns = list(DEFAULT_MEDIUM_RISK_PATTERNS)
        self.whitelist = list(DEFAULT_WHITELIST)
        self._compiled_high = self._compile(self.high_risk_patterns)
        self._compiled_medium = self._compile(self.medium_risk_patterns)
        if config:
            self.apply_config(config)

    def apply_config(self, config: Dict[str, Any]) -> None:
        """动态更新黑白名单（.lite-code/config.json），无需重启。"""
        if "forbidden_paths" in config: self.forbidden_paths = list(config["forbidden_paths"])
        if "high_risk_patterns" in config: self.high_risk_patterns = list(config["high_risk_patterns"])
        if "medium_risk_patterns" in config: self.medium_risk_patterns = list(config["medium_risk_patterns"])
        if "whitelist" in config: self.whitelist = list(config["whitelist"])
        self._compiled_high = self._compile(self.high_risk_patterns)
        self._compiled_medium = self._compile(self.medium_risk_patterns)

    @staticmethod
    def _compile(patterns: List[str]) -> List[re.Pattern]:
        out = []
        for p in patterns:
            try:
                out.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue
        return out

    def check_path(self, file_path: str) -> SecurityCheckResult:
        """检查文件路径是否合规（敏感路径拦截）。"""
        normalized = file_path.replace("\\", "/").lower()
        for forbidden in self.forbidden_paths:
            if forbidden.lower() in normalized:
                return SecurityCheckResult(ThreatLevel.HIGH,
                    f'访问敏感路径 "{forbidden}" 被严格禁止！')
        return SecurityCheckResult(ThreatLevel.SAFE)

    def check_shell_command(self, command: str) -> SecurityCheckResult:
        """检查 Shell 指令的安全风险等级。"""
        stripped = command.strip()
        # 1. 高危黑名单永远最先检查——硬边界，白名单不可绕过
        #    （否则 `git push --force` 会因命中白名单 `git push` 而漏过拦截）
        for pattern in self._compiled_high:
            if pattern.search(command):
                return SecurityCheckResult(ThreatLevel.HIGH,
                    f'命令命中高危黑名单规则: /{pattern.pattern}/')
        # 2. 白名单精确前缀放行——仅对"简单命令"生效
        if not _COMPOUND_HINT_RE.search(command):   # 含 && ; | ` $() > 等即为复合命令
            for prefix in self.whitelist:
                if stripped.startswith(prefix):
                    return SecurityCheckResult(ThreatLevel.SAFE)
        # 3. 中危模式（提权 / 破坏性操作）→ 人工确认
        #    正则全文搜索：复合命令中的每个子命令都会被覆盖
        for pattern in self._compiled_medium:
            if pattern.search(command):
                return SecurityCheckResult(ThreatLevel.MEDIUM,
                    f'命令需要人工确认: {command[:200]}')
        return SecurityCheckResult(ThreatLevel.SAFE)
```

**真实踩坑：白名单前缀绕过（v0.10.0 修复）**。初版实现把白名单检查放在了**最前面**且无条件生效，结果出现了两个绕过通道：

1. **复合命令拼接**：用户要求删除文件，Agent 生成了 `cd /Users/xx/Test && rm hello.py && echo "已删除"`——命令以白名单前缀 `cd ` 开头，直接 SAFE 放行，后面的 `rm` 根本没被正则审查。更糟的是 Agent 事后向用户解释"删除单文件不算高危所以没触发"，完全没意识到这是绕过漏洞——**安全缺陷的可怕之处在于它对使用者也透明**；
2. **优先级倒置**：白名单 `git push` 在高危检查之前放行，`git push --force` 因此漏过 HIGH 拦截。

修复确立了三条铁律：**①高危黑名单永远最先检查**（硬边界，任何机制不能前置）；**②白名单只对简单命令生效**——含 `&&`/`;`/`|`/反引号/`$()`/重定向的复合命令必须整条走正则（正则全文搜索天然覆盖每个子命令）；**③宁可误判不可漏判**——`echo "a && b"` 这类含引号字面量的命令会被误判为复合、多走一遍正则，结果仍是 SAFE，代价为零；反过来漏判的代价是文件被删。这与第 22 课"MCP 工具一刀切审批"是同一个思想：**不确定的风险按最坏假设处理**。

**增强点（相比第 8 课的命令级过滤）**：
1. **动态黑白名单**：规则通过 `.lite-code/config.json` 热加载，改配置即生效，无需改代码；
2. **白名单机制**：精确前缀放行仅覆盖简单命令的默认检查（如 `git status`、`pytest` 直接放行），复合命令强制全量审查；
3. **非法正则忽略**：编译失败的规则自动跳过，不会导致整体崩溃。

#### 3. 手写 Human-in-the-Loop 审批门 (`litecode/security/approval.py`)

在遇到 `MEDIUM` 风险级别的操作时，我们需要暂停 Agent 自动流转，向操作员发起交互式确认。

第 8 课的控制台确认适合单机演示；实战中我们需要**Web 审批卡**：用 `asyncio.Future` 挂起 AgentLoop，通过 SSE 通知 UI 弹出审批卡，用户点击后在 `POST /api/approve` 中 resolve Future：

```python
# litecode/security/approval.py
import asyncio, itertools, logging, time
from typing import Any, Dict

class ApprovalGate:
    """人机交互审批门：asyncio.Future 挂起等待 Web UI 的人工确认。"""

    def __init__(self, timeout_seconds: float = 600.0):
        self.timeout_seconds = timeout_seconds
        self._ids = itertools.count(1)
        self._pending: Dict[str, Dict[str, Any]] = {}

    def request_approval(self, action: str, risk_reason: str,
                         auto_approve: bool = False) -> asyncio.Future:
        approval_id = f"apv_{next(self._ids)}"
        future = asyncio.get_event_loop().create_future()

        if auto_approve:
            future.set_result(True)
            return future

        self._pending[approval_id] = {
            "id": approval_id, "action": action, "reason": risk_reason,
            "created_at": int(time.time() * 1000), "future": future,
        }

        # 超时保护：超过时限未确认，自动拒绝
        async def _timeout_guard():
            await asyncio.sleep(self.timeout_seconds)
            if not future.done():
                self.resolve(approval_id, approved=False, by="timeout")
        asyncio.ensure_future(_timeout_guard())
        return future

    def current_id(self, future: asyncio.Future) -> str:
        """根据 future 反查审批 ID（用于事件广播）。"""
        for aid, entry in self._pending.items():
            if entry["future"] is future:
                return aid
        return ""

    def resolve(self, approval_id: str, approved: bool, by: str = "user") -> bool:
        entry = self._pending.pop(approval_id, None)
        if entry is None:
            return False
        if not entry["future"].done():
            entry["future"].set_result(approved)
        return True
```

#### 4. 在 Kernel 中注册安全中间件 (`litecode/security/plugin.py`)

我们将 `SecurityGuard` 与 `ApprovalGate` 作为内核插件，挂载到 `beforeTool` Pipeline 中：

```python
# litecode/security/plugin.py
class SecurityPlugin(Plugin):
    name = "security-plugin"

    def __init__(self, guard: SecurityGuard, approval_gate: ApprovalGate):
        self.guard = guard
        self.approval_gate = approval_gate

    def install(self, kernel: Kernel) -> None:
        @kernel.before_tool.use
        async def _middleware(ctx, data, next):
            tool_name = data.get("toolName", "")
            args = data.get("args", {}) or {}

            # A. 路径型工具过滤（read_file / write_file / apply_search_replace 等）
            path_result = self.guard.check_tool(tool_name, args)
            if path_result.level == ThreatLevel.HIGH:
                data["cancel"] = True
                data["reason"] = f"[SecurityGuard]: {path_result.reason}"
                return await next(data)

            # B. Shell 工具过滤
            if tool_name == "execute_command":
                command = args.get("command", "")
                result = self.guard.check_shell_command(command)

                # 高风险：直接拒绝
                if result.level == ThreatLevel.HIGH:
                    data["cancel"] = True
                    data["reason"] = f"[Blocked by SecurityGuard]: {result.reason}"
                    return await next(data)

                # 中风险：挂起并请求 Web 审批
                if result.level == ThreatLevel.MEDIUM:
                    approved = await self._request_approval(
                        kernel, f'execute_command("{command}")',
                        result.reason or "中危操作")
                    if not approved:
                        data["cancel"] = True
                        data["reason"] = "[User Rejected]: 操作被操作员明确拒绝。"
                        return await next(data)

            # C. MCP 工具：外部进程提供，行为不可预知 → 默认全部审批
            if tool_name.startswith("mcp_"):
                approved = await self._request_approval(
                    kernel,
                    f"调用 MCP 工具 {tool_name}",
                    "MCP 工具由外部进程提供，可能访问文件、网络或其他本地资源。",
                )
                if not approved:
                    data["cancel"] = True
                    data["reason"] = "[User Rejected]: MCP 工具调用已被拒绝。"
                    return await next(data)

            return await next(data)

        kernel.register_service("security_guard", self.guard)

    async def _request_approval(self, kernel, action, reason) -> bool:
        future = self.approval_gate.request_approval(action, reason)
        approval_id = self.approval_gate.current_id(future)
        # 广播审批请求 → SSE → Web UI 弹出确认卡片
        await kernel.events.emit("approval:request",
                                 {"id": approval_id, "action": action, "reason": reason})
        approved = await future
        # 广播审批结果 → UI 关闭确认卡片
        await kernel.events.emit("approval:resolved",
                                 {"id": approval_id, "approved": approved})
        return approved
```

#### 5. 实战验证

`AgentLoop` 在工具执行前调用 `before_tool.run()`，安全插件据此拦截或放行：

```python
# AgentLoop 中工具执行前的安全校验
hook_data = {"toolName": call.name, "args": args, "cancel": False, "reason": ""}
verified = await self.kernel.before_tool.run(self.kernel.ctx, hook_data)

if verified.get("cancel"):
    result = f"[Tool Execution Cancelled]: {verified.get('reason')}"
else:
    raw = await asyncio.wait_for(self.registry.execute(call.name, args),
                                 timeout=self.tool_timeout)
    result = truncate_tool_output(raw)
```

运行链路验证：
- 高危 `rm -rf /` → `HIGH` 直接拒绝（LLM 通常也会自我拒绝）；
- 中危 `rm temp.txt` → `MEDIUM` 弹出审批卡 → 用户点"允许"才执行，点"拒绝"则返回取消消息给 Agent；
- 白名单命令（`git status`、`pytest`）→ `SAFE` 直接放行；
- MCP 工具 `mcp_sqlite_query` → 无论参数是什么，一律弹出审批卡。

**为什么 MCP 工具要"一刀切"走审批？** 内置工具的行为是我们审计过的代码——`read_file` 的路径检查、`execute_command` 的黑白名单都可控；而 MCP Server 是**任意外部进程**（第 11 课），它的 `query` 工具完全可能在背后读写文件系统、发起网络请求。我们无法为未知工具编写规则，只能按**最坏假设**处理：不可审计的能力必须经过人。注意这恰恰是洋葱模型的优雅之处——MCP 工具在 AgentLoop 与注册表层与内置工具完全同权，但在安全层被单独识别并降权，**接入零成本，调用有门槛**。

### 本课小结

在本课中，我们构建了 `lite-code` 的沙箱防护系统：

1. 建立了三级风险控制模型（`SAFE` / `MEDIUM` / `HIGH`）；
2. 实现了基于正则与敏感词的 **`SecurityGuard` 规则引擎**，阻断路径穿越与恶性指令；
3. **动态黑白名单**：规则可从 `.lite-code/config.json` 热加载，无需改代码；
4. 实现了 **`ApprovalGate` (Human-in-the-Loop)** Web 化机制，关键操作让用户保持控制权（`asyncio.Future` 挂起 + SSE 审批卡）；
5. 将安全机制封装为 **`SecurityPlugin`**，展示了 `beforeTool` 洋葱机制在防护层的优雅应用；
6. **MCP 工具默认审批**：外部进程提供的能力按最坏假设处理，不可审计的调用必须经过人。

下一次我们将开启 **第20课：Web UI 实战 (`lite-code` 实战第五篇)** —— 为 `lite-code` 构建FastAPI 服务层与带流式 Markdown、工具卡片、审批卡的现代化 React Web UI！
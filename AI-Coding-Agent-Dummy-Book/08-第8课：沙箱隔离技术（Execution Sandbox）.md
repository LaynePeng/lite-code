在完成了代码感知与 AST 检索后，Agent 必然需要**执行代码、运行测试套件或执行 Shell 命令**。

但是直接在宿主机（Host Machine）上运行 Agent 生成的 Shell 指令是极度危险的。LLM 可能会产生以下风险：

1. **破坏性指令**：误执行 `rm -rf /` 或改写关键系统环境变量；
2. **资源耗尽**：运行了死循环代码（如 `while True`）导致宿主机 CPU / 内存爆满；
3. **网络与数据外泄**：恶意代码尝试连接外部未知 Server 上传敏感环境变量（`.env`）。

本课我们将手写一个基于 **进程隔离** 的安全沙箱控制器（Execution Sandbox），为 Agent Harness 提供安全的"轻量隔离圈"。

#### 1. 沙箱隔离的核心设计要求

一个合格的 Code Agent 沙箱必须具备 4 层防线：
- **文件系统隔离**：代码的读写必须限定在工作区目录内部，宿主机的 `/etc`、`~/.ssh` 等不可见；
- **资源限制 (Resource Limit)**：限制输出大小，超时中断；
- **超时中断 (Timeout Guarantee)**：任意 Shell 命令超过预定时间强行杀掉进程；
- **环境变量隔离**：API Key 等敏感信息不透传给子进程。

#### 2. 基于 asyncio 的受限本地进程沙箱

在开发机器上，最实用的方案是 **本地进程沙箱（Local Process Sandbox）**。通过 asyncio 子进程 + 超时 + 环境变量擦除来防范常规误操作：

```python
# sandbox/local_sandbox.py
import asyncio
import os
import re
from typing import Optional

# 最终防线：高危命令粗暴过滤
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+[/\~]",
    r"mkfs",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r":\(\)\{\s*:\|\:&\s*\};:",  # Fork 炸弹
]

# 敏感环境变量列表
SENSITIVE_ENV_VARS = [
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "GITHUB_TOKEN",
    "DATABASE_URL", "MYSQL_PWD", "PGPASSWORD",
]

class LocalProcessSandbox:
    """基于 asyncio 子进程的受限 Shell 执行器，超时自动终止，敏感环境变量已擦除。"""

    def __init__(self, workspace_dir: str, timeout_seconds: float = 60.0,
                 max_output: int = 200_000):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.timeout_seconds = timeout_seconds
        self.max_output = max_output

    async def exec_command(self, command: str) -> dict:
        # 高危命令过滤
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return {"stdout": "", "stderr": f"[Blocked]: 高危模式 /{pattern}/",
                        "exit_code": 1, "timed_out": False}

        # 剥离敏感环境变量
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in SENSITIVE_ENV_VARS}

        try:
            proc = await asyncio.create_subprocess_shell(
                command, cwd=self.workspace_dir, env=clean_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            return {"stdout": out, "stderr": err,
                    "exit_code": proc.returncode or 0, "timed_out": False}
        except asyncio.TimeoutError:
            proc.kill()
            return {"stdout": "", "stderr": f"[Timeout] 超过 {self.timeout_seconds}s",
                    "exit_code": 124, "timed_out": True}
```

#### 3. 轻量级 Docker 容器沙箱（可选）

对需要更高隔离级别的场景，可以包装 Docker CLI（Python 的 `asyncio.create_subprocess_exec` 调用 `docker run`）：

```python
# sandbox/docker_sandbox.py
import asyncio, os, path, uuid

class DockerSandbox:
    """基于 Docker CLI 的容器沙箱，支持内存/CPU/网络限制。"""

    def __init__(self, workspace_dir: str, image: str = "python:3.11-slim",
                 allow_network: bool = False, memory: str = "1g", cpus: str = "2.0"):
        self.container_name = f"lc_sandbox_{uuid.uuid4().hex[:8]}"
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.image = image
        self.allow_network = allow_network
        self.memory = memory
        self.cpus = cpus
        self.is_running = False

    async def start(self):
        network = "" if self.allow_network else "--network none"
        cmd = (f"docker run -d --name {self.container_name} {network} "
               f"-v \"{self.workspace_dir}:/workspace\" -w /workspace "
               f"--memory={self.memory} --cpus={self.cpus} --pids-limit=100 "
               f"{self.image} tail -f /dev/null")
        proc = await asyncio.create_subprocess_shell(cmd)
        await proc.wait()
        self.is_running = True

    async def exec_command(self, command: str) -> dict:
        proc = await asyncio.create_subprocess_shell(
            f"docker exec {self.container_name} sh -c {shlex.quote(command)}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"stdout": stdout.decode(), "stderr": stderr.decode(),
                "exit_code": proc.returncode or 0, "timed_out": False}

    async def stop(self):
        await asyncio.create_subprocess_shell(f"docker rm -f {self.container_name}")
        self.is_running = False
```

#### 4. 将沙箱 Shell 工具接入 Harness Tool Protocol

```python
# tools/sandbox_tools.py
sandbox_tools = [
    ToolDefinition(
        name="execute_command",
        description="在受限沙箱环境中执行 Shell 命令（如 npm test, git status, pytest 等）",
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的终端命令"},
        }, "required": ["command"]},
    ),
]

async def execute_sandbox_tool(name: str, args: dict, sandbox: LocalProcessSandbox) -> str:
    if name != "execute_command":
        raise ValueError(f"Unknown Sandbox Tool: {name}")
    res = await sandbox.exec_command(args["command"])
    out = f"[Exit Code]: {res['exit_code']}\n"
    if res['timed_out']: out += "[Timed Out]\n"
    if res['stdout'].strip(): out += f"[STDOUT]:\n{res['stdout'].strip()}\n"
    if res['stderr'].strip(): out += f"[STDERR]:\n{res['stderr'].strip()}\n"
    return out or "[No output]"
```

### 本课小结

在第六课中，我们为 Harness 构建了安全底座：

1. 实现了基于 **asyncio 子进程的受限 Shell 执行器**（超时自动终止 + 敏感环境变量擦除）；
2. 提供了可选的 **Docker 容器沙箱**（内存/CPU/网络限制）；
3. 增加了 **硬超时摧毁机制** 与 **高危命令过滤**；
4. 屏蔽了底层 API Key 环境变量在 Shell 进程中的泄漏风险。

下一次我们将进入 **第9课：精确代码编辑与 Apply Patch 机制** —— 学习解决 LLM 频繁写错代码行号的问题，手写高效且具备重试能力的代码编辑与 Patch 校验工具！
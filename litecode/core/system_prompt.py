"""静态 System Prompt 骨架（对应课程第3课 DynamicSystemPromptBuilder）。

System 只含任务内恒定内容（角色 / 环境 / 工具摘要 / 规则），保证缓存前缀稳定；
git 状态等会随时间变化的信息由 git_status 工具按需获取。
"""
from __future__ import annotations

import platform
import subprocess
from typing import List

from .types import ToolDefinition


class SystemPromptBuilder:
    """静态 System Prompt 组装器：同一任务内所有 LLM 调用共享同一份 system 内容。"""

    @staticmethod
    def _git_info(cwd: str) -> str:
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=3,
            )
            branch_name = branch.stdout.strip() if branch.returncode == 0 else "N/A"
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=3,
            )
            changed = len([l for l in status.stdout.splitlines() if l.strip()])
            return f"分支: {branch_name} | 未提交改动文件数: {changed}"
        except Exception:
            return "不是 Git 仓库 / Git 不可用"

    @classmethod
    def build(cls, cwd: str, tools: List[ToolDefinition]) -> str:
        os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
        tools_summary = "\n".join(f"- **{t.name}**: {t.description}" for t in tools)

        return f"""你是一个专业的 AI 软件工程师 Code Agent，运行在用户本地的开发环境中。

### 环境信息 (Environment Context)
- **操作系统**: {os_name}
- **当前工作目录**: `{cwd}`

### 可用工具 (Available Tools)
{tools_summary}

### 工作规则 (Operating Rules)
1. 修改代码前，先用工具探查代码库结构与相关文件内容，不要盲目猜测；
2. 修改文件使用 apply_search_replace / apply_unified_diff 等精确编辑工具，
   避免整文件重写；编辑前先 read_file 获取精确上下文；
3. 执行命令优先使用 execute_command；命令失败时分析错误输出并换一种策略，
   不要连续用完全相同参数重试同一个失败的工具；
4. 涉及删除、强制推送、sudo 提权等操作时，系统会要求用户确认；
5. 用简洁的 Markdown 回复用户；中文优先；
6. 涉及多个独立模块、需要广泛搜索或可并行调研时，优先使用 spawn_sub_agent 的 explorer 角色；
7. 完成任务后给出清晰的总结（改了什么、如何验证）。
"""

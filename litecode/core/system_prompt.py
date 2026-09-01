"""静态 System Prompt 骨架（对应课程第3课 DynamicSystemPromptBuilder）。

System 只含任务内恒定内容（角色 / 环境 / 工具摘要 / 规则），保证缓存前缀稳定；
git 状态等会随时间变化的信息由 git_status 工具按需获取。
"""
from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import List

from .types import ToolDefinition


# This instruction is intentionally explicit and duplicated in the numbered rules:
# agents must always leave the user with a completion report, not just stop after tools.
FINAL_REPORT_REQUIREMENT = """### 强制交付要求（必须遵守）
任务结束前必须向用户汇报结果，不能只执行工具后停止，也不能只回复“已完成”。最终回复必须使用简洁的 Markdown，至少包含：
- **完成内容**：实际完成了什么；
- **改动文件/关键结果**：涉及哪些文件或得到什么结论（如适用）；
- **验证情况**：运行了哪些测试、构建或检查，以及结果；
- **未完成事项**：仍有问题、风险或未验证内容必须明确说明，没有则写“无”。
如果任务因失败、停止、超时或达到步骤上限而结束，也必须向用户说明当前状态、原因和后续建议。"""


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

    @staticmethod
    def _project_instructions(cwd: str) -> str:
        """读取 workspace 根目录的项目指令文件。"""
        sections = []
        root = Path(cwd).resolve()
        for filename in ("AGENTS.md", "Claude.md", "CLAUDE.md"):
            path = root / filename
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if content:
                sections.append(f"### {filename}\n{content}")
        return "\n\n".join(sections)

    @classmethod
    def build(cls, cwd: str, tools: List[ToolDefinition]) -> str:
        os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
        tools_summary = "\n".join(f"- **{t.name}**: {t.description}" for t in tools)
        project_instructions = cls._project_instructions(cwd)
        skill_index = ""
        try:
            from ..tools.skills import SkillsTools
            skill_index = SkillsTools(cwd).index()
        except Exception:
            skill_index = "（技能索引不可用）"
        instruction_section = (
            "\n\n### 项目指令 (Project Instructions)\n"
            "以下内容来自 workspace 中的项目指令文件，请在不违反系统安全规则的前提下遵守：\n"
            f"{project_instructions}"
            if project_instructions else ""
        )
        skill_section = (
            "\n\n### 可用技能 (Skills)\n"
            "需要专项流程时，使用 `load_skill` 按名称加载完整 SKILL.md；不要猜测技能内容。\n"
            f"{skill_index}"
        )

        return FINAL_REPORT_REQUIREMENT + "\n\n" + f"""你是一个专业的 AI 软件工程师 Code Agent，运行在用户本地的开发环境中。

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
7. **完成工作后必须向用户提交完整汇报**：说明完成内容、改动文件/关键结果、验证情况和未完成事项；绝不能在工具调用后无回复结束。{instruction_section}{skill_section}

"""

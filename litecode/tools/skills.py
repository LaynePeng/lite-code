"""项目与用户技能发现、按需加载工具。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from ..core.types import ToolDefinition


class SkillsTools:
    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).resolve()
        self.roots = [
            self.workspace / ".agents" / "skills",
            self.workspace / ".claude" / "skills",
            self.workspace / ".opencode" / "skills",
            self.workspace / "skills",
            Path.home() / ".agents" / "skills",
            Path.home() / ".claude" / "skills",
            Path.home() / ".config" / "opencode" / "skills",
        ]

    def _skills(self) -> Dict[str, Path]:
        found: Dict[str, Path] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for skill_dir in sorted(root.iterdir()):
                skill_file = skill_dir / "SKILL.md"
                if skill_dir.is_dir() and skill_file.is_file():
                    found.setdefault(skill_dir.name, skill_file)
        return found

    def index(self) -> str:
        rows = []
        for name, path in self._skills().items():
            description = ""
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                        break
            except OSError:
                continue
            rows.append(f"- {name}: {description or '使用该技能目录中的 SKILL.md'}")
        return "\n".join(rows) or "（当前没有发现可用技能）"

    def get_tools(self) -> List[ToolDefinition]:
        return [ToolDefinition(
            name="load_skill",
            description="按名称加载项目或用户技能的 SKILL.md，使用前先从 System Prompt 的技能索引选择技能",
            parameters={
                "type": "object",
                "properties": {"skillName": {"type": "string", "description": "技能目录名"}},
                "required": ["skillName"],
            },
        )]

    async def execute(self, name: str, args: Dict[str, Any]) -> str:
        if name != "load_skill":
            raise ValueError(f"Unknown Skills Tool: {name}")
        skill_name = str(args.get("skillName") or "").strip()
        path = self._skills().get(skill_name)
        if path is None:
            return f"[Error]: 未找到技能 {skill_name!r}"
        try:
            return f"技能 {skill_name}：\n\n{path.read_text(encoding='utf-8')}"
        except OSError as exc:
            return f"[Error]: 无法读取技能 {skill_name!r}: {exc}"

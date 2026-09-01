import asyncio

from litecode.core.system_prompt import SystemPromptBuilder
from litecode.tools.skills import SkillsTools


def test_skill_index_and_load(tmp_path):
    skill = tmp_path / ".agents" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("description: Review workflow\n\nRun tests first.", encoding="utf-8")
    tools = SkillsTools(str(tmp_path))

    assert "review: Review workflow" in tools.index()
    result = asyncio.run(tools.execute("load_skill", {"skillName": "review"}))
    assert "Run tests first." in result


def test_project_instructions_support_claude_uppercase(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Use the repository style.", encoding="utf-8")
    prompt = SystemPromptBuilder.build(str(tmp_path), [])
    assert "Use the repository style." in prompt
    assert "load_skill" in prompt

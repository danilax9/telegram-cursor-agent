"""Cursor Agent Skills discovery and routing rules."""

from pathlib import Path

from telegram_cursor_agent.agent.skills import (
    CursorSkill,
    build_skills_routing_rule,
    compose_task_prompt,
    discover_cursor_skills,
    format_skills_index,
    select_skills_for_prompt,
)
from telegram_cursor_agent.core.config import Settings, default_cursor_skills_dirs


def test_discover_cursor_skills_from_user_dir(
    test_settings: Settings, tmp_path: Path
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo for tests.\n---\n# Demo\n",
        encoding="utf-8",
    )
    settings = test_settings.model_copy(update={"cursor_skills_dirs": [skills_root]})
    found = discover_cursor_skills(settings)
    assert len(found) == 1
    assert found[0].name == "demo-skill"
    assert "Demo for tests" in found[0].description


def test_project_skills_override_user(
    test_settings: Settings, tmp_path: Path
) -> None:
    user_root = tmp_path / "user-skills"
    user_skill = user_root / "shared"
    user_skill.mkdir(parents=True)
    user_skill.joinpath("SKILL.md").write_text(
        "---\nname: shared\ndescription: User copy.\n---\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    proj_skill = project / ".cursor" / "skills" / "shared"
    proj_skill.mkdir(parents=True)
    proj_skill.joinpath("SKILL.md").write_text(
        "---\nname: shared\ndescription: Project copy.\n---\n",
        encoding="utf-8",
    )
    settings = test_settings.model_copy(update={"cursor_skills_dirs": [user_root]})
    found = discover_cursor_skills(settings, project)
    assert len(found) == 1
    assert found[0].scope == "project"
    assert found[0].description == "Project copy."


def test_build_skills_routing_includes_index(
    test_settings: Settings, tmp_path: Path
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "alpha"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        encoding="utf-8",
    )
    settings = test_settings.model_copy(update={"cursor_skills_dirs": [skills_root]})
    rule = build_skills_routing_rule(settings, str(tmp_path / "ws"))
    assert "alwaysApply: true" in rule
    assert "**alpha**" in rule
    assert "Alpha skill" in rule
    assert "Mandatory routing" in rule
    assert "skills-cursor" in rule
    assert format_skills_index([])


def _write_skill(root: Path, name: str, body: str, description: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    desc = description or f"{name} skill."
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n{body}",
        encoding="utf-8",
    )


def test_discover_skips_hermes_and_keeps_symlink_path(
    test_settings: Settings, tmp_path: Path
) -> None:
    hermes = tmp_path / ".hermes" / "skills" / "foreign"
    hermes.mkdir(parents=True)
    hermes.joinpath("SKILL.md").write_text(
        "---\nname: foreign\ndescription: Hermes skill.\n---\n",
        encoding="utf-8",
    )
    skills_root = tmp_path / "skills"
    real = tmp_path / "plugins" / "cache" / "plugin-skill"
    real.mkdir(parents=True)
    real.joinpath("SKILL.md").write_text(
        "---\nname: linked\ndescription: Linked skill.\n---\n# Stay in the skills dir\n",
        encoding="utf-8",
    )
    link = skills_root / "linked"
    skills_root.mkdir()
    link.symlink_to(real, target_is_directory=True)
    settings = test_settings.model_copy(
        update={"cursor_skills_dirs": [hermes.parent, skills_root]}
    )
    found = discover_cursor_skills(settings)
    assert [skill.name for skill in found] == ["linked"]
    assert found[0].skill_md == (link / "SKILL.md").absolute()
    assert "cache" not in str(found[0].skill_md)


def test_select_landing_and_ignore_file_format(tmp_path: Path) -> None:
    landing = CursorSkill(
        name="web-landing-pages",
        description="Landing pages.",
        skill_md=tmp_path / "web-landing-pages" / "SKILL.md",
        scope="user",
    )
    forms = CursorSkill(
        name="forms-inputs-checkout",
        description="Checkout forms.",
        skill_md=tmp_path / "forms" / "SKILL.md",
        scope="user",
    )
    selected = select_skills_for_prompt([landing, forms], "сделай лендинг для кафе")
    assert [skill.name for skill in selected] == ["web-landing-pages"]
    assert select_skills_for_prompt([forms], "поменяй формат файла") == []


def test_description_overlap_selects_skill_without_route() -> None:
    skill = CursorSkill(
        name="billing-export",
        description="Export invoices and payment receipts for accounting.",
        skill_md=Path("/skills/billing-export/SKILL.md"),
        scope="user",
    )
    found = select_skills_for_prompt([skill], "export invoices and receipts")
    assert [item.name for item in found] == ["billing-export"]


def test_compose_task_prompt_binds_identity_and_skills(
    test_settings: Settings, tmp_path: Path
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "skill-manager", "# List skills only here\n")
    _write_skill(skills_root, "web-landing-pages", "# " + ("x" * 5000))
    _write_skill(skills_root, "modern-web-design", "# modern\n")
    _write_skill(skills_root, "frontend-design", "# frontend\n")
    settings = test_settings.model_copy(update={"cursor_skills_dirs": [skills_root]})
    workspace = str(tmp_path / "ws")

    landing = compose_task_prompt(settings, workspace, "сделай лендинг для кафе")
    assert "telegram-cursor-agent" in landing
    assert "~/.hermes/skills" in landing
    assert "web-landing-pages" in landing
    assert "modern-web-design" in landing
    assert "frontend-design" in landing
    assert "List skills only here" not in landing
    assert "xxxxx" not in landing
    assert landing.endswith("сделай лендинг для кафе")

    skills_question = compose_task_prompt(settings, workspace, "какие у тебя скиллы?")
    assert "skill-manager" in skills_question
    assert "List skills only here" in skills_question
    assert skills_question.endswith("какие у тебя скиллы?")

    bugfix = compose_task_prompt(settings, workspace, "почини воркер")
    assert "не выбран" in bugfix
    assert bugfix.endswith("почини воркер")

    system = "[System: Self-deploy finished successfully.]"
    assert compose_task_prompt(settings, workspace, system) == system


def test_inlined_skill_uses_absolute_skills_dir(
    test_settings: Settings, tmp_path: Path
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "skill-manager",
        "bash ~/.cursor/skills/skill-manager/scripts/list-skills.sh\n",
    )
    settings = test_settings.model_copy(update={"cursor_skills_dirs": [skills_root]})
    prompt = compose_task_prompt(settings, str(tmp_path), "какие у тебя скиллы?")
    assert f"bash {skills_root}/skill-manager/scripts/list-skills.sh" in prompt
    header = prompt.split("[User task]", 1)[0]
    assert "~/.cursor/skills/" not in header


def test_default_skills_dir_is_not_cursor_account_home(monkeypatch, tmp_path: Path) -> None:
    account = tmp_path / ".cursor-accounts" / "main"
    (account / ".cursor" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(account))
    monkeypatch.delenv("CURSOR_SKILLS_DIRS", raising=False)
    chosen = default_cursor_skills_dirs()
    root_skills = Path("/root/.cursor/skills")
    if root_skills.is_dir():
        assert chosen == [root_skills]
    else:
        assert chosen == [account / ".cursor" / "skills"]

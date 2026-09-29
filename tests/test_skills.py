"""Cursor Agent Skills discovery and prompt composition."""

from pathlib import Path

from telegram_cursor_agent.agent.prompts import (
    REVIEW_MAX_PROMPT,
    REVIEW_PASS_PROMPT,
    cycle_review_mode,
    review_follow_up,
    review_marked_ok,
    strip_review_mark,
)
from telegram_cursor_agent.agent.skills import (
    CursorSkill,
    build_skills_routing_rule,
    compose_task_prompt,
    discover_cursor_skills,
    format_skills_index,
    format_skills_menu,
)
from telegram_cursor_agent.core.config import Settings, default_cursor_skills_dirs


def test_format_skills_menu_lists_name_and_description() -> None:
    text = format_skills_menu(
        [
            CursorSkill(
                name="demo-skill",
                description="Коротко *про* навык и _ещё_ текст.",
                skill_md=Path("/tmp/SKILL.md"),
                scope="user",
            )
        ]
    )
    assert "*Скиллы* — 1" in text
    assert "`demo-skill`" in text
    assert "*" not in text.split("—", 1)[1]
    assert "_" not in text


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
    assert "Contextual routing" in rule
    assert "does **not** auto-select" in rule
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


def test_compose_task_prompt_agent_picks_skills_no_script_preselect(
    test_settings: Settings, tmp_path: Path
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "skill-manager", "# List skills only here\n")
    _write_skill(skills_root, "web-landing-pages", "# " + ("x" * 5000))
    settings = test_settings.model_copy(update={"cursor_skills_dirs": [skills_root]})
    workspace = str(tmp_path / "ws")

    landing = compose_task_prompt(settings, workspace, "сделай лендинг для кафе")
    assert "telegram-cursor-agent" in landing
    assert "~/.hermes/skills" in landing
    assert "решает агент, не бот" in landing
    assert "по контексту" in landing
    assert "skills-routing.mdc" in landing
    assert "Обязательные скиллы" not in landing
    assert "List skills only here" not in landing
    assert "xxxxx" not in landing
    assert landing.endswith("сделай лендинг для кафе")

    skills_question = compose_task_prompt(settings, workspace, "какие у тебя скиллы?")
    assert "skill-manager" in skills_question
    assert "List skills only here" not in skills_question
    assert skills_question.endswith("какие у тебя скиллы?")

    bugfix = compose_task_prompt(settings, workspace, "почини воркер")
    assert "skills-routing.mdc" in bugfix
    assert bugfix.endswith("почини воркер")

    system = "[System: Self-deploy finished successfully.]"
    assert compose_task_prompt(settings, workspace, system) == system


def test_review_modes_off_on_and_max() -> None:
    plain = "почини воркер"
    system = "[System: Self-deploy finished successfully.]"
    assert cycle_review_mode("off") == "on"
    assert cycle_review_mode("on") == "max"
    assert cycle_review_mode("max") == "off"
    assert review_follow_up("off", plain, 0) is None
    assert review_follow_up("on", system, 0) is None
    assert review_follow_up("on", plain, 0) == REVIEW_PASS_PROMPT
    assert review_follow_up("on", plain, 1) is None
    assert review_follow_up("max", plain, 0) == REVIEW_MAX_PROMPT
    assert review_follow_up("max", plain, 4) == REVIEW_MAX_PROMPT
    assert review_follow_up("max", plain, 5) is None
    answer = "Готово.\nREVIEW_OK"
    assert review_marked_ok(answer)
    assert strip_review_mark(answer) == "Готово."
    assert not review_marked_ok("REVIEW_OK ещё не всё")


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

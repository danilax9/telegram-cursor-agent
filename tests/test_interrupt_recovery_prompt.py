from telegram_cursor_agent.services.deploy_resume import build_interrupt_recovery_prompt


def test_build_interrupt_recovery_prompt_includes_user_text() -> None:
    prompt = build_interrupt_recovery_prompt("исправь баг с сессией")
    assert "исправь баг с сессией" in prompt
    assert "System:" in prompt


def test_build_interrupt_recovery_prompt_skips_nested_system() -> None:
    prompt = build_interrupt_recovery_prompt("[System: prior recovery]")
    assert "prior recovery" not in prompt

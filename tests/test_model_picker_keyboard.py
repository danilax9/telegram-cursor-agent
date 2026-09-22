"""Model picker inline keyboard tests."""

from telegram_cursor_agent.services.cursor_model_catalog import friendly_tier_label
from telegram_cursor_agent.telegram.model_keyboards import (
    ModelPickerState,
    model_picker_keyboard,
    parse_mpick_callback,
)

SAMPLE = [
    {"id": "composer-2.5", "label": "Composer 2.5 (current)"},
    {"id": "gpt-5.2", "label": "GPT-5.2"},
    {"id": "claude-opus-5-high", "label": "Claude Opus 5 1M"},
    {"id": "claude-opus-5-thinking-high-fast", "label": "Claude Opus 5 1M Thinking Fast"},
]


def _flat_callbacks(kb) -> list[str]:
    return [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


def test_fam_step_shows_only_families() -> None:
    kb = model_picker_keyboard(SAMPLE, ModelPickerState(step="fam", menu=True))
    cbs = _flat_callbacks(kb)
    assert any(cb.startswith("mpick:s:") for cb in cbs)
    assert not any(cb.startswith("model:") for cb in cbs)


def test_models_step_shows_only_models() -> None:
    kb = model_picker_keyboard(
        SAMPLE,
        ModelPickerState(step="models", sel_fi=2, menu=True),
    )
    cbs = _flat_callbacks(kb)
    assert any(cb.startswith("model:") or cb.startswith("mpick:vb:") for cb in cbs)
    assert not any(cb.startswith("mpick:s:") for cb in cbs)
    assert any(cb.startswith("mpick:bf:") for cb in cbs)


def test_picker_footer_pager_then_back() -> None:
    kb = model_picker_keyboard(
        SAMPLE,
        ModelPickerState(step="models", sel_fi=2, menu=True),
    )
    rows = kb.inline_keyboard
    back_row = rows[-1]
    assert back_row[0].text == "← Семейства"
    assert not any(r[0].text.startswith("🔄") for r in rows)


def test_parse_mpick_menu_flag() -> None:
    parsed = parse_mpick_callback("mpick:s:0:1:0:m")
    assert parsed is not None
    prefix, state = parsed
    assert prefix == "s"
    assert state.menu is True
    assert state.sel_fi == 1
    assert state.step == "models"


def test_friendly_tier_label() -> None:
    assert friendly_tier_label("High Fast") == "Мощная · быстрая"
    assert friendly_tier_label("Low") == "Лёгкая"

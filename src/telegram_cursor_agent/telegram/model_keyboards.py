"""Inline keyboards for hierarchical model selection."""

from __future__ import annotations

import json
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.cursor_model_catalog import (
    ModelBase,
    ModelFamily,
    ModelVariant,
    build_model_catalog,
    friendly_tier_label,
)
from telegram_cursor_agent.services.cursor_models import load_selected_model_id

PAGE_SIZE = 6
FAMILY_COLS = 2
MODEL_COLS = 2
MENU_BACK_CURSOR = "menu:sub:cursor"


@dataclass(frozen=True)
class ModelPickerState:
    """step: fam — только семейства; models — модели семейства; variants — режимы."""

    step: str = "fam"
    fam_page: int = 0
    sel_fi: int = 0
    base_page: int = 0
    variant_base_idx: int | None = None
    variant_page: int = 0
    menu: bool = False

    def with_menu(self, menu: bool) -> ModelPickerState:
        return ModelPickerState(
            step=self.step,
            fam_page=self.fam_page,
            sel_fi=self.sel_fi,
            base_page=self.base_page,
            variant_base_idx=self.variant_base_idx,
            variant_page=self.variant_page,
            menu=menu,
        )


def initial_picker_state(
    models: list[dict[str, str]],
    settings: Settings,
    *,
    menu: bool = False,
) -> ModelPickerState:
    catalog = build_model_catalog(models)
    if not catalog:
        return ModelPickerState(menu=menu)
    current = load_selected_model_id(settings) or "auto"
    for fi, family in enumerate(catalog):
        for bi, base in enumerate(family.bases):
            for variant in base.variants:
                if variant.model_id == current:
                    fam_page = fi // PAGE_SIZE
                    base_page = bi // PAGE_SIZE
                    return ModelPickerState(
                        step="fam",
                        fam_page=fam_page,
                        sel_fi=fi,
                        base_page=base_page,
                        menu=menu,
                    )
    return ModelPickerState(step="fam", menu=menu)


def _menu_suffix(state: ModelPickerState) -> str:
    return ":m" if state.menu else ""


def _cb(prefix: str, state: ModelPickerState, *parts: int) -> str:
    body = ":".join(str(p) for p in parts)
    return f"mpick:{prefix}:{body}{_menu_suffix(state)}"


def parse_mpick_callback(data: str) -> tuple[str, ModelPickerState] | None:
    if not data.startswith("mpick:"):
        return None
    if data == "mpick:noop":
        return "noop", ModelPickerState()
    menu = data.endswith(":m")
    core = data[:-2] if menu else data
    parts = core.split(":")
    if len(parts) < 2:
        return None
    prefix = parts[1]

    def state_from(
        fam_page: int,
        sel_fi: int,
        base_page: int,
        *,
        step: str = "fam",
        variant_base_idx: int | None = None,
        variant_page: int = 0,
    ) -> ModelPickerState:
        if variant_base_idx is not None:
            step = "variants"
        return ModelPickerState(
            step=step,
            fam_page=fam_page,
            sel_fi=sel_fi,
            base_page=base_page,
            variant_base_idx=variant_base_idx,
            variant_page=variant_page,
            menu=menu,
        )

    if prefix == "s" and len(parts) == 5:
        return prefix, state_from(
            int(parts[2]), int(parts[3]), 0, step="models"
        )
    if prefix == "bf" and len(parts) == 5:
        return prefix, state_from(
            int(parts[2]), int(parts[3]), 0, step="fam"
        )
    if prefix == "fp" and len(parts) == 6:
        return prefix, state_from(
            int(parts[2]), int(parts[3]), int(parts[4]), step="fam"
        )
    if prefix in {"bp", "bm"} and len(parts) == 6:
        return prefix, state_from(
            int(parts[2]),
            int(parts[3]),
            int(parts[4]),
            step="models",
            variant_base_idx=None,
            variant_page=int(parts[5]),
        )
    if prefix == "ref" and len(parts) == 8:
        vbi_raw = int(parts[5])
        vbi = None if vbi_raw < 0 else vbi_raw
        step_code = parts[7]
        step = {"f": "fam", "m": "models", "v": "variants"}.get(step_code, "fam")
        return prefix, state_from(
            int(parts[2]),
            int(parts[3]),
            int(parts[4]),
            step=step,
            variant_base_idx=vbi,
            variant_page=int(parts[6]),
        )
    if prefix == "vb" and len(parts) == 6:
        base_idx = int(parts[4])
        return prefix, state_from(
            int(parts[2]),
            int(parts[3]),
            base_idx // PAGE_SIZE,
            variant_base_idx=base_idx,
            variant_page=0,
        )
    if prefix == "vp" and len(parts) == 6:
        base_idx = int(parts[4])
        return prefix, state_from(
            int(parts[2]),
            int(parts[3]),
            base_idx // PAGE_SIZE,
            variant_base_idx=base_idx,
            variant_page=int(parts[5]),
        )
    return None


def _step_code(state: ModelPickerState) -> str:
    if state.step == "models":
        return "m"
    if state.step == "variants" or state.variant_base_idx is not None:
        return "v"
    return "f"


def refresh_models_payload(state: ModelPickerState, chat_id: int, message_id: int) -> str:
    data = {
        "menu_message": {
            "chat_id": chat_id,
            "message_id": message_id,
            "view": "picker",
            "picker": {
                "step": state.step,
                "fam_page": state.fam_page,
                "sel_fi": state.sel_fi,
                "base_page": state.base_page,
                "variant_base_idx": state.variant_base_idx,
                "variant_page": state.variant_page,
                "menu": state.menu,
            },
        }
    }
    return json.dumps(data, ensure_ascii=False)


def refresh_models_submenu_payload(chat_id: int, message_id: int) -> str:
    return json.dumps(
        {
            "menu_message": {
                "chat_id": chat_id,
                "message_id": message_id,
                "view": "cursor_submenu",
            }
        },
        ensure_ascii=False,
    )


def picker_state_from_payload(raw: dict[str, object]) -> ModelPickerState | None:
    picker = raw.get("picker")
    if not isinstance(picker, dict):
        return None
    variant_raw = picker.get("variant_base_idx")
    variant_base_idx = int(variant_raw) if variant_raw is not None else None
    step = str(picker.get("step", "fam"))
    if variant_base_idx is not None:
        step = "variants"
    return ModelPickerState(
        step=step,
        fam_page=int(picker.get("fam_page", 0)),
        sel_fi=int(picker.get("sel_fi", 0)),
        base_page=int(picker.get("base_page", 0)),
        variant_base_idx=variant_base_idx,
        variant_page=int(picker.get("variant_page", 0)),
        menu=bool(picker.get("menu", False)),
    )


def _clamp_sel_fi(catalog: list[ModelFamily], state: ModelPickerState) -> ModelPickerState:
    if not catalog:
        return state
    sel = max(0, min(state.sel_fi, len(catalog) - 1))
    if sel != state.sel_fi:
        return ModelPickerState(
            step=state.step,
            fam_page=state.fam_page,
            sel_fi=sel,
            base_page=0,
            variant_base_idx=None,
            variant_page=0,
            menu=state.menu,
        )
    return state


def _family_at(catalog: list[ModelFamily], fi: int) -> ModelFamily | None:
    if fi < 0 or fi >= len(catalog):
        return None
    return catalog[fi]


def _current_model_id(settings: Settings | None, current_id: str | None) -> str | None:
    if current_id is not None:
        return current_id
    if settings is None:
        return None
    return load_selected_model_id(settings)


def model_picker_text(
    models: list[dict[str, str]],
    state: ModelPickerState,
    settings: Settings | None = None,
    *,
    current_id: str | None = None,
) -> str:
    catalog = build_model_catalog(models)
    state = _clamp_sel_fi(catalog, state)
    family = _family_at(catalog, state.sel_fi)
    active = _current_model_id(settings, current_id)
    active_label = next(
        (item["label"] for item in models if item.get("id") == active),
        active or "—",
    )

    lines = ["*Модель Cursor*"]
    if active:
        lines.append(f"Сейчас: *{_short_label(active_label, 48)}*")
    lines.append("")

    if family is None:
        lines.append("Каталог пуст.")
        return "\n".join(lines)

    if state.step == "variants" and state.variant_base_idx is not None:
        base = _base_at(family, state.variant_base_idx)
        if base is not None:
            lines.append(f"*{family.label}* — `{_short_label(base.label, 36)}`")
            lines.append("Выбери режим:")
            return "\n".join(lines)

    if state.step == "models":
        lines.append(f"*{family.label}* — выбери модель:")
        return "\n".join(lines)

    lines.append("Выбери семейство:")
    return "\n".join(lines)


def model_picker_keyboard(
    models: list[dict[str, str]],
    state: ModelPickerState,
    settings: Settings | None = None,
    *,
    current_id: str | None = None,
) -> InlineKeyboardMarkup:
    catalog = build_model_catalog(models)
    state = _clamp_sel_fi(catalog, state)
    family = _family_at(catalog, state.sel_fi)
    active = _current_model_id(settings, current_id)
    rows: list[list[InlineKeyboardButton]] = []

    if state.step == "variants" and family is not None and state.variant_base_idx is not None:
        base = _base_at(family, state.variant_base_idx)
        if base is not None:
            var_start = state.variant_page * PAGE_SIZE
            variants = base.variants[var_start : var_start + PAGE_SIZE]
            variant_buttons = [
                _variant_button(variant, state, active) for variant in variants
            ]
            rows.extend(_grid_rows(variant_buttons, MODEL_COLS))
            _append_pager_and_back(
                rows,
                _variant_pager_row(state, len(base.variants)),
                [
                    InlineKeyboardButton(
                        text="← Модели",
                        callback_data=_cb(
                            "bm",
                            state,
                            state.fam_page,
                            state.sel_fi,
                            state.base_page,
                            0,
                        ),
                    )
                ],
            )
    elif state.step == "models" and family is not None:
        base_start = state.base_page * PAGE_SIZE
        bases = family.bases[base_start : base_start + PAGE_SIZE]
        model_buttons: list[InlineKeyboardButton] = []
        for row_idx, base in enumerate(bases):
            model_buttons.append(
                _base_button(family, base, base_start + row_idx, state, active)
            )
        rows.extend(_grid_rows(model_buttons, MODEL_COLS))
        _append_pager_and_back(
            rows,
            _models_nav_row(state, len(family.bases)),
            [
                InlineKeyboardButton(
                    text="← Семейства",
                    callback_data=_cb("bf", state, state.fam_page, state.sel_fi, 0),
                )
            ],
        )
    elif state.step == "fam":
        fam_start = state.fam_page * PAGE_SIZE
        fam_slice = catalog[fam_start : fam_start + PAGE_SIZE]
        fam_buttons = [
            _family_button(fam_slice, row_idx, fam_start, state, active)
            for row_idx in range(len(fam_slice))
        ]
        rows.extend(_grid_rows(fam_buttons, FAMILY_COLS))
        back: list[InlineKeyboardButton] = []
        if state.menu:
            back = [
                InlineKeyboardButton(
                    text="← Cursor",
                    callback_data=MENU_BACK_CURSOR,
                )
            ]
        _append_pager_and_back(rows, _fam_nav_row(state, len(catalog)), back)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _append_pager_and_back(
    rows: list[list[InlineKeyboardButton]],
    pager: list[InlineKeyboardButton],
    back: list[InlineKeyboardButton],
) -> None:
    if pager:
        rows.append(pager)
    if back:
        rows.append(back)


def _grid_rows(
    buttons: list[InlineKeyboardButton],
    cols: int,
) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(buttons), cols):
        rows.append(buttons[index : index + cols])
    return rows


def _family_contains_active(family: ModelFamily, active: str | None) -> bool:
    if not active:
        return False
    return any(v.model_id == active for b in family.bases for v in b.variants)


def _family_button(
    fam_slice: list[ModelFamily],
    row_idx: int,
    fam_start: int,
    state: ModelPickerState,
    active: str | None,
) -> InlineKeyboardButton:
    family = fam_slice[row_idx]
    fi = fam_start + row_idx
    label = family.label
    if _family_contains_active(family, active):
        label = f"· {label} ·"
    return InlineKeyboardButton(
        text=label,
        callback_data=_cb("s", state, state.fam_page, fi, 0),
    )


def _base_button(
    family: ModelFamily,
    base: ModelBase,
    base_idx: int,
    state: ModelPickerState,
    active: str | None,
) -> InlineKeyboardButton:
    if len(base.variants) == 1:
        variant = base.variants[0]
        return InlineKeyboardButton(
            text=_model_button_label(base.label, variant, active),
            callback_data=_model_callback(variant.model_id, state.menu),
        )
    return InlineKeyboardButton(
        text=f"{_short_label(base.label)} ›",
        callback_data=_cb("vb", state, state.fam_page, state.sel_fi, base_idx, 0),
    )


def _variant_button(
    variant: ModelVariant,
    state: ModelPickerState,
    active: str | None,
) -> InlineKeyboardButton:
    tier = friendly_tier_label(variant.tier_label)
    mark = "✓ " if active and variant.model_id == active else ""
    return InlineKeyboardButton(
        text=f"{mark}{tier}",
        callback_data=_model_callback(variant.model_id, state.menu),
    )


def _model_callback(model_id: str, menu: bool) -> str:
    return f"model:{model_id}:m" if menu else f"model:{model_id}"


def _model_button_label(
    base_label: str,
    variant: ModelVariant,
    active: str | None,
) -> str:
    mark = "✓ " if active and variant.model_id == active else ""
    name = _short_label(base_label)
    if variant.tier_label and variant.tier_label not in {"Default", ""}:
        tier = friendly_tier_label(variant.tier_label)
        if tier.lower() not in name.lower():
            name = f"{name} ({tier})"
    return f"{mark}{name}"


def _short_label(label: str, max_len: int = 28) -> str:
    cleaned = label.replace(" (current)", "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _base_at(family: ModelFamily, base_idx: int) -> ModelBase | None:
    if base_idx < 0 or base_idx >= len(family.bases):
        return None
    return family.bases[base_idx]


def _fam_nav_row(state: ModelPickerState, total_families: int) -> list[InlineKeyboardButton]:
    nav: list[InlineKeyboardButton] = []
    if state.fam_page > 0:
        nav.append(
            InlineKeyboardButton(
                text="‹",
                callback_data=_cb(
                    "fp",
                    state,
                    state.fam_page - 1,
                    state.sel_fi,
                    0,
                    0,
                ),
            )
        )
    if (state.fam_page + 1) * PAGE_SIZE < total_families:
        nav.append(
            InlineKeyboardButton(
                text="›",
                callback_data=_cb(
                    "fp",
                    state,
                    state.fam_page + 1,
                    state.sel_fi,
                    0,
                    0,
                ),
            )
        )
    return nav


def _models_nav_row(state: ModelPickerState, total_bases: int) -> list[InlineKeyboardButton]:
    nav: list[InlineKeyboardButton] = []
    page = state.base_page
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="‹",
                callback_data=_cb(
                    "bp",
                    state,
                    state.fam_page,
                    state.sel_fi,
                    page - 1,
                    0,
                ),
            )
        )
    if (page + 1) * PAGE_SIZE < total_bases:
        nav.append(
            InlineKeyboardButton(
                text="›",
                callback_data=_cb(
                    "bp",
                    state,
                    state.fam_page,
                    state.sel_fi,
                    page + 1,
                    0,
                ),
            )
        )
    return nav


def _variant_pager_row(
    state: ModelPickerState,
    total_variants: int,
) -> list[InlineKeyboardButton]:
    base_idx = state.variant_base_idx or 0
    nav: list[InlineKeyboardButton] = []
    if state.variant_page > 0:
        nav.append(
            InlineKeyboardButton(
                text="‹",
                callback_data=_cb(
                    "vp",
                    state,
                    state.fam_page,
                    state.sel_fi,
                    base_idx,
                    state.variant_page - 1,
                ),
            )
        )
    if (state.variant_page + 1) * PAGE_SIZE < total_variants:
        nav.append(
            InlineKeyboardButton(
                text="›",
                callback_data=_cb(
                    "vp",
                    state,
                    state.fam_page,
                    state.sel_fi,
                    base_idx,
                    state.variant_page + 1,
                ),
            )
        )
    return nav


def find_family_label(models: list[dict[str, str]], family_key: str) -> str:
    for family in build_model_catalog(models):
        if family.key == family_key:
            return family.label
    return family_key

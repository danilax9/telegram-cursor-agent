"""Inline keyboards for hierarchical model selection."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_cursor_agent.services.cursor_model_catalog import (
    ModelBase,
    ModelFamily,
    build_model_catalog,
)

PAGE_SIZE = 8


def model_families_keyboard(
    models: list[dict[str, str]],
    page: int = 0,
) -> InlineKeyboardMarkup:
    catalog = build_model_catalog(models)
    start = page * PAGE_SIZE
    current = catalog[start : start + PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []

    for family in current:
        if len(family.bases) == 1 and len(family.bases[0].variants) == 1:
            variant = family.bases[0].variants[0]
            rows.append([
                InlineKeyboardButton(
                    text=family.label,
                    callback_data=f"model:{variant.model_id}",
                )
            ])
            continue
        rows.append([
            InlineKeyboardButton(
                text=f"{family.label} ({len(family.bases)})",
                callback_data=f"mfam:{family.key}",
            )
        ])

    pager = _pager_row(page, len(catalog), "mfams")
    if pager:
        rows.append(pager)
    rows.append([
        InlineKeyboardButton(text="Обновить каталог", callback_data="models:refresh"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_bases_keyboard(
    models: list[dict[str, str]],
    family_key: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    catalog = build_model_catalog(models)
    family = _require_family(catalog, family_key)
    start = page * PAGE_SIZE
    current = family.bases[start : start + PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []

    for base in current:
        if len(base.variants) == 1:
            rows.append([
                InlineKeyboardButton(
                    text=base.label,
                    callback_data=f"model:{base.variants[0].model_id}",
                )
            ])
            continue
        rows.append([
            InlineKeyboardButton(
                text=base.label,
                callback_data=f"mbase:{family.key}:{base.key}",
            )
        ])

    rows.append([
        InlineKeyboardButton(text="← Семейства", callback_data="mfams:0"),
    ])
    rows.append(_pager_row(page, len(family.bases), f"mbases:{family.key}"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_variants_keyboard(
    models: list[dict[str, str]],
    family_key: str,
    base_key: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    catalog = build_model_catalog(models)
    family = _require_family(catalog, family_key)
    base = _require_base(family, base_key)
    start = page * PAGE_SIZE
    current = base.variants[start : start + PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(
                text=variant.tier_label,
                callback_data=f"model:{variant.model_id}",
            )
        ]
        for variant in current
    ]
    rows.append([
        InlineKeyboardButton(
            text="← Модели",
            callback_data=f"mfam:{family.key}",
        )
    ])
    if len(base.variants) > PAGE_SIZE:
        rows.append(_pager_row(page, len(base.variants), f"mvars:{family.key}:{base.key}"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_selection_text(
    models: list[dict[str, str]],
    *,
    family_key: str | None = None,
    base: ModelBase | None = None,
) -> str:
    if base is not None:
        family = find_family_label(models, family_key or "")
        return f"*{family}*\n`{base.label}`\n\nВыбери вариант:"
    if family_key:
        family = find_family_label(models, family_key)
        return f"*{family}*\n\nВыбери модель:"
    return "Выбери семейство модели Cursor:"


def find_family_label(models: list[dict[str, str]], family_key: str) -> str:
    for family in build_model_catalog(models):
        if family.key == family_key:
            return family.label
    return family_key


def _require_family(catalog: list[ModelFamily], family_key: str) -> ModelFamily:
    for family in catalog:
        if family.key == family_key:
            return family
    raise ValueError(f"Unknown model family: {family_key}")


def _require_base(family: ModelFamily, base_key: str) -> ModelBase:
    for base in family.bases:
        if base.key == base_key:
            return base
    raise ValueError(f"Unknown model base: {base_key}")


def _pager_row(current_page: int, total_items: int, prefix: str) -> list[InlineKeyboardButton]:
    nav: list[InlineKeyboardButton] = []
    if current_page > 0:
        nav.append(
            InlineKeyboardButton(text="‹", callback_data=f"{prefix}:{current_page - 1}")
        )
    if (current_page + 1) * PAGE_SIZE < total_items:
        nav.append(
            InlineKeyboardButton(text="›", callback_data=f"{prefix}:{current_page + 1}")
        )
    return nav

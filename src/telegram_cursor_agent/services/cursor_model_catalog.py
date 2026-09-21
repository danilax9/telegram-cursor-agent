"""Group Cursor models into family → base → tier navigation."""

from __future__ import annotations

from dataclasses import dataclass, field

TIER_WORDS = frozenset(
    {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "fast",
        "none",
        "minimal",
        "mini",
        "nano",
    }
)

FAMILY_ORDER = [
    "cursor",
    "gpt",
    "claude",
    "gemini",
    "muse",
    "kimi",
    "glm",
    "other",
]


@dataclass(frozen=True)
class ModelVariant:
    model_id: str
    label: str
    tier_label: str


@dataclass
class ModelBase:
    key: str
    label: str
    variants: list[ModelVariant] = field(default_factory=list)


@dataclass
class ModelFamily:
    key: str
    label: str
    bases: list[ModelBase] = field(default_factory=list)


def split_base_and_tier(model_id: str, label: str) -> tuple[str, str]:
    if model_id == "auto":
        return model_id, ""

    parts = model_id.split("-")
    tier_parts: list[str] = []
    while parts:
        if len(parts) >= 2 and parts[-1] == "fast" and parts[-2] in TIER_WORDS:
            tier_parts.insert(0, parts.pop())
            tier_parts.insert(0, parts.pop())
            continue
        if parts[-1] in TIER_WORDS:
            tier_parts.insert(0, parts.pop())
            continue
        break

    base_id = "-".join(parts) if parts else model_id
    if not tier_parts:
        return base_id, ""

    tier_label = _format_tier_label(tier_parts, label, base_id)
    return base_id, tier_label


def _format_tier_label(tier_parts: list[str], full_label: str, base_id: str) -> str:
    if not tier_parts:
        return "Default"

    base_label = _base_label_from_full(full_label, tier_parts)
    if base_label and full_label.startswith(base_label):
        remainder = full_label[len(base_label) :].strip(" -")
        if remainder:
            return remainder

    return " ".join(_title_word(part) for part in tier_parts)


def _base_label_from_full(full_label: str, tier_parts: list[str]) -> str:
    lowered = full_label.lower()
    for size in range(len(tier_parts), 0, -1):
        suffix = " ".join(_title_word(part) for part in tier_parts[-size:])
        if lowered.endswith(suffix.lower()):
            return full_label[: -len(suffix)].strip(" -")
    return ""


def _title_word(word: str) -> str:
    mapping = {
        "xhigh": "Extra High",
        "none": "None",
        "mini": "Mini",
        "nano": "Nano",
    }
    if word in mapping:
        return mapping[word]
    return word.replace("-", " ").title()


def detect_family(model_id: str, label: str) -> tuple[str, str]:
    if (
        model_id == "auto"
        or model_id.startswith("composer-")
        or "grok" in model_id
        or label.startswith("Cursor Grok")
    ):
        return "cursor", "Cursor"
    if model_id.startswith("claude-") or label.startswith("Claude "):
        return "claude", "Claude"
    if model_id.startswith("gpt-") or label.startswith("Codex") or label.startswith("GPT"):
        return "gpt", "GPT"
    if model_id.startswith("gemini-"):
        return "gemini", "Gemini"
    if model_id.startswith("muse-spark-"):
        return "muse", "Muse Spark"
    if model_id.startswith("kimi-"):
        return "kimi", "Kimi"
    if model_id.startswith("glm-"):
        return "glm", "GLM"

    return "other", "Other"


def base_display_label(model_id: str, label: str, tier_label: str) -> str:
    if model_id == "auto":
        return label
    if not tier_label or tier_label == "Default":
        return label

    main, suffix = _split_label_suffix(label)
    trimmed = _strip_tier_from_label(main, tier_label)
    if suffix:
        return f"{trimmed} {suffix}".strip()
    return trimmed


def _split_label_suffix(label: str) -> tuple[str, str]:
    marker = label.rfind("(")
    if marker == -1:
        return label, ""
    return label[:marker].rstrip(), label[marker:]


def _strip_tier_from_label(label: str, tier_label: str) -> str:
    base_label = _base_label_from_full(
        label, [part.lower().replace(" ", "-") for part in tier_label.split()]
    )
    if base_label:
        return base_label

    for token in (
        " Low Fast",
        " High Fast",
        " Medium Fast",
        " Max Fast",
        " None Fast",
        " Extra High Fast",
        " Low",
        " High",
        " Medium",
        " Max",
        " None",
        " Fast",
        " Extra High",
        " Minimal",
    ):
        if label.endswith(token):
            return label[: -len(token)].rstrip()
    return label


def build_model_catalog(models: list[dict[str, str]]) -> list[ModelFamily]:
    families: dict[str, ModelFamily] = {}
    bases: dict[str, dict[str, ModelBase]] = {}

    for item in models:
        model_id = str(item["id"])
        label = str(item["label"])
        family_key, family_label = detect_family(model_id, label)
        base_id, tier_label = split_base_and_tier(model_id, label)

        family = families.get(family_key)
        if family is None:
            family = ModelFamily(key=family_key, label=family_label)
            families[family_key] = family
            bases[family_key] = {}

        family_bases = bases[family_key]
        base = family_bases.get(base_id)
        if base is None:
            base = ModelBase(
                key=base_id,
                label=base_display_label(base_id, label, tier_label),
            )
            family_bases[base_id] = base
            family.bases.append(base)

        variant_tier = tier_label or "Default"
        base.variants.append(
            ModelVariant(
                model_id=model_id,
                label=label,
                tier_label=variant_tier,
            )
        )

    for family in families.values():
        for base in family.bases:
            exact = next(
                (variant for variant in base.variants if variant.model_id == base.key),
                None,
            )
            if exact is not None:
                base.label = exact.label
            elif base.variants:
                sample = base.variants[0]
                base.label = base_display_label(
                    base.key,
                    sample.label,
                    sample.tier_label,
                )
            base.variants.sort(key=lambda variant: _tier_sort_key(variant.tier_label))
        family.bases.sort(key=lambda base: base.label.lower())

    ordered: list[ModelFamily] = []
    seen: set[str] = set()
    for key in FAMILY_ORDER:
        family = families.get(key)
        if family is not None:
            ordered.append(family)
            seen.add(key)
    for key, family in families.items():
        if key not in seen:
            ordered.append(family)
    return ordered


def find_family(catalog: list[ModelFamily], family_key: str) -> ModelFamily | None:
    for family in catalog:
        if family.key == family_key:
            return family
    return None


def find_base(family: ModelFamily, base_key: str) -> ModelBase | None:
    for base in family.bases:
        if base.key == base_key:
            return base
    return None


def _tier_sort_key(tier_label: str) -> tuple[int, str]:
    order = {
        "default": 0,
        "none": 1,
        "minimal": 2,
        "low": 3,
        "medium": 4,
        "high": 5,
        "extra high": 6,
        "max": 7,
    }
    lowered = tier_label.lower()
    for key, value in order.items():
        if key in lowered:
            return (value, lowered)
    return (99, lowered)

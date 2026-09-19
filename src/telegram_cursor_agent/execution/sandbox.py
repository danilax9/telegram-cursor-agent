"""Path canonicalization and allowed-root enforcement."""

from pathlib import Path

from telegram_cursor_agent.core.config import Settings


class PathAccessError(PermissionError):
    """Raised when a path is outside allowed roots or forbidden."""


def canonicalize_path(path: str | Path, allowed_roots: list[Path]) -> Path:
    """Resolve and canonicalize a path, ensuring it stays within allowed roots."""
    if not allowed_roots:
        raise PathAccessError("No allowed roots configured")

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = allowed_roots[0] / candidate

    resolved = candidate.resolve(strict=False)

    for root in allowed_roots:
        root_resolved = root.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
            return resolved
        except ValueError:
            continue

    raise PathAccessError(f"Path {resolved} is outside allowed roots")


def is_path_allowed(
    path: str | Path,
    allowed_roots: list[Path],
    forbidden_segments: list[str],
) -> bool:
    try:
        resolved = canonicalize_path(path, allowed_roots)
    except PathAccessError:
        return False

    for part in resolved.parts:
        if part in forbidden_segments:
            return False
        if part.startswith(".") and part not in (".", ".."):
            return False
    return True


def assert_path_allowed(
    path: str | Path,
    settings: Settings,
) -> Path:
    roots = [r.resolve(strict=False) for r in settings.allowed_project_roots]
    if not is_path_allowed(path, roots, settings.forbidden_path_segments):
        raise PathAccessError(f"Access denied to path: {path}")
    return canonicalize_path(path, roots)


def get_allowed_roots(settings: Settings) -> list[Path]:
    return [r.resolve(strict=False) for r in settings.allowed_project_roots]

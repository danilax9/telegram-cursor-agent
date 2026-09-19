"""Project discovery by scanning roots for file signatures."""

from dataclasses import dataclass
from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.sandbox import canonicalize_path, get_allowed_roots


@dataclass(frozen=True)
class DiscoveredProject:
    name: str
    root_path: Path
    signature_file: str


def discover_projects(settings: Settings) -> list[DiscoveredProject]:
    """Scan discovery roots for known project signature files."""
    roots = [r.resolve(strict=False) for r in settings.project_search_roots]
    signatures = set(settings.project_file_signatures)
    found: list[DiscoveredProject] = []
    seen_paths: set[Path] = set()

    for root in roots:
        if not root.exists():
            continue
        _scan_directory(root, signatures, found, seen_paths, max_depth=4)

    return sorted(found, key=lambda p: p.name.lower())


def _scan_directory(
    directory: Path,
    signatures: set[str],
    found: list[DiscoveredProject],
    seen_paths: set[Path],
    max_depth: int,
    depth: int = 0,
) -> None:
    if depth > max_depth:
        return

    try:
        entries = list(directory.iterdir())
    except (OSError, PermissionError):
        return

    for sig in signatures:
        sig_path = directory / sig
        if sig_path.is_file():
            resolved = directory.resolve()
            if resolved not in seen_paths:
                seen_paths.add(resolved)
                found.append(
                    DiscoveredProject(
                        name=resolved.name,
                        root_path=resolved,
                        signature_file=sig,
                    )
                )
            return

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in ("node_modules", "__pycache__", "venv"):
            continue
        _scan_directory(entry, signatures, found, seen_paths, max_depth, depth + 1)


def find_project_by_name(
    name: str, settings: Settings
) -> DiscoveredProject | None:
    normalized = name.strip().lower()
    for project in discover_projects(settings):
        if project.name.lower() == normalized:
            return project
    return None


def validate_project_path(path: str | Path, settings: Settings) -> Path:
    return canonicalize_path(path, get_allowed_roots(settings))

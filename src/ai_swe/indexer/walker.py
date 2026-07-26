"""
Recursive repository filesystem walker.

Yields every regular file under *root*, pruning directories that should
never be indexed (version-control internals, generated build artefacts,
dependency trees, virtual environments, various cache folders, etc.).

Usage::

    from pathlib import Path
    from ai_swe.indexer.walker import walk_repository

    for path in walk_repository(Path("/path/to/repo")):
        print(path)  # absolute Path objects
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Directories to skip entirely (matched against each directory *name*, not
# its full path, so they're ignored at any depth in the tree).
# ---------------------------------------------------------------------------
IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        # Version control
        ".git",
        ".svn",
        ".hg",
        # Dependency trees
        "node_modules",
        "vendor",
        "bower_components",
        # Build / distribution outputs
        "build",
        "dist",
        "out",
        "target",
        "_build",
        # Python virtual environments
        "venv",
        ".venv",
        "env",
        ".env",
        "virtualenv",
        # Caches
        "__pycache__",
        ".cache",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
        ".hypothesis",
        # IDE / tool dirs
        ".idea",
        ".vscode",
        ".DS_Store",
        # Egg/wheel build artefacts
        "*.egg-info",
        ".eggs",
        # Coverage
        "htmlcov",
        ".coverage",
    }
)

# File extensions that are almost certainly binary / not useful to parse.
IGNORED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
        ".tiff", ".tif",
        # Audio / video
        ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".avi", ".mov", ".mkv",
        # Archives
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        # Compiled / binary
        ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
        ".class", ".jar", ".war",
        # Data / DB
        ".db", ".sqlite", ".sqlite3",
        # Lock files (huge, machine-generated)
        ".lock",
        # Fonts
        ".ttf", ".otf", ".woff", ".woff2",
        # Documents
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    }
)

# Specific filenames to always skip.
IGNORED_FILENAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "go.sum",
    }
)


def _should_skip_dir(dir_name: str) -> bool:
    """Return True if a directory with *dir_name* should be pruned."""
    if dir_name in IGNORED_DIR_NAMES:
        return True
    # Handle glob-style patterns like "*.egg-info"
    if dir_name.endswith(".egg-info"):
        return True
    return False


def walk_repository(root: Path, max_file_size_bytes: int = 1_000_000) -> Iterator[Path]:
    """
    Yield every source file under *root*, skipping ignored directories/files.

    Args:
        root:                  Repository root directory (absolute or relative).
        max_file_size_bytes:   Skip files larger than this (default 1 MB).
                               Very large files are unlikely to be hand-written
                               source code.

    Yields:
        Absolute ``Path`` objects for every accepted regular file.
    """
    root = root.resolve()

    for dirpath_str, dirnames, filenames in os.walk(root, topdown=True):
        # --- Prune ignored directories in-place (affects os.walk recursion) ---
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        dirpath = Path(dirpath_str)

        for filename in filenames:
            if filename in IGNORED_FILENAMES:
                continue

            filepath = dirpath / filename

            if filepath.suffix.lower() in IGNORED_EXTENSIONS:
                continue

            try:
                if filepath.stat().st_size > max_file_size_bytes:
                    continue
            except OSError:
                continue  # broken symlink or permission error

            yield filepath


def collect_files(root: Path) -> list[Path]:
    """Convenience wrapper — returns a sorted list instead of a generator."""
    return sorted(walk_repository(root))

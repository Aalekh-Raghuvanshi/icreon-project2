"""
Language detection for source files.

Detection uses a two-step heuristic:

1. File **extension** — fast and covers the vast majority of cases.
2. **Shebang** line (first line ``#!/usr/bin/env python3`` etc.) — fallback
   for extension-less scripts.

Usage::

    from pathlib import Path
    from ai_swe.indexer.language import detect_language
    from ai_swe.indexer.models import Language

    lang = detect_language(Path("foo.py"))   # Language.PYTHON
    lang = detect_language(Path("Makefile")) # Language.UNKNOWN
"""

from __future__ import annotations

from pathlib import Path

from ai_swe.indexer.models import Language

# ---------------------------------------------------------------------------
# Extension → Language mapping
# ---------------------------------------------------------------------------

_EXT_MAP: dict[str, Language] = {
    # Python
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".pyw": Language.PYTHON,
    # JavaScript
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    # TypeScript
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    # Java
    ".java": Language.JAVA,
    # Go
    ".go": Language.GO,
    # C++
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".c++": Language.CPP,
    ".hpp": Language.CPP,
    ".hh": Language.CPP,
    ".hxx": Language.CPP,
    ".h": Language.CPP,  # ambiguous but usually C/C++
    ".c": Language.CPP,  # treat C as C++ for parsing purposes
}

# ---------------------------------------------------------------------------
# Shebang → Language mapping (partial string match, in order)
# ---------------------------------------------------------------------------

_SHEBANG_KEYWORDS: list[tuple[str, Language]] = [
    ("python", Language.PYTHON),
    ("node", Language.JAVASCRIPT),
    ("ts-node", Language.TYPESCRIPT),
    ("deno", Language.TYPESCRIPT),
]


def _language_from_shebang(first_line: str) -> Language:
    """Infer language from a shebang line; return UNKNOWN if unrecognised."""
    if not first_line.startswith("#!"):
        return Language.UNKNOWN
    lower = first_line.lower()
    for keyword, lang in _SHEBANG_KEYWORDS:
        if keyword in lower:
            return lang
    return Language.UNKNOWN


def detect_language(path: Path) -> Language:
    """
    Detect the programming language of *path*.

    First tries the file extension; if that yields UNKNOWN (e.g. a script
    with no extension), reads the first line and checks for a shebang.

    Args:
        path: Path to the source file (does not need to exist for the
              extension-based check, but must exist for the shebang check).

    Returns:
        A :class:`~ai_swe.indexer.models.Language` enum value.
    """
    ext = path.suffix.lower()
    if ext in _EXT_MAP:
        return _EXT_MAP[ext]

    # Try shebang
    try:
        with path.open("rb") as fh:
            first_line = fh.readline().decode("utf-8", errors="replace").strip()
        return _language_from_shebang(first_line)
    except OSError:
        return Language.UNKNOWN


def group_by_language(paths: list[Path]) -> dict[Language, list[Path]]:
    """
    Group a list of file paths by their detected language.

    Returns:
        dict mapping Language → list of Paths with that language.
    """
    result: dict[Language, list[Path]] = {lang: [] for lang in Language}
    for path in paths:
        result[detect_language(path)].append(path)
    return result

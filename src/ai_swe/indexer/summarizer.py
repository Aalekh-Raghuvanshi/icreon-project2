"""
Heuristic semantic file summarizer.

Generates a :class:`~ai_swe.indexer.models.FileSummary` for each source file
from its raw text and parsed symbols, without any LLM call. This is fast,
deterministic, and works completely offline.

**Purpose heuristics** (tried in order):
  1. Module-level docstring (Python ``\"\"\"...\"\"\"`` or ``'''...'''``)
  2. First ``//`` or ``#`` comment block
  3. Synthesised description: "Module with N classes, M functions"

Usage::

    from pathlib import Path
    from ai_swe.indexer.summarizer import summarize_file
    from ai_swe.indexer.models import Language

    summary = summarize_file(
        path=Path("src/foo.py"),
        relative_path="src/foo.py",
        language=Language.PYTHON,
        symbols=symbols,
    )
"""

from __future__ import annotations

import re
from pathlib import Path

from ai_swe.indexer.models import FileSummary, Language, Symbol, SymbolKind

# ---------------------------------------------------------------------------
# Docstring / header comment extraction
# ---------------------------------------------------------------------------

# Python triple-quoted string at the very start (may have leading whitespace)
_PY_DOCSTRING_RE = re.compile(
    r'^\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')',
    re.DOTALL,
)

# Single-line comment openers per language
_COMMENT_PREFIXES: dict[Language, list[str]] = {
    Language.PYTHON: ["#"],
    Language.JAVASCRIPT: ["//", "/*"],
    Language.TYPESCRIPT: ["//", "/*"],
    Language.JAVA: ["//", "/*"],
    Language.GO: ["//", "/*"],
    Language.CPP: ["//", "/*"],
}

# Regex to strip comment markers
_COMMENT_STRIP_RE = re.compile(r"^[\s/\*#!]+")


def _first_docstring(source: str, lang: Language) -> str | None:
    """Extract a module-level docstring from source text."""
    if lang == Language.PYTHON:
        m = _PY_DOCSTRING_RE.match(source)
        if m:
            text = (m.group(1) or m.group(2) or "").strip()
            # Take just the first paragraph (up to first blank line)
            paragraph = text.split("\n\n")[0].strip()
            # Collapse internal newlines into a single line
            one_line = " ".join(paragraph.splitlines()).strip()
            if one_line:
                return one_line[:200]
    return None


def _first_comment_block(lines: list[str], lang: Language) -> str | None:
    """Extract the first contiguous comment block from the top of the file."""
    prefixes = _COMMENT_PREFIXES.get(lang, [])
    if not prefixes:
        return None

    collected: list[str] = []
    in_block = False

    for raw_line in lines[:30]:  # only look at first 30 lines
        line = raw_line.strip()
        if not line:
            if in_block:
                break
            continue

        is_comment = any(line.startswith(p) for p in prefixes)
        if is_comment:
            in_block = True
            cleaned = _COMMENT_STRIP_RE.sub("", line).strip()
            if cleaned:
                collected.append(cleaned)
        else:
            if in_block:
                break

    if collected:
        return " ".join(collected)[:200]
    return None


def _synthesise_purpose(
    n_classes: int, n_functions: int, n_imports: int, lang: Language
) -> str:
    """Fall-back: generate a plain-English description from symbol counts."""
    parts: list[str] = []
    if n_classes:
        parts.append(f"{n_classes} class{'es' if n_classes > 1 else ''}")
    if n_functions:
        parts.append(f"{n_functions} function{'s' if n_functions > 1 else ''}")
    if not parts:
        return f"{lang.value.capitalize()} source file."
    return f"{lang.value.capitalize()} module defining {', '.join(parts)}."


# ---------------------------------------------------------------------------
# LOC counting
# ---------------------------------------------------------------------------


def _count_loc(source: str, lang: Language) -> tuple[int, int]:
    """
    Return (loc, total_lines).

    *loc* counts non-blank, non-comment lines.
    """
    comment_prefixes = _COMMENT_PREFIXES.get(lang, [])
    total = 0
    loc = 0
    for line in source.splitlines():
        total += 1
        stripped = line.strip()
        if not stripped:
            continue
        if comment_prefixes and any(stripped.startswith(p) for p in comment_prefixes):
            continue
        loc += 1
    return loc, total


# ---------------------------------------------------------------------------
# Dependency extraction from import symbols
# ---------------------------------------------------------------------------


def _extract_dependencies(symbols: list[Symbol]) -> list[str]:
    """Return unique module/package names from all import symbols."""
    seen: set[str] = set()
    deps: list[str] = []
    for sym in symbols:
        if sym.kind == SymbolKind.IMPORT:
            # Normalise: take only the top-level package name
            raw = sym.name.strip()
            top_level = raw.split(".")[0].split("/")[0]
            if top_level and top_level not in seen:
                seen.add(top_level)
                deps.append(top_level)
    return deps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summarize_file(
    path: Path,
    relative_path: str,
    language: Language,
    symbols: list[Symbol],
) -> FileSummary:
    """
    Build a :class:`~ai_swe.indexer.models.FileSummary` for one source file.

    Args:
        path:          Absolute path to the file.
        relative_path: Path relative to the repository root (used as the key).
        language:      Detected language.
        symbols:       Parsed symbols (from :mod:`ai_swe.indexer.parser`).

    Returns:
        A fully-populated ``FileSummary``.
    """
    # Read source once
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""

    lines = source.splitlines()
    loc, total_lines = _count_loc(source, language)

    # --- Classify symbols ---------------------------------------------------
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    interfaces = [s for s in symbols if s.kind == SymbolKind.INTERFACE]
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    functions = [s for s in symbols if s.kind == SymbolKind.FUNCTION]

    # Top-level classes (no parent)
    top_classes = [c for c in classes if c.parent is None]
    major_classes = [c.name for c in top_classes[:10]] + \
                    [i.name for i in interfaces[:5]]

    # Important functions: non-private, non-dunder
    important_fns = [
        f.name for f in functions
        if not f.name.startswith("_") and not f.name.startswith("__")
    ]
    # Also include public methods
    important_methods = [
        m.name for m in methods
        if not m.name.startswith("_") and not m.name.startswith("__")
    ]
    important_functions = sorted(set(important_fns + important_methods))[:20]

    dependencies = _extract_dependencies(symbols)

    # --- Purpose -----------------------------------------------------------
    purpose = (
        _first_docstring(source, language)
        or _first_comment_block(lines, language)
        or _synthesise_purpose(len(top_classes), len(functions), len(dependencies), language)
    )

    return FileSummary(
        path=relative_path,
        language=language,
        purpose=purpose,
        major_classes=major_classes,
        important_functions=important_functions,
        dependencies=dependencies,
        symbols=symbols,
        loc=loc,
        total_lines=total_lines,
    )

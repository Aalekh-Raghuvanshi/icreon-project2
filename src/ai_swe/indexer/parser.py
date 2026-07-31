"""
Tree-sitter AST parser for extracting structured symbols from source files.

This module wraps tree-sitter 0.26's ``Parser`` + manual AST traversal to
extract:
  - functions / free functions
  - classes
  - methods (functions nested inside a class body)
  - imports / from-imports
  - exports (JS/TS)
  - interfaces (TypeScript / Java)
  - inheritance base-class names
  - interface implementations

**API compatibility note**: tree-sitter 0.26 exposes ``Query`` objects but
their ``captures()`` / ``matches()`` methods were removed from this version.
We use manual recursive tree traversal (``_walk``) instead, which is more
robust across tree-sitter versions and avoids S-expression query syntax
differences between language grammars.

Usage::

    from pathlib import Path
    from ai_swe.indexer.models import Language
    from ai_swe.indexer.parser import parse_file

    symbols = parse_file(Path("src/main.py"), Language.PYTHON)
    for sym in symbols:
        print(sym.kind, sym.name, sym.line_start)
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path

from tree_sitter import Language as TSLanguage
from tree_sitter import Node, Parser

from ai_swe.indexer.models import Language, Symbol, SymbolKind

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grammar loaders — lazy-loaded and cached per language
# ---------------------------------------------------------------------------


@cache
def _get_ts_language(lang: Language) -> TSLanguage | None:
    """Return a cached tree-sitter Language object, or None if unavailable."""
    try:
        if lang == Language.PYTHON:
            import tree_sitter_python as m_python
            return TSLanguage(m_python.language())
        elif lang == Language.JAVASCRIPT:
            import tree_sitter_javascript as m_javascript
            return TSLanguage(m_javascript.language())
        elif lang == Language.TYPESCRIPT:
            import tree_sitter_typescript as m_typescript
            return TSLanguage(m_typescript.language_typescript())
        elif lang == Language.JAVA:
            import tree_sitter_java as m_java
            return TSLanguage(m_java.language())
        elif lang == Language.GO:
            import tree_sitter_go as m_go
            return TSLanguage(m_go.language())
        elif lang == Language.CPP:
            import tree_sitter_cpp as m_cpp
            return TSLanguage(m_cpp.language())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load tree-sitter grammar for %s: %s", lang, exc)
    return None


# ---------------------------------------------------------------------------
# Node-type sets per language
# ---------------------------------------------------------------------------

# Mapping: language -> set of AST node types that represent a "function"
_FUNCTION_TYPES: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"function_definition"}),
    Language.JAVASCRIPT: frozenset(
        {"function_declaration", "function_expression", "arrow_function",
         "generator_function_declaration"}
    ),
    Language.TYPESCRIPT: frozenset(
        {"function_declaration", "function_expression", "arrow_function",
         "generator_function_declaration"}
    ),
    Language.JAVA: frozenset({"method_declaration", "constructor_declaration"}),
    Language.GO: frozenset({"function_declaration"}),
    Language.CPP: frozenset({"function_definition"}),
}

# Mapping: language -> set of AST node types that represent a "method"
# (function nested inside a class body)
_METHOD_TYPES: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"function_definition"}),
    Language.JAVASCRIPT: frozenset({"method_definition"}),
    Language.TYPESCRIPT: frozenset({"method_definition", "method_signature"}),
    Language.JAVA: frozenset({"method_declaration", "constructor_declaration"}),
    Language.GO: frozenset({"method_declaration"}),
    Language.CPP: frozenset({"function_definition"}),
}

_CLASS_TYPES: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"class_definition"}),
    Language.JAVASCRIPT: frozenset({"class_declaration", "class_expression"}),
    Language.TYPESCRIPT: frozenset({"class_declaration", "class_expression"}),
    Language.JAVA: frozenset({"class_declaration", "enum_declaration"}),
    Language.GO: frozenset({"type_declaration"}),  # Go uses struct types
    Language.CPP: frozenset({"class_specifier", "struct_specifier"}),
}

_INTERFACE_TYPES: dict[Language, frozenset[str]] = {
    Language.TYPESCRIPT: frozenset({"interface_declaration"}),
    Language.JAVA: frozenset({"interface_declaration"}),
    Language.GO: frozenset({"type_declaration"}),
    Language.CPP: frozenset(),
    Language.PYTHON: frozenset(),
    Language.JAVASCRIPT: frozenset(),
}

_IMPORT_TYPES: dict[Language, frozenset[str]] = {
    Language.PYTHON: frozenset({"import_statement", "import_from_statement"}),
    Language.JAVASCRIPT: frozenset({"import_statement"}),
    Language.TYPESCRIPT: frozenset({"import_statement"}),
    Language.JAVA: frozenset({"import_declaration"}),
    Language.GO: frozenset({"import_declaration", "import_spec"}),
    Language.CPP: frozenset({"preproc_include"}),
}

_EXPORT_TYPES: dict[Language, frozenset[str]] = {
    Language.JAVASCRIPT: frozenset({"export_statement"}),
    Language.TYPESCRIPT: frozenset({"export_statement"}),
    Language.PYTHON: frozenset(),
    Language.JAVA: frozenset(),
    Language.GO: frozenset(),
    Language.CPP: frozenset(),
}

# ---------------------------------------------------------------------------
# Name-extraction helpers
# ---------------------------------------------------------------------------


def _child_of_type(node: Node, *types: str) -> Node | None:
    """Return the first direct child with any of the given types."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _node_text(node: Node) -> str:
    """Return the decoded text for a node."""
    return node.text.decode("utf-8", errors="replace") if node.text else ""


def _get_name(node: Node, lang: Language) -> str:
    """Best-effort name extraction from a definition node."""
    # Most languages put the name in a child of type "identifier"
    name_node = _child_of_type(node, "identifier", "name", "type_identifier",
                                "field_identifier", "property_identifier")
    if name_node:
        return _node_text(name_node)
    # Go function declarations have a "name" field
    fn_name = node.child_by_field_name("name")
    if fn_name:
        return _node_text(fn_name)
    return "<anonymous>"


def _extract_import_name(node: Node, lang: Language) -> str:
    """Extract the module/package name from an import node."""
    if lang == Language.PYTHON:
        # import_statement  -> dotted_name | aliased_import
        # import_from_statement -> "from" dotted_name "import" ...
        from_name = node.child_by_field_name("module_name")
        if from_name:
            return _node_text(from_name)
        for child in node.children:
            if child.type in ("dotted_name", "relative_import"):
                return _node_text(child)
    elif lang in (Language.JAVASCRIPT, Language.TYPESCRIPT):
        # import ... from "module"
        src = node.child_by_field_name("source")
        if src:
            return _node_text(src).strip("'\"")
    elif lang == Language.JAVA:
        for child in node.children:
            if child.type == "scoped_identifier":
                return _node_text(child)
    elif lang == Language.GO:
        path = node.child_by_field_name("path")
        if path:
            return _node_text(path).strip('"')
        for child in node.children:
            if child.type == "interpreted_string_literal":
                return _node_text(child).strip('"')
    elif lang == Language.CPP:
        for child in node.children:
            if child.type in ("string_literal", "system_lib_string"):
                return _node_text(child).strip('"<>')
    return _node_text(node)[:80]


# ---------------------------------------------------------------------------
# Recursive walker
# ---------------------------------------------------------------------------


class _SymbolCollector:
    """
    Collects symbols by recursively walking a tree-sitter AST.

    Tracks the current enclosing class so methods can be tagged with a parent.
    """

    def __init__(self, lang: Language) -> None:
        self.lang = lang
        self.symbols: list[Symbol] = []
        self._class_stack: list[str] = []  # stack of enclosing class names

    def _is_inside_class(self) -> bool:
        return bool(self._class_stack)

    def _current_class(self) -> str | None:
        return self._class_stack[-1] if self._class_stack else None

    def visit(self, node: Node) -> None:
        ntype = node.type
        lang = self.lang

        # ---- class / interface -------------------------------------------
        if ntype in _CLASS_TYPES.get(lang, frozenset()):
            name = _get_name(node, lang)
            sym = Symbol(
                name=name,
                kind=SymbolKind.CLASS,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
                parent=self._current_class(),
            )
            self.symbols.append(sym)
            self._class_stack.append(name)
            for child in node.children:
                self.visit(child)
            self._class_stack.pop()
            return

        if ntype in _INTERFACE_TYPES.get(lang, frozenset()):
            name = _get_name(node, lang)
            sym = Symbol(
                name=name,
                kind=SymbolKind.INTERFACE,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
                parent=self._current_class(),
            )
            self.symbols.append(sym)
            for child in node.children:
                self.visit(child)
            return

        # ---- function / method --------------------------------------------
        if ntype in _FUNCTION_TYPES.get(lang, frozenset()) or \
                ntype in _METHOD_TYPES.get(lang, frozenset()):
            name = _get_name(node, lang)
            kind = (
                SymbolKind.METHOD if self._is_inside_class() else SymbolKind.FUNCTION
            )
            sym = Symbol(
                name=name,
                kind=kind,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
                parent=self._current_class(),
            )
            self.symbols.append(sym)
            # Recurse into the body for nested functions/classes
            body = node.child_by_field_name("body") or \
                   _child_of_type(node, "block", "statement_block", "compound_statement")
            if body:
                for child in body.children:
                    self.visit(child)
            return

        # ---- imports -------------------------------------------------------
        if ntype in _IMPORT_TYPES.get(lang, frozenset()):
            name = _extract_import_name(node, lang)
            sym = Symbol(
                name=name,
                kind=SymbolKind.IMPORT,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
            )
            self.symbols.append(sym)
            return

        # ---- exports (JS/TS) -----------------------------------------------
        if ntype in _EXPORT_TYPES.get(lang, frozenset()):
            # The exported name is usually in a nested declaration
            inner = None
            for child in node.children:
                if child.type in ("function_declaration", "class_declaration",
                                  "variable_declaration", "lexical_declaration"):
                    inner = child
                    break
            name = _get_name(inner, lang) if inner else "<export>"
            sym = Symbol(
                name=name,
                kind=SymbolKind.EXPORT,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
            )
            self.symbols.append(sym)
            if inner:
                self.visit(inner)
            return

        # ---- default: recurse into children --------------------------------
        for child in node.children:
            self.visit(child)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_file(path: Path, language: Language) -> list[Symbol]:
    """
    Parse *path* using Tree-sitter and return all extracted symbols.

    Returns an empty list on any error (binary files, encoding issues,
    unsupported language, etc.) so callers never need to handle exceptions.

    Args:
        path:     Absolute path to the source file.
        language: Detected language for the file.

    Returns:
        List of :class:`~ai_swe.indexer.models.Symbol` objects.
    """
    if language == Language.UNKNOWN:
        return []

    ts_lang = _get_ts_language(language)
    if ts_lang is None:
        return []

    try:
        source = path.read_bytes()
    except OSError as exc:
        logger.debug("Could not read %s: %s", path, exc)
        return []

    try:
        parser = Parser(ts_lang)
        tree = parser.parse(source)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tree-sitter parse error for %s: %s", path, exc)
        return []

    collector = _SymbolCollector(language)
    try:
        collector.visit(tree.root_node)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Symbol collection error for %s: %s", path, exc)
        return []

    return collector.symbols

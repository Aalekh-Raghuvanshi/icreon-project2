"""Tests for `ai_swe.mcp.filesystem_tools` -- pure logic, no live MCP server needed."""

from ai_swe.mcp.filesystem_tools import _flatten_tree
from ai_swe.state import FileRecord


def test_flatten_tree_flat_file() -> None:
    out: list[FileRecord] = []
    _flatten_tree({"name": "README.md", "type": "file"}, "", out)
    assert out == [FileRecord(path="README.md", is_dir=False)]


def test_flatten_tree_nested_directory() -> None:
    tree = {
        "name": "src",
        "type": "directory",
        "children": [
            {"name": "main.py", "type": "file"},
            {
                "name": "utils",
                "type": "directory",
                "children": [{"name": "helpers.py", "type": "file"}],
            },
        ],
    }
    out: list[FileRecord] = []
    _flatten_tree(tree, "", out)

    paths = {record.path: record.is_dir for record in out}
    assert paths == {
        "src": True,
        "src/main.py": False,
        "src/utils": True,
        "src/utils/helpers.py": False,
    }


def test_flatten_tree_empty_children_list() -> None:
    out: list[FileRecord] = []
    _flatten_tree({"name": "empty_dir", "type": "directory", "children": []}, "", out)
    assert out == [FileRecord(path="empty_dir", is_dir=True)]

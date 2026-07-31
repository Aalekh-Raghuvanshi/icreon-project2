"""
Tests for `ai_swe.agents.edit_engine.EditEngine`.

All tests run with `orchestrator=None`, so edits are applied directly via
`pathlib` against a `tmp_path` fixture -- no MCP server, no network, fully
offline.
"""

from __future__ import annotations

import pytest

from ai_swe.agents.coder_models import EditAction, FileEdit, SearchReplaceBlock
from ai_swe.agents.edit_engine import EditApplyError, EditEngine, SyntaxVerificationError


class TestCreateFile:
    @pytest.mark.asyncio
    async def test_create_new_python_file(self, tmp_path):
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="new_module.py",
            action=EditAction.CREATE,
            new_content="x = 1\n",
            description="Add new module.",
        )

        patches = await engine.apply_changeset([edit])

        assert (tmp_path / "new_module.py").read_text(encoding="utf-8") == "x = 1\n"
        assert len(patches) == 1
        assert patches[0].file_path == "new_module.py"
        assert "+x = 1" in patches[0].diff
        assert patches[0].description == "Add new module."

    @pytest.mark.asyncio
    async def test_create_in_nested_directory(self, tmp_path):
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="pkg/sub/new_module.py",
            action=EditAction.CREATE,
            new_content="y = 2\n",
        )

        await engine.apply_changeset([edit])

        assert (tmp_path / "pkg" / "sub" / "new_module.py").read_text(encoding="utf-8") == "y = 2\n"


class TestModifyFile:
    @pytest.mark.asyncio
    async def test_modify_with_full_content(self, tmp_path):
        (tmp_path / "existing.py").write_text("x = 1\n", encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="existing.py",
            action=EditAction.MODIFY,
            new_content="x = 2\n",
        )

        patches = await engine.apply_changeset([edit])

        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "x = 2\n"
        assert "-x = 1" in patches[0].diff
        assert "+x = 2" in patches[0].diff

    @pytest.mark.asyncio
    async def test_modify_with_search_replace(self, tmp_path):
        (tmp_path / "existing.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="existing.py",
            action=EditAction.MODIFY,
            search_replace=[SearchReplaceBlock(search="return 'hi'", replace="return 'hello'")],
        )

        await engine.apply_changeset([edit])

        assert (
            tmp_path / "existing.py"
        ).read_text(encoding="utf-8") == "def greet():\n    return 'hello'\n"

    @pytest.mark.asyncio
    async def test_search_replace_missing_text_raises_and_rolls_back(self, tmp_path):
        original = "def greet():\n    return 'hi'\n"
        (tmp_path / "existing.py").write_text(original, encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="existing.py",
            action=EditAction.MODIFY,
            search_replace=[SearchReplaceBlock(search="does not exist", replace="whatever")],
        )

        with pytest.raises(EditApplyError):
            await engine.apply_changeset([edit])

        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == original


class TestDeleteFile:
    @pytest.mark.asyncio
    async def test_delete_existing_file(self, tmp_path):
        (tmp_path / "gone.py").write_text("x = 1\n", encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(file_path="gone.py", action=EditAction.DELETE)

        patches = await engine.apply_changeset([edit])

        assert not (tmp_path / "gone.py").exists()
        assert "-x = 1" in patches[0].diff


class TestSyntaxVerificationAndRollback:
    @pytest.mark.asyncio
    async def test_broken_python_syntax_rolls_back_and_leaves_new_file_absent(self, tmp_path):
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="broken.py",
            action=EditAction.CREATE,
            new_content="def f(:\n    pass\n",
        )

        with pytest.raises(SyntaxVerificationError):
            await engine.apply_changeset([edit])

        assert not (tmp_path / "broken.py").exists()

    @pytest.mark.asyncio
    async def test_broken_edit_to_existing_file_restores_original(self, tmp_path):
        original = "x = 1\n"
        (tmp_path / "existing.py").write_text(original, encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="existing.py",
            action=EditAction.MODIFY,
            new_content="def f(:\n    pass\n",
        )

        with pytest.raises(SyntaxVerificationError):
            await engine.apply_changeset([edit])

        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_second_broken_edit_rolls_back_first_good_edit_too(self, tmp_path):
        """A multi-file changeset where edit 2 is broken must roll back edit 1 as well."""
        (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)

        good_edit = FileEdit(
            file_path="a.py",
            action=EditAction.MODIFY,
            new_content="a = 2\n",
        )
        broken_edit = FileEdit(
            file_path="b.py",
            action=EditAction.CREATE,
            new_content="def f(:\n    pass\n",
        )

        with pytest.raises(SyntaxVerificationError):
            await engine.apply_changeset([good_edit, broken_edit])

        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 1\n"
        assert not (tmp_path / "b.py").exists()

    @pytest.mark.asyncio
    async def test_valid_javascript_passes_verification(self, tmp_path):
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="script.js",
            action=EditAction.CREATE,
            new_content="function greet() {\n  return 'hi';\n}\n",
        )

        await engine.apply_changeset([edit])

        assert (tmp_path / "script.js").exists()

    @pytest.mark.asyncio
    async def test_broken_javascript_rolls_back(self, tmp_path):
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(
            file_path="script.js",
            action=EditAction.CREATE,
            new_content="function greet( {\n  return 'hi'\n",
        )

        with pytest.raises(SyntaxVerificationError):
            await engine.apply_changeset([edit])

        assert not (tmp_path / "script.js").exists()


class TestRollbackMethod:
    @pytest.mark.asyncio
    async def test_explicit_rollback_restores_modified_file(self, tmp_path):
        (tmp_path / "existing.py").write_text("x = 1\n", encoding="utf-8")
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(file_path="existing.py", action=EditAction.MODIFY, new_content="x = 2\n")

        await engine.apply_changeset([edit])
        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "x = 2\n"

        await engine.rollback()
        assert (tmp_path / "existing.py").read_text(encoding="utf-8") == "x = 1\n"

    @pytest.mark.asyncio
    async def test_explicit_rollback_removes_created_file(self, tmp_path):
        engine = EditEngine(tmp_path, orchestrator=None)
        edit = FileEdit(file_path="new.py", action=EditAction.CREATE, new_content="x = 1\n")

        await engine.apply_changeset([edit])
        assert (tmp_path / "new.py").exists()

        await engine.rollback()
        assert not (tmp_path / "new.py").exists()

"""Obsidian notes as Jarvis memory.

Two halves, because the feature is only real if both work:

  * ``jarvis/memory/obsidian.py`` - the vault itself: parsing frontmatter, tags
    and ``[[wikilinks]]``, searching, writing, and refusing every name that
    would leave the vault;
  * the seams that consume it - indexing into the existing Vector Store and
    Knowledge Graph, retrieval through the same RAG block the agent loop already
    builds, and the ``notes`` action.

The load-bearing claim these tests hold the code to is the last section: with no
vault configured, nothing about memory changes at all.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jarvis.config import Config, load_config
from jarvis.memory.manager import MemoryManager
from jarvis.memory.obsidian import ObsidianVault, VaultUnavailable, get_vault, vault_path_setting
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME, to_json_schema


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """A small vault: two notes, one link, and Obsidian's own hidden config."""
    root = tmp_path / "Vault"
    (root / ".obsidian").mkdir(parents=True)
    (root / "Projects").mkdir()
    (root / "Projects" / "Roadmap.md").write_text(
        "---\ntags: [work, planning]\naliases: plan\n---\n\n# Roadmap\n\n"
        "Ship the memory work before the vault polish. See [[Budget]].\n",
        encoding="utf-8",
    )
    (root / "Budget.md").write_text(
        "# Budget\n\nThe Q3 budget review is Thursday.\n", encoding="utf-8"
    )
    (root / ".obsidian" / "workspace.md").write_text(
        "# Obsidian internals\n", encoding="utf-8"
    )
    (root / "shopping-list.txt").write_text("milk\n", encoding="utf-8")
    return root


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch) -> MemoryManager:
    """A manager on its own database, used by the registry path too.

    The ``notes`` action resolves its manager through ``get_memory_manager()``,
    so redirecting that is what keeps these tests off the shared state
    directory - and keeps one test's indexed notes out of another's searches.
    """
    from jarvis.memory import manager as manager_module

    instance = MemoryManager(
        db_path=tmp_path / "jarvis_memory.db", memory_path=tmp_path / "memory.txt"
    )
    monkeypatch.setattr(manager_module, "get_memory_manager", lambda *a, **k: instance)
    return instance


@pytest.fixture()
def configured(monkeypatch, vault_dir: Path) -> Path:
    """Point the integration at the fixture vault for the duration of a test."""
    monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT", str(vault_dir))
    return vault_dir


def _run(args: dict, cfg: Config | None = None):
    """Dispatch through the registry, exactly as the agent loop does."""
    return registry.execute("notes", args, None, cfg)


# --------------------------------------------------------------------------- #
# the vault itself
# --------------------------------------------------------------------------- #

def test_only_markdown_outside_hidden_folders_is_a_note(vault_dir: Path):
    vault = ObsidianVault(vault_dir)

    paths = [path.relative_to(vault_dir).as_posix() for path in vault.note_paths()]

    assert paths == ["Budget.md", "Projects/Roadmap.md"]
    assert not any("workspace" in path for path in paths), ".obsidian/ must stay out of the index"
    assert not any(path.endswith(".txt") for path in paths)


def test_frontmatter_tags_headings_and_links_are_parsed(vault_dir: Path):
    note = ObsidianVault(vault_dir).read("Projects/Roadmap")

    assert note.title == "Roadmap"
    assert note.tags == ("work", "planning")
    assert note.aliases == ("plan",)
    assert note.links == ("Budget",)
    assert note.headings == ("Roadmap",)
    # The H1 that only repeats the title must not be repeated into memory.
    assert note.memory_content().startswith("[note] Roadmap (tags: work, planning) — Ship")
    assert "[[Budget]]" in note.memory_content()


def test_note_names_may_not_leave_the_vault(vault_dir: Path, configured: Path, tmp_path: Path):
    vault = ObsidianVault(vault_dir)
    escapes = ["../outside", "..\\outside", "C:/Windows/System32/evil", "/etc/passwd",
               "\\\\server\\share\\evil", "Sub/../../escape"]

    for name in escapes:
        with pytest.raises(ValueError):
            vault.read(name)
        with pytest.raises(ValueError):
            vault.append(name, "written by a test")

    assert not (tmp_path / "outside.md").exists()
    assert not (tmp_path.parent / "outside.md").exists()

    refused = _run({"action": "append", "title": "../outside", "content": "x"}, Config())
    assert refused.ok is False
    assert "leaves the vault" in refused.message or "absolute path" in refused.message


def test_missing_note_is_reported_rather_than_created(vault_dir: Path):
    with pytest.raises(FileNotFoundError):
        ObsidianVault(vault_dir).read("Projects/DoesNotExist")


# --------------------------------------------------------------------------- #
# notes as memory
# --------------------------------------------------------------------------- #

def test_notes_are_indexed_and_retrievable_through_rag(manager: MemoryManager, configured: Path):
    indexed, links = manager.sync_obsidian(Config())

    assert indexed == 2
    assert links >= 1
    records = manager.vector_store.get_all(doc_type="note")
    assert len(records) == 2
    assert all(record.metadata["source"] == "obsidian" for record in records)

    hits = manager.search_semantic("Q3 budget review", top_k=3)
    assert any("Thursday" in record.content for record, _score in hits)

    context = manager.get_rag_context("what did I write about the budget review?")
    assert "RELEVANT LONG-TERM MEMORY" in context
    assert "Thursday" in context


def test_wikilinks_become_knowledge_graph_relations(manager: MemoryManager, configured: Path):
    manager.sync_obsidian(Config())

    neighborhood = manager.query_graph("Roadmap")

    assert any(
        relation.relation_type == "links_to" and relation.target_name == "Budget"
        for relation in neighborhood["relations"]
    )


def test_reindexing_updates_in_place_and_prunes_deleted_notes(
    manager: MemoryManager, configured: Path, vault_dir: Path
):
    manager.sync_obsidian(Config())
    manager.sync_obsidian(Config())
    assert len(manager.vector_store.get_all(doc_type="note")) == 2, "re-index must not duplicate"

    (vault_dir / "Budget.md").unlink()
    manager.sync_obsidian(Config())

    remaining = manager.vector_store.get_all(doc_type="note")
    assert len(remaining) == 1
    assert remaining[0].metadata["path"] == "Projects/Roadmap.md"


def test_indexed_notes_survive_a_restart(manager: MemoryManager, configured: Path, tmp_path: Path):
    manager.sync_obsidian(Config())

    reopened = MemoryManager(db_path=tmp_path / "jarvis_memory.db", memory_path=tmp_path / "memory.txt")

    assert len(reopened.vector_store.get_all(doc_type="note")) == 2


def test_the_first_rag_read_indexes_the_vault_once(manager: MemoryManager, configured: Path):
    manager.get_rag_context("anything")
    assert len(manager.vector_store.get_all(doc_type="note")) == 2

    # A second read must not walk the vault again, and a note added afterwards
    # reaches memory through the explicit index action instead.
    (Path(configured) / "Later.md").write_text("# Later\n\nA new note.\n", encoding="utf-8")
    manager.get_rag_context("anything")
    assert len(manager.vector_store.get_all(doc_type="note")) == 2

    assert _run({"action": "index"}, Config()).ok is True
    assert len(manager.vector_store.get_all(doc_type="note")) == 3


# --------------------------------------------------------------------------- #
# writing back
# --------------------------------------------------------------------------- #

def test_appending_keeps_existing_text_and_refreshes_memory(
    manager: MemoryManager, configured: Path, vault_dir: Path
):
    result = manager.save_obsidian_note(
        "Projects/Roadmap", "Decided: ship memory first.", heading="Decisions", cfg=Config()
    )

    assert result == "Projects/Roadmap.md"
    text = (vault_dir / "Projects" / "Roadmap.md").read_text(encoding="utf-8")
    assert "Ship the memory work before the vault polish" in text
    assert "## Decisions" in text and "ship memory first" in text

    record = next(r for r in manager.vector_store.get_all(doc_type="note")
                  if r.metadata["path"] == "Projects/Roadmap.md")
    assert "ship memory first" in record.content


def test_create_refuses_to_clobber_an_existing_note(manager: MemoryManager, configured: Path):
    assert _run({"action": "create", "title": "Ideas", "content": "First."}, Config()).ok is True

    again = _run({"action": "create", "title": "Ideas", "content": "Second."}, Config())

    assert again.ok is False
    assert "already exists" in again.message


def test_daily_note_appends_under_the_configured_folder(configured: Path, manager: MemoryManager):
    first = _run({"action": "daily", "content": "Reviewed the vault integration."}, Config())
    second = _run({"action": "daily", "content": "Wrote the guide."}, Config())

    assert first.ok and second.ok
    path = Path(configured) / "Journal" / f"{date.today():%Y-%m-%d}.md"
    text = path.read_text(encoding="utf-8")
    assert "- Reviewed the vault integration." in text
    assert "- Wrote the guide." in text, "a second entry must append, not overwrite"


# --------------------------------------------------------------------------- #
# the action surface
# --------------------------------------------------------------------------- #

def test_search_read_and_list_answer_from_the_vault(manager: MemoryManager, configured: Path):
    listed = _run({"action": "list"}, Config())
    assert listed.ok is True
    assert "Projects/Roadmap.md" in listed.message and "Budget.md" in listed.message

    found = _run({"action": "search", "query": "budget"}, Config())
    assert found.ok is True
    assert "Budget.md" in found.message
    assert "Thursday" in found.message

    read = _run({"action": "read", "title": "Budget"}, Config())
    assert read.ok is True
    assert "Note: Budget.md" in read.message and "Thursday" in read.message


def test_notes_action_never_asks_for_a_screenshot(manager: MemoryManager, configured: Path):
    calls = [
        {"action": "list"},
        {"action": "search", "query": "roadmap"},
        {"action": "read", "title": "Budget"},
        {"action": "append", "title": "Budget", "content": "note"},
        {"action": "create", "title": "Fresh", "content": "note"},
        {"action": "daily", "content": "note"},
        {"action": "index"},
        {"action": "status"},
        {"action": "nonsense"},
        {"action": "read"},
    ]

    for args in calls:
        assert _run(args, Config()).needs_observe is False, args


def test_the_action_is_declared_with_its_parameters():
    assert "notes" in ACTIONS_BY_NAME
    entry = next(e for e in to_json_schema() if e["name"] == "notes")
    assert set(entry["parameters"]["properties"]) == {
        "action", "query", "title", "content", "tags", "heading", "limit",
    }
    assert entry["parameters"]["required"] == ["action"]


# --------------------------------------------------------------------------- #
# configuration: off by default, and off means unchanged
# --------------------------------------------------------------------------- #

def test_no_vault_configured_leaves_memory_exactly_as_it_was(manager: MemoryManager, monkeypatch):
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    cfg = Config()

    assert cfg.memory.obsidian_vault == ""
    assert get_vault(cfg) is None
    assert manager.obsidian_vault(cfg) is None
    assert manager.sync_obsidian(cfg) == (0, 0)
    assert manager.ensure_obsidian_index(cfg) == 0

    context = manager.get_rag_context("anything at all")
    assert "[note]" not in context
    assert manager.vector_store.get_all(doc_type="note") == []

    status = _run({"action": "status"}, cfg)
    assert status.ok is True
    assert "obsidian_vault" in status.message

    refused = _run({"action": "list"}, cfg)
    assert refused.ok is False
    assert "memory.obsidian_vault" in refused.message


def test_an_empty_folder_is_usable_and_says_so(manager: MemoryManager, tmp_path: Path,
                                               monkeypatch):
    empty = tmp_path / "EmptyFolder"
    empty.mkdir()
    monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT", str(empty))
    cfg = Config()

    # An empty vault is legitimate - notes may be added later - so it is usable
    # and simply contributes nothing yet.
    assert manager.obsidian_vault(cfg).root == empty.resolve()
    assert manager.sync_obsidian(cfg) == (0, 0)
    assert manager.get_rag_context("anything") is not None
    assert _run({"action": "index"}, cfg).ok is True


def test_a_configured_vault_that_is_missing_is_explained(manager: MemoryManager, tmp_path: Path,
                                                        monkeypatch):
    monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT", str(tmp_path / "NotAVault"))
    cfg = Config()

    with pytest.raises(VaultUnavailable):
        manager.obsidian_vault(cfg)

    failed = _run({"action": "list"}, cfg)
    assert failed.ok is False
    assert "is not a directory" in failed.message

    # A broken vault must never break the loop either: indexing stays silent.
    assert manager.ensure_obsidian_index(cfg) == 0


def test_config_and_environment_both_carry_the_vault(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "memory:\n"
        # Single quotes: a Windows path is full of backslashes, which YAML reads
        # as escapes inside a double-quoted scalar.
        f"  obsidian_vault: '{tmp_path / 'FromYaml'}'\n"
        "  obsidian_daily_folder: \"Daily\"\n",
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.memory.obsidian_vault == str(tmp_path / "FromYaml")
    assert cfg.memory.obsidian_daily_folder == "Daily"

    monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT", str(tmp_path / "FromEnv"))
    env_cfg = load_config(config_path)
    assert vault_path_setting(env_cfg) == str(tmp_path / "FromEnv"), "the environment wins"

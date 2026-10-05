# Obsidian vault as Jarvis memory

Jarvis can use your Obsidian vault as both a memory **source** and a **sink**:
notes become retrievable long-term memory, and what Jarvis works out can be
written back into the notes you actually read. It works directly on the folder,
so there is no plugin, no Local REST API and no new dependency - a vault is just
markdown.

The feature is **off until you name a vault**, and with no vault configured the
memory subsystem behaves exactly as before.

## 1. Point Jarvis at the vault

`config.yaml`, next to the other sections:

```yaml
memory:
  # Folder containing your notes (the one with .obsidian/ inside). Empty = off.
  obsidian_vault: "C:/Users/You/Documents/MyVault"
  # Folder for daily notes, relative to the vault root.
  obsidian_daily_folder: "Journal"
```

Or without editing the file (the environment wins when both are set):

```powershell
$env:JARVIS_OBSIDIAN_VAULT = "C:/Users/You/Documents/MyVault"
```

## 2. Reading: notes become memory

On the first memory read of a process, the vault is indexed **once**. Every note
is filed into the existing Vector Store as a `note` record and every
`[[wikilink]]` becomes a `links_to` relation in the Knowledge Graph. Nothing
about retrieval changed - the notes simply became candidates, so they arrive in
the same RAG block the agent loop already builds.

Asking the model directly, or through the `notes` action:

```text
notes action=search query="pricing decision"   # title/tag/heading/text ranking
notes action=read  title="Projects/Roadmap"    # one note, in full
notes action=list                              # what is in the vault
notes action=index                             # re-index now (after editing files)
notes action=status                            # path, note count, indexed count
```

Search here is keyword ranking, not embeddings (title ×10, tags ×6, headings ×4,
body ×1), so it answers without a model call. Re-indexing updates changed notes
in place and prunes notes you deleted, so the index follows the vault.

Each note contributes at most 1200 characters to memory, and a vault is read up
to 5000 notes deep - long notes do not get to consume the prompt budget.

## 3. Writing: memory lands in your notes

```text
notes action=append title="Projects/Roadmap" content="Decided: ship memory first." heading=Decisions
notes action=create title="Ideas/Launch"     content="..." tags="product,launch"
notes action=daily  content="Reviewed the vault integration."
```

* `append` (default) never rewrites existing text; it creates the note if needed.
* `create` refuses to overwrite a note that already exists.
* `daily` appends one bullet to today's note in `obsidian_daily_folder`.

A write re-indexes that one note, so the new text is retrievable immediately.

## 4. What it will not do

* Note names are vault-relative. `../notes`, an absolute path, a drive letter or
  a UNC path are all refused - a name can never resolve outside the vault root.
* Hidden directories are never notes, so `.obsidian/` (Obsidian's own config and
  plugin cache) is never read, indexed or written.
* Writes go through a temporary sibling file and an atomic replace, so an
  interrupted write cannot truncate a note.
* Nothing is deleted from the vault, ever: pruning removes memory records, not
  files.

## 5. Testing checklist

- [ ] `notes action=status` reports your vault path and a note count above zero.
- [ ] `notes action=list` shows notes you know are in the vault, and no
      `.obsidian` files.
- [ ] `notes action=search query=<a phrase from a real note>` returns that note.
- [ ] Ask a question whose answer is only in your notes; the reply or the logs
      show the note arrived as memory.
- [ ] `notes action=append title=<a scratch note> content="test"` appears in
      Obsidian after a sync (Obsidian picks up external edits automatically).
- [ ] `notes action=read title="../escape"` is refused with a clear message.
- [ ] With `obsidian_vault: ""`, `python -m pytest -q tests/test_memory_rag.py`
      still passes - proof the feature is inert when unused.
- [ ] `python -m pytest -q tests/test_obsidian_memory.py` passes.

## Code map

| Piece | File |
| --- | --- |
| Vault reader, writer, indexer | `jarvis/memory/obsidian.py` |
| Indexing and write-back | `jarvis/memory/manager.py` (`sync_obsidian`, `save_obsidian_note`) |
| Config | `jarvis/config.py` (`MemoryConfig`) |
| Action declaration | `jarvis/tools/schema.py` (`notes`) |
| Action handler | `jarvis/tools/registry.py` (`_h_notes`) |
| Tests | `tests/test_obsidian_memory.py` |

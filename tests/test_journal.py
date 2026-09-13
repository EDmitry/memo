from __future__ import annotations

from memo import journal

from conftest import make_transcript


def test_entry_format():
    transcript = make_transcript("2026-09-12_170312_000", "Refactor the pipeline.", 42)
    assert journal.entry(transcript) == (
        "## 17:03 · 0:42\n\nRefactor the pipeline. ^memo-2026-09-12-170312-000\n"
    )


def test_entry_collapses_whitespace():
    transcript = make_transcript("2026-09-12_170312_000", "  one\n two   three\n", 65)
    assert journal.entry(transcript) == (
        "## 17:03 · 1:05\n\none two three ^memo-2026-09-12-170312-000\n"
    )


def test_append_matches_render(store):
    first = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)

    journal.append_entry(store, first)
    path = journal.append_entry(store, second)

    expected = journal.render_day("2026-09-12", [first, second])
    assert path.read_text(encoding="utf-8") == expected
    assert expected == (
        "# 2026-09-12\n\n"
        "## 17:03 · 0:42\n\nFirst thought. ^memo-2026-09-12-170312-000\n\n"
        "## 17:45 · 1:05\n\nSecond thought. ^memo-2026-09-12-174501-001\n"
    )


def test_rebuild_is_idempotent_and_matches_append(store):
    transcripts = [
        make_transcript("2026-09-12_170312_000", "First thought.", 42),
        make_transcript("2026-09-12_174501_001", "Second thought.", 65),
        make_transcript("2026-09-13_090000_000", "Next day.", 10),
    ]
    for transcript in transcripts:
        store.write_transcript(transcript)
        journal.append_entry(store, transcript)

    appended = {path.name: path.read_text(encoding="utf-8") for path in store.journal_files()}

    written = journal.rebuild(store)
    assert [path.name for path in written] == ["2026-09-12.md", "2026-09-13.md"]
    rebuilt = {path.name: path.read_text(encoding="utf-8") for path in written}
    assert rebuilt == appended

    journal.rebuild(store)
    assert {path.name: path.read_text(encoding="utf-8") for path in store.journal_files()} == rebuilt


def test_rebuild_repairs_damaged_entries(store):
    transcript = make_transcript("2026-09-12_170312_000", "Only thought.", 42)
    store.write_transcript(transcript)
    store.journal_path("2026-09-12").write_text(
        "# 2026-09-12\n\n## 17:03 · 0:42\n\ngarbage\n", encoding="utf-8"
    )

    journal.rebuild(store)
    assert store.journal_path("2026-09-12").read_text(encoding="utf-8") == journal.render_day(
        "2026-09-12", [transcript]
    )


def test_claude_md_written_once(store):
    assert journal.ensure_claude_md(store) is True
    store.claude_md.write_text("edited by hand\n", encoding="utf-8")
    assert journal.ensure_claude_md(store) is False
    assert store.claude_md.read_text(encoding="utf-8") == "edited by hand\n"


# ------------------------------------------------- journal_dir and templates

FRONTMATTER_HEAD = """\
---
created: 2026-09-12 09:00
modified: 2026-09-12 19:30
subjects:
  - walking
---
# 2026-09-12

A line I typed myself.

"""


def test_rebuild_keeps_the_head_byte_for_byte(store):
    """An Obsidian plugin owns `modified:`; memo owns only the entries."""
    first = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)
    for transcript in (first, second):
        store.write_transcript(transcript)
    path = store.journal_path("2026-09-12")
    path.write_text(FRONTMATTER_HEAD + journal.entry(first) + "\nstale junk\n", encoding="utf-8")

    journal.rebuild(store)

    content = path.read_text(encoding="utf-8")
    assert content == FRONTMATTER_HEAD + journal.render_entries([first, second])
    assert content.startswith(FRONTMATTER_HEAD)
    assert "stale junk" not in content


def test_rebuild_is_idempotent_over_a_head(store):
    transcript = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    store.write_transcript(transcript)
    path = store.journal_path("2026-09-12")
    path.write_text(FRONTMATTER_HEAD, encoding="utf-8")

    journal.rebuild(store)
    once = path.read_text(encoding="utf-8")
    journal.rebuild(store)
    assert path.read_text(encoding="utf-8") == once


def test_rebuild_drops_the_entry_of_a_deleted_transcript(store):
    first = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)
    for transcript in (first, second):
        store.write_transcript(transcript)
        journal.append_entry(store, transcript)

    store.transcript_path(second.name).unlink()
    journal.rebuild(store)

    assert store.journal_path("2026-09-12").read_text(encoding="utf-8") == journal.render_day(
        "2026-09-12", [first]
    )


def test_render_template():
    from datetime import datetime

    text = "---\ncreated: {{date}} {{time}}\nmodified: {{DATE}} {{Time}}\n---\n# {{title}}\n\n"
    rendered = journal.render_template(
        text, date="2026-09-12", when=datetime(2026, 9, 12, 17, 3, 12)
    )
    assert rendered == (
        "---\ncreated: 2026-09-12 17:03\nmodified: 2026-09-12 17:03\n---\n# 2026-09-12\n\n"
    )


def test_render_template_leaves_unknown_placeholders_alone():
    from datetime import datetime

    text = "{{date}} {{unknown}} {{ title }} {{}}"
    rendered = journal.render_template(
        text, date="2026-09-12", when=datetime(2026, 9, 12, 17, 3, 12)
    )
    assert rendered == "2026-09-12 {{unknown}} 2026-09-12 {{}}"


def test_append_uses_the_template_for_a_new_file(isolated_env, monkeypatch):
    from memo.config import load as load_config
    from memo.store import Store

    template = isolated_env / "journal.md"
    template.write_text(
        "---\ncreated: {{date}} {{time}}\nsubjects:\n---\n# {{title}}\n\n", encoding="utf-8"
    )
    monkeypatch.setenv("MEMO_JOURNAL_TEMPLATE", str(template))
    store = Store.from_config(load_config())
    store.ensure_dirs()

    transcript = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    path = journal.append_entry(store, transcript)
    assert path.read_text(encoding="utf-8") == (
        "---\ncreated: 2026-09-12 17:03\nsubjects:\n---\n# 2026-09-12\n\n"
        "## 17:03 · 0:42\n\nFirst thought. ^memo-2026-09-12-170312-000\n"
    )

    # A second memo lands under the same head, and a rebuild agrees.
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)
    journal.append_entry(store, second)
    appended = path.read_text(encoding="utf-8")
    store.write_transcript(transcript)
    store.write_transcript(second)
    journal.rebuild(store)
    assert path.read_text(encoding="utf-8") == appended


def test_a_missing_template_is_a_config_error(isolated_env, monkeypatch):
    import pytest

    from memo.config import ConfigError, load as load_config
    from memo.store import Store

    monkeypatch.setenv("MEMO_JOURNAL_TEMPLATE", str(isolated_env / "no-such-template.md"))
    store = Store.from_config(load_config())
    store.ensure_dirs()
    with pytest.raises(ConfigError):
        journal.append_entry(store, make_transcript("2026-09-12_170312_000", "x", 1))


def test_journals_are_written_to_the_journal_dir(vault, isolated_env, monkeypatch):
    from memo.config import load as load_config
    from memo.store import Store

    store = Store.from_config(load_config())
    store.ensure_dirs()
    transcript = make_transcript("2026-09-12_170312_000", "In the vault.", 42)
    path = journal.append_entry(store, transcript)

    assert path == vault / "Memos" / "2026-09-12.md"
    assert not (store.root / "2026-09-12.md").exists()
    assert path.read_text(encoding="utf-8") == journal.render_day("2026-09-12", [transcript])


def test_claude_md_points_at_the_journal_dir(vault, isolated_env):
    from memo.config import load as load_config
    from memo.store import Store

    store = Store.from_config(load_config())
    assert journal.ensure_claude_md(store) is True
    text = store.claude_md.read_text(encoding="utf-8")
    assert str(vault / "Memos") in text
    assert store.claude_md.parent == store.root


# ------------------------------------------------------- the shared journal


def test_entries_carry_a_block_id_the_parser_recovers(store):
    transcript = make_transcript("2026-09-12_170312_000", "First thought.", 65)
    text = journal.render_day("2026-09-12", [transcript])

    (item,) = journal.parse_day(text, "2026-09-12")
    assert item.block_id == "memo-2026-09-12-170312-000"
    assert (item.time, item.duration_s, item.text) == ("17:03", 65.0, "First thought.")
    assert item.as_transcript("2026-09-12_170312_000").adopted is True


def test_an_adopted_entry_renders_back_to_itself(store):
    transcript = make_transcript("2026-09-12_170312_000", "First thought.", 65)
    rendered = journal.entry(transcript)

    (item,) = journal.parse_day(rendered, "2026-09-12")
    assert journal.entry(item.as_transcript(transcript.name)) == rendered


def test_the_parser_tolerates_hand_edits_and_other_sections():
    text = (
        "---\ntitle: 2026-09-12\n---\n"
        "# 2026-09-12\n\nA paragraph of my own.\n\n"
        "##   17:03  ·  0:42\n\n"
        "   Text I   fixed by hand.  \n\n"
        "^memo-2026-09-12-170312-000\n\n"
        "## 17:45 · 1:05\n\nNo block id here.\n\n"
        "## Other notes\n\nNot a memo.\n"
    )
    first, second = journal.parse_day(text, "2026-09-12")
    assert (first.time, first.text) == ("17:03", "Text I fixed by hand.")
    assert first.block_id == "memo-2026-09-12-170312-000"
    assert (second.time, second.duration_s, second.text) == ("17:45", 65.0, "No block id here.")
    assert second.block_id is None  # older entries simply do not dedupe


def test_adoption_reconstructs_a_transcript_for_another_machines_entry(store):
    theirs = make_transcript("2026-09-12_170312_000", "Their thought.", 42)
    store.journal_path("2026-09-12").write_text(
        journal.render_day("2026-09-12", [theirs]), encoding="utf-8"
    )

    (adopted,) = journal.adopt(store)
    assert adopted.name == "2026-09-12-170312-000"  # no local audio: from the block id
    assert adopted.adopted is True
    assert store.read_transcript(adopted.name).text == "Their thought."

    # Idempotent, and a rebuild reproduces the file byte for byte.
    before = store.journal_path("2026-09-12").read_text(encoding="utf-8")
    assert journal.adopt(store) == []
    journal.rebuild(store)
    assert store.journal_path("2026-09-12").read_text(encoding="utf-8") == before


def test_adoption_names_the_memo_after_local_audio_when_it_has_it(store):
    from conftest import write_wav

    write_wav(store.audio_dir / "2026-09-12_170312_000.wav")
    theirs = make_transcript("2026-09-12_170312_000", "Their thought.", 42)
    store.journal_path("2026-09-12").write_text(
        journal.render_day("2026-09-12", [theirs]), encoding="utf-8"
    )

    (adopted,) = journal.adopt(store)
    assert adopted.name == "2026-09-12_170312_000"
    assert store.untranscribed() == []  # so the audio is never transcribed again


def test_adoption_keeps_a_hand_edit_but_rebuild_reverts_our_own(store):
    """The journal wins for adopted entries; our own transcripts win for ours."""
    mine = make_transcript("2026-09-12_170312_000", "As transcribed.", 42)
    theirs = make_transcript("2026-09-12_174501_001", "Theirs as transcribed.", 65)
    store.write_transcript(mine)
    path = store.journal_path("2026-09-12")
    path.write_text(journal.render_day("2026-09-12", [mine, theirs]), encoding="utf-8")
    journal.adopt(store)

    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("As transcribed.", "Fixed by hand.")
        .replace("Theirs as transcribed.", "Theirs, fixed by hand."),
        encoding="utf-8",
    )
    journal.adopt(store)
    journal.rebuild(store)

    content = path.read_text(encoding="utf-8")
    assert "Theirs, fixed by hand." in content  # adopted: the journal is the record
    assert "As transcribed." in content  # ours: re-rendered from our transcript
    assert "Fixed by hand." not in content


def test_parser_accepts_a_block_id_on_its_own_line():
    """Entries written before the id was attached to the paragraph still parse."""
    from memo.journal import parse_day

    legacy = "# 2026-09-12\n\n## 17:03 · 0:42\n\nFirst thought.\n\n^memo-2026-09-12-170312-000\n"
    entries = parse_day(legacy, "2026-09-12")
    assert [(e.block_id, e.text) for e in entries] == [("memo-2026-09-12-170312-000", "First thought.")]

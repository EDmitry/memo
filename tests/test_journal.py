from __future__ import annotations

from memo import journal

from conftest import make_transcript


def test_entry_format():
    transcript = make_transcript("2026-09-12_170312_000", "Refactor the pipeline.", 42)
    assert journal.entry(transcript) == "## 17:03 · 0:42\n\nRefactor the pipeline.\n"


def test_entry_collapses_whitespace():
    transcript = make_transcript("2026-09-12_170312_000", "  one\n two   three\n", 65)
    assert journal.entry(transcript) == "## 17:03 · 1:05\n\none two three\n"


def test_append_matches_render(store):
    first = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)

    journal.append_entry(store, first)
    path = journal.append_entry(store, second)

    expected = journal.render_day("2026-09-12", [first, second])
    assert path.read_text(encoding="utf-8") == expected
    assert expected == (
        "# 2026-09-12\n\n"
        "## 17:03 · 0:42\n\nFirst thought.\n\n"
        "## 17:45 · 1:05\n\nSecond thought.\n"
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


def test_rebuild_repairs_a_damaged_journal(store):
    transcript = make_transcript("2026-09-12_170312_000", "Only thought.", 42)
    store.write_transcript(transcript)
    store.journal_path("2026-09-12").write_text("garbage\n", encoding="utf-8")

    journal.rebuild(store)
    assert store.journal_path("2026-09-12").read_text(encoding="utf-8") == journal.render_day(
        "2026-09-12", [transcript]
    )


def test_claude_md_written_once(store):
    assert journal.ensure_claude_md(store) is True
    store.claude_md.write_text("edited by hand\n", encoding="utf-8")
    assert journal.ensure_claude_md(store) is False
    assert store.claude_md.read_text(encoding="utf-8") == "edited by hand\n"

from __future__ import annotations

import pytest

from memo import journal, ledger

from conftest import make_transcript

DAY = """\
---
memos:
  - 2026-09-12_170312_000
  - 2026-09-12_174501_001
---
# 2026-09-12

## 17:03 · 0:42

First thought.

## 17:45 · 1:05

Second thought.
"""


def test_entry_is_a_heading_and_a_paragraph():
    transcript = make_transcript("2026-09-12_170312_000", "Refactor the pipeline.", 42)
    assert journal.entry(transcript) == "## 17:03 · 0:42\n\nRefactor the pipeline.\n"


def test_entry_collapses_whitespace():
    transcript = make_transcript("2026-09-12_170312_000", "  one\n two   three\n", 65)
    assert journal.entry(transcript) == "## 17:03 · 1:05\n\none two three\n"


def test_append_writes_the_entry_and_the_ledger_item(store):
    first = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)

    journal.append_entry(store, first)
    path = journal.append_entry(store, second)

    assert path.read_text(encoding="utf-8") == DAY
    assert store.ledger_names() == {"2026-09-12_170312_000", "2026-09-12_174501_001"}


def test_append_leaves_everything_above_it_alone(store):
    """An Obsidian plugin owns `modified:`; a hand edit owns the text it fixed."""
    path = store.journal_path("2026-09-12")
    path.write_text(DAY.replace("First thought.", "Fixed by hand."), encoding="utf-8")

    third = make_transcript("2026-09-12_190000_002", "Third thought.", 10)
    journal.append_entry(store, third)

    content = path.read_text(encoding="utf-8")
    assert "Fixed by hand." in content
    assert content.endswith("## 19:00 · 0:10\n\nThird thought.\n")
    assert ledger.read(content)[-1] == "2026-09-12_190000_002"


def test_claude_md_written_once(store):
    assert journal.ensure_claude_md(store) is True
    store.claude_md.write_text("edited by hand\n", encoding="utf-8")
    assert journal.ensure_claude_md(store) is False
    assert store.claude_md.read_text(encoding="utf-8") == "edited by hand\n"


# ------------------------------------------------------------ append_missing


def test_append_missing_adds_only_what_no_journal_lists(store):
    first = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    second = make_transcript("2026-09-12_174501_001", "Second thought.", 65)
    store.write_transcript(first)
    store.write_transcript(second)
    journal.append_entry(store, first)

    assert [item.name for item in journal.append_missing(store)] == [second.name]
    assert store.journal_path("2026-09-12").read_text(encoding="utf-8") == DAY
    assert journal.append_missing(store) == []


def test_append_missing_never_rewrites_an_existing_entry(store):
    for name, text in [("2026-09-12_170312_000", "First thought."), ("2026-09-12_174501_001", "Second thought.")]:
        store.write_transcript(make_transcript(name, text, 42 if name.endswith("000") else 65))
    path = store.journal_path("2026-09-12")
    edited = DAY.replace("First thought.", "First thought, fixed by hand.").replace(
        "  - 2026-09-12_174501_001\n", ""
    ).replace("\n## 17:45 · 1:05\n\nSecond thought.\n", "")
    path.write_text(edited, encoding="utf-8")

    journal.append_missing(store)

    content = path.read_text(encoding="utf-8")
    assert "First thought, fixed by hand." in content
    assert content.count("## 17:45 · 1:05") == 1  # re-added exactly once
    assert journal.append_missing(store) == []


def test_append_missing_recreates_a_day_file_deleted_by_hand(store):
    transcript = make_transcript("2026-09-12_170312_000", "First thought.", 42)
    store.write_transcript(transcript)
    journal.append_entry(store, transcript)
    store.journal_path("2026-09-12").unlink()

    assert [item.name for item in journal.append_missing(store)] == [transcript.name]
    assert store.journal_path("2026-09-12").read_text(encoding="utf-8") == (
        "---\nmemos:\n  - 2026-09-12_170312_000\n---\n"
        "# 2026-09-12\n\n## 17:03 · 0:42\n\nFirst thought.\n"
    )


# ------------------------------------------------- journal_dir and templates

TEMPLATE = "---\ncreated: {{date}} {{time}}\nmodified: {{date}} {{time}}\nsubjects:\n---\n# {{title}}\n\n"


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


def test_a_new_file_gets_the_template_with_the_ledger_in_its_frontmatter(
    isolated_env, monkeypatch
):
    from memo.config import load as load_config
    from memo.store import Store

    template = isolated_env / "journal.md"
    template.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setenv("MEMO_JOURNAL_TEMPLATE", str(template))
    store = Store.from_config(load_config())
    store.ensure_dirs()

    path = journal.append_entry(store, make_transcript("2026-09-12_170312_000", "First thought.", 42))
    assert path.read_text(encoding="utf-8") == (
        "---\ncreated: 2026-09-12 17:03\nmodified: 2026-09-12 17:03\nsubjects:\n"
        "memos:\n  - 2026-09-12_170312_000\n---\n"
        "# 2026-09-12\n\n## 17:03 · 0:42\n\nFirst thought.\n"
    )

    journal.append_entry(store, make_transcript("2026-09-12_174501_001", "Second thought.", 65))
    content = path.read_text(encoding="utf-8")
    assert "created: 2026-09-12 17:03" in content
    assert ledger.read(content) == ["2026-09-12_170312_000", "2026-09-12_174501_001"]


def test_a_missing_template_is_a_config_error(isolated_env, monkeypatch):
    from memo.config import ConfigError, load as load_config
    from memo.store import Store

    monkeypatch.setenv("MEMO_JOURNAL_TEMPLATE", str(isolated_env / "no-such-template.md"))
    store = Store.from_config(load_config())
    store.ensure_dirs()
    with pytest.raises(ConfigError):
        journal.append_entry(store, make_transcript("2026-09-12_170312_000", "x", 1))


def test_journals_are_written_to_the_journal_dir(vault, isolated_env):
    from memo.config import load as load_config
    from memo.store import Store

    store = Store.from_config(load_config())
    store.ensure_dirs()
    path = journal.append_entry(store, make_transcript("2026-09-12_170312_000", "In the vault.", 42))

    assert path == vault / "Memos" / "2026-09-12.md"
    assert not (store.root / "2026-09-12.md").exists()
    assert ledger.read(path.read_text(encoding="utf-8")) == ["2026-09-12_170312_000"]


def test_claude_md_points_at_the_journal_dir(vault, isolated_env):
    from memo.config import load as load_config
    from memo.store import Store

    store = Store.from_config(load_config())
    assert journal.ensure_claude_md(store) is True
    text = store.claude_md.read_text(encoding="utf-8")
    assert str(vault / "Memos") in text
    assert store.claude_md.parent == store.root


# ------------------------------------------------------- reading them back


def test_parse_day_reads_headings_and_text():
    entries = journal.parse_day(DAY, "2026-09-12")
    assert [(item.time, item.duration_s, item.text) for item in entries] == [
        ("17:03", 42.0, "First thought."),
        ("17:45", 65.0, "Second thought."),
    ]
    assert [item.name for item in entries] == [None, None]  # named from the ledger, not here


def test_the_parser_tolerates_hand_edits_and_other_sections():
    text = (
        "---\ntitle: 2026-09-12\n---\n"
        "# 2026-09-12\n\nA paragraph of my own.\n\n"
        "##   17:03  ·  0:42\n\n"
        "   Text I   fixed by hand.  \n\n"
        "## 17:45 · 1:05\n\nSecond.\n\n"
        "## Other notes\n\nNot a memo.\n"
    )
    first, second = journal.parse_day(text, "2026-09-12")
    assert (first.time, first.text) == ("17:03", "Text I fixed by hand.")
    assert (second.time, second.duration_s, second.text) == ("17:45", 65.0, "Second.")


def test_journal_entries_name_each_memo_from_the_ledger(store):
    store.journal_path("2026-09-12").write_text(DAY, encoding="utf-8")
    store.journal_path("2026-09-11").write_text(
        "---\nmemos:\n  - 2026-09-11_083000_000\n---\n# 2026-09-11\n\n## 08:30 · 0:05\n\nYesterday.\n",
        encoding="utf-8",
    )

    entries = journal.journal_entries(store)
    assert [(item.date, item.time, item.name) for item in entries] == [
        ("2026-09-11", "08:30", "2026-09-11_083000_000"),
        ("2026-09-12", "17:03", "2026-09-12_170312_000"),
        ("2026-09-12", "17:45", "2026-09-12_174501_001"),
    ]


def test_a_ledger_that_no_longer_lines_up_leaves_the_names_out(store):
    """Entries are still readable; only the identity is dropped."""
    store.journal_path("2026-09-12").write_text(
        DAY.replace("  - 2026-09-12_170312_000\n", ""), encoding="utf-8"
    )
    entries = journal.journal_entries(store)
    assert [item.text for item in entries] == ["First thought.", "Second thought."]
    assert [item.name for item in entries] == [None, None]


def test_entries_and_ledger_out_of_order_leave_the_names_out(store):
    store.journal_path("2026-09-12").write_text(
        DAY.replace(
            "  - 2026-09-12_170312_000\n  - 2026-09-12_174501_001\n",
            "  - 2026-09-12_174501_001\n  - 2026-09-12_170312_000\n",
        ),
        encoding="utf-8",
    )
    assert [item.name for item in journal.journal_entries(store)] == [None, None]

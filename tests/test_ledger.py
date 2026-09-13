"""The `memos:` frontmatter property memo appends to and nothing else."""

from __future__ import annotations

import pytest

from memo import ledger

FULL = """\
---
created: 2026-09-12 13:17
modified: 2026-09-12 13:17
subjects:
  - walking
memos:
  - 2026-09-12_131726_000
  - 2026-09-12_131837_000
---
# 2026-09-12

## 13:17 · 0:42

First thought.
"""


def test_reads_the_block_form():
    assert ledger.read(FULL) == ["2026-09-12_131726_000", "2026-09-12_131837_000"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("---\nmemos: [a, b]\n---\n", ["a", "b"]),
        ('---\nmemos: ["a", \'b\']\n---\n', ["a", "b"]),
        ("---\nmemos: []\n---\n", []),
        ("---\nmemos: a\n---\n", ["a"]),  # a lone name, however it got there
        ("---\nmemos:\n- a\n- b\n---\n", ["a", "b"]),  # unindented list
        ("---\nmemos:\n  - a\nsubjects:\n  - b\n---\n", ["a"]),  # stops at the next key
    ],
)
def test_reads_the_shapes_obsidian_and_hands_produce(text, expected):
    assert ledger.read(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# 2026-09-12\n",
        "---\ncreated: 2026-09-12\n---\n",  # frontmatter without the key
        "---\nnever closed\n",  # an unterminated fence is not frontmatter
        "# note\n\n---\nmemos:\n  - a\n---\n",  # not at the top: not frontmatter
    ],
)
def test_no_ledger_reads_as_empty(text):
    assert ledger.read(text) == []


def test_appending_touches_nothing_but_the_list():
    added = ledger.add(FULL, "2026-09-12_140000_002")
    assert added == FULL.replace(
        "  - 2026-09-12_131837_000\n",
        "  - 2026-09-12_131837_000\n  - 2026-09-12_140000_002\n",
    )
    assert ledger.read(added) == [
        "2026-09-12_131726_000",
        "2026-09-12_131837_000",
        "2026-09-12_140000_002",
    ]


def test_appending_a_name_the_list_already_has_changes_nothing():
    assert ledger.add(FULL, "2026-09-12_131726_000") == FULL


def test_the_key_is_created_just_above_the_closing_fence():
    text = "---\ncreated: 2026-09-12 13:17\nsubjects:\n---\n# 2026-09-12\n\nbody\n"
    assert ledger.add(text, "a") == (
        "---\ncreated: 2026-09-12 13:17\nsubjects:\nmemos:\n  - a\n---\n# 2026-09-12\n\nbody\n"
    )


def test_an_empty_key_gets_its_first_item():
    assert ledger.add("---\nmemos:\ntags: []\n---\nx\n", "a") == (
        "---\nmemos:\n  - a\ntags: []\n---\nx\n"
    )


def test_frontmatter_is_created_for_a_file_without_any():
    assert ledger.add("# 2026-09-12\n\nbody\n", "a") == (
        "---\nmemos:\n  - a\n---\n# 2026-09-12\n\nbody\n"
    )
    assert ledger.add("", "a") == "---\nmemos:\n  - a\n---\n"


def test_the_inline_form_becomes_the_block_form_when_written_to():
    assert ledger.add("---\nmemos: [a, b]\ntags: x\n---\n", "c") == (
        "---\nmemos:\n  - a\n  - b\n  - c\ntags: x\n---\n"
    )


def test_an_unindented_list_keeps_its_own_indent():
    assert ledger.add("---\nmemos:\n- a\n---\n", "b") == "---\nmemos:\n- a\n- b\n---\n"


def test_many_appends_accumulate():
    text = "---\ncreated: 2026-09-12\n---\n# 2026-09-12\n"
    for name in ("a", "b", "c"):
        text = ledger.add(text, name)
    assert ledger.read(text) == ["a", "b", "c"]
    assert text.endswith("---\n# 2026-09-12\n")

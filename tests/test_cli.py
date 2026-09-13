from __future__ import annotations

from datetime import datetime

import pytest
from click.testing import CliRunner

from memo import journal
from memo.cli import cli, parse_since
from memo.store import Store

from conftest import make_transcript

NOW = datetime(2026, 9, 12, 12, 0, 0)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("3d", datetime(2026, 9, 9, 12, 0, 0)),
        ("2w", datetime(2026, 8, 29, 12, 0, 0)),
        ("12h", datetime(2026, 9, 12, 0, 0, 0)),
        ("90m", datetime(2026, 9, 12, 10, 30, 0)),
        (" 1D ", datetime(2026, 9, 11, 12, 0, 0)),
        ("2026-09-01", datetime(2026, 9, 1, 0, 0, 0)),
        ("2026-09-01T08:30", datetime(2026, 9, 1, 8, 30, 0)),
    ],
)
def test_parse_since(value, expected):
    assert parse_since(value, now=NOW) == expected


@pytest.mark.parametrize("value", ["", "soon", "3", "3x", "yesterday", "2026-13-01"])
def test_parse_since_rejects_junk(value):
    with pytest.raises(ValueError):
        parse_since(value, now=NOW)


@pytest.fixture
def transcribed(isolated_env) -> Store:
    """Three transcripts, no journals yet."""
    store = Store(isolated_env / "memos")
    store.ensure_dirs()
    for name, text in [
        ("2026-09-11_083000_000", "Older thought from yesterday."),
        ("2026-09-12_170312_000", "Refactor the transcription pipeline tomorrow."),
        ("2026-09-12_174501_001", "Buy oat milk."),
    ]:
        store.write_transcript(make_transcript(name, text))
    return store


@pytest.fixture
def populated(transcribed) -> Store:
    """…and the journals that hold them, which is what `show` and `ls` read."""
    journal.append_missing(transcribed)
    return transcribed


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_show_groups_by_day_newest_last(populated):
    result = run("show")
    assert result.exit_code == 0
    assert result.output.index("2026-09-11") < result.output.index("2026-09-12")
    assert "17:03  0:42" in result.output
    assert "Refactor the transcription pipeline tomorrow." in result.output
    assert "\x1b[" not in result.output  # no ANSI when stdout is not a TTY


def test_show_limit_and_since(populated):
    assert run("show", "-n", "1").output.count("## ") == 0
    assert "Buy oat milk." in run("show", "-n", "1").output
    assert "Older thought" not in run("show", "-n", "1").output
    assert "Older thought" in run("show", "--all").output


def test_show_rejects_two_selectors(populated):
    result = run("show", "--all", "--today")
    assert result.exit_code == 2


def test_show_bad_since_is_a_usage_error(populated):
    assert run("show", "--since", "soon").exit_code == 2


def test_show_json(populated):
    import json

    payload = json.loads(run("show", "--all", "--json").output)
    assert [item["name"] for item in payload] == [
        "2026-09-11_083000_000",
        "2026-09-12_170312_000",
        "2026-09-12_174501_001",
    ]
    assert payload[2] == {
        "date": "2026-09-12",
        "time": "17:45",
        "duration_s": 42.0,
        "text": "Buy oat milk.",
        "name": "2026-09-12_174501_001",
    }


def test_show_reads_the_journal_not_the_transcripts(populated):
    """Both machines see the same thing: whatever the journal says."""
    path = populated.journal_path("2026-09-12")
    path.write_text(
        path.read_text(encoding="utf-8").replace("Buy oat milk.", "Buy oat milk and bread."),
        encoding="utf-8",
    )
    assert "Buy oat milk and bread." in run("show", "--all").output
    assert "Buy oat milk and bread." in run("ls").output


def test_show_needs_no_transcripts_at_all(populated):
    """A second machine has the journals and none of the JSON."""
    import shutil

    shutil.rmtree(populated.transcripts_dir)
    assert len(run("ls").output.strip().splitlines()) == 3


def test_show_empty(store):
    assert run("show").output.strip() == "no memos"


def test_ls(populated):
    lines = run("ls").output.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("2026-09-11 08:30   0:42  Older thought")


def test_rebuild_appends_what_the_journals_are_missing(transcribed):
    result = run("rebuild")
    assert result.exit_code == 0
    assert "appended 3 memos to the journals" in result.output
    assert transcribed.journal_path("2026-09-12").exists()
    assert transcribed.claude_md.exists()
    assert transcribed.ledger_names() == {item.name for item in transcribed.transcripts()}


def test_rebuild_leaves_a_complete_journal_alone(populated):
    before = {path: path.read_text(encoding="utf-8") for path in populated.journal_files()}
    result = run("rebuild")
    assert result.exit_code == 0
    assert "nothing missing from the journals" in result.output
    assert {path: path.read_text(encoding="utf-8") for path in populated.journal_files()} == before


def test_rebuild_keeps_hand_edits_and_re_adds_only_the_deleted_entry(populated):
    path = populated.journal_path("2026-09-12")
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("Refactor the transcription pipeline tomorrow.", "Refactor the pipeline.")
        .replace("  - 2026-09-12_174501_001\n", "")
        .replace("\n## 17:45 · 0:42\n\nBuy oat milk.\n", ""),
        encoding="utf-8",
    )

    result = run("rebuild")
    assert result.exit_code == 0
    assert "appended 1 memo to the journals" in result.output

    content = path.read_text(encoding="utf-8")
    assert "Refactor the pipeline." in content  # the hand edit survives
    assert "Refactor the transcription pipeline tomorrow." not in content
    assert content.count("Buy oat milk.") == 1


def test_status(populated):
    result = run("status")
    assert result.exit_code == 0
    assert str(populated.root) in result.output
    assert "3 audio" not in result.output  # transcripts exist, audio does not
    assert "0 audio, 3 transcribed" in result.output
    assert "last sync   never" in result.output


# ------------------------------------------------------ journals in a vault


@pytest.fixture
def opened(monkeypatch):
    """Record what `memo open` shells out to."""
    calls: list[list[str]] = []
    monkeypatch.setattr("memo.cli.subprocess.run", lambda command, **kwargs: calls.append(command))
    monkeypatch.delenv("EDITOR", raising=False)
    return calls


def test_open_hands_a_journal_in_a_vault_to_obsidian(populated, vault, opened):
    run("rebuild")
    note = vault / "Memos" / "2026-09-12.md"
    assert note.exists()

    result = run("open")
    assert result.exit_code == 0, result.output
    assert opened == [["open", "obsidian://open?path=" + str(note).replace("/", "%2F")]]
    assert "%20" not in opened[0][1] or " " not in str(note)  # the path is URL-encoded


def test_open_outside_a_vault_uses_the_editor(populated, opened, monkeypatch):
    run("rebuild")
    monkeypatch.setenv("EDITOR", "vi")
    result = run("open")
    assert result.exit_code == 0, result.output
    assert opened == [["vi", str(populated.journal_path("2026-09-12"))]]


def test_open_dir_still_opens_the_memo_dir(populated, vault, opened):
    run("rebuild")
    assert run("open", "--dir").exit_code == 0
    assert opened == [["open", str(populated.root)]]


def test_status_shows_the_journal_dir_and_vault(populated, vault):
    result = run("status")
    assert result.exit_code == 0
    assert f"journal dir {vault / 'Memos'}" in result.output
    assert f"vault       {vault}" in result.output


def test_status_without_a_vault_shows_no_vault_line(populated):
    result = run("status")
    assert f"journal dir {populated.root}" in result.output
    assert "vault  " not in result.output

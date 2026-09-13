from __future__ import annotations

import os
from datetime import datetime

from memo.store import Store, Transcript, parse_recorded_at, recorded_at_for

from conftest import write_wav


def test_parses_tp7_filenames():
    assert parse_recorded_at("2026-09-12_170312_000") == datetime(2026, 9, 12, 17, 3, 12)
    assert parse_recorded_at("2026-01-01_000000") == datetime(2026, 1, 1, 0, 0, 0)


def test_rejects_other_names():
    for stem in ("memo", "2026-09-12", "2026-09-12_1703", "2026-13-40_170312_000", "x2026-09-12_170312_000"):
        assert parse_recorded_at(stem) is None


def test_falls_back_to_mtime(tmp_path):
    path = write_wav(tmp_path / "voice-note.wav")
    os.utime(path, (1_700_000_000, 1_700_000_000))
    assert recorded_at_for(path) == datetime.fromtimestamp(1_700_000_000)


def test_orders_audio_and_finds_untranscribed(store):
    write_wav(store.audio_dir / "2026-09-12_174501_001.wav")
    write_wav(store.audio_dir / "2026-09-12_170312_000.wav")

    names = [path.stem for path in store.audio_files()]
    assert names == ["2026-09-12_170312_000", "2026-09-12_174501_001"]
    assert [path.stem for path in store.untranscribed()] == names

    store.write_transcript(
        Transcript(
            name="2026-09-12_170312_000",
            recorded_at=datetime(2026, 9, 12, 17, 3, 12),
            duration_s=1.0,
            language="en",
            model="fake",
            text="hello",
        )
    )
    assert [path.stem for path in store.untranscribed()] == ["2026-09-12_174501_001"]


def test_transcript_round_trip(store):
    original = Transcript(
        name="2026-09-12_170312_000",
        recorded_at=datetime(2026, 9, 12, 17, 3, 12),
        duration_s=42.4,
        language="en",
        model="fake",
        text="hello there",
        segments=[{"start": 0.0, "end": 1.0, "text": "hello there"}],
    )
    store.write_transcript(original)
    loaded = store.read_transcript(original.name)
    assert loaded is not None
    assert loaded.text == original.text
    assert loaded.recorded_at == original.recorded_at
    assert loaded.duration == "0:42"


def test_ignores_unreadable_transcripts(store):
    store.transcript_path("broken").write_text("{not json", encoding="utf-8")
    assert store.read_transcript("broken") is None
    assert store.transcripts() == []


def test_journal_dir_defaults_to_the_memo_dir(store):
    assert store.journal_dir == store.root
    assert store.journal_path("2026-09-12") == store.root / "2026-09-12.md"


def test_journal_dir_comes_from_the_config_and_the_env(isolated_env, monkeypatch):
    from memo.config import load as load_config

    monkeypatch.setenv("MEMO_JOURNAL_DIR", str(isolated_env / "vault" / "Memos"))
    cfg = load_config()
    resolved = Store.from_config(cfg)
    assert resolved.root == isolated_env / "memos"
    assert resolved.journal_dir == isolated_env / "vault" / "Memos"
    assert resolved.journal_path("2026-09-12") == isolated_env / "vault" / "Memos" / "2026-09-12.md"
    # Audio, transcripts and CLAUDE.md never leave the memo dir.
    assert resolved.audio_dir == resolved.root / "audio"
    assert resolved.claude_md == resolved.root / "CLAUDE.md"


def test_journal_files_are_listed_from_the_journal_dir(isolated_env, monkeypatch):
    from memo.config import load as load_config

    journals = isolated_env / "vault" / "Memos"
    journals.mkdir(parents=True)
    (journals / "2026-09-12.md").write_text("# 2026-09-12\n", encoding="utf-8")
    (journals / "not-a-journal.md").write_text("hello\n", encoding="utf-8")
    monkeypatch.setenv("MEMO_JOURNAL_DIR", str(journals))

    assert [path.name for path in Store.from_config(load_config()).journal_files()] == [
        "2026-09-12.md"
    ]

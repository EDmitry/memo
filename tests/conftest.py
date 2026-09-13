from __future__ import annotations

import struct
import wave
from datetime import datetime
from pathlib import Path

import pytest

from memo.store import Store, Transcript

FAKE_TP7 = Path(__file__).with_name("fake_tp7.py")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No real config, no real memo dir, no real device."""
    monkeypatch.setenv("MEMO_CONFIG", str(tmp_path / "no-such-config.toml"))
    monkeypatch.setenv("MEMO_DIR", str(tmp_path / "memos"))
    monkeypatch.setenv("MEMO_TP7", str(FAKE_TP7))
    for name in (
        "MEMO_MODEL",
        "MEMO_LANGUAGE",
        "MEMO_REMOTE_DIRS",
        "MEMO_MAX_MINUTES",
        "MEMO_JOURNAL_DIR",
        "MEMO_JOURNAL_TEMPLATE",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def store(isolated_env) -> Store:
    store = Store(isolated_env / "memos")
    store.ensure_dirs()
    return store


@pytest.fixture
def remote(isolated_env) -> Path:
    """A directory of TP-7-looking recordings for the fake tp7 to serve."""
    directory = isolated_env / "device"
    directory.mkdir()
    write_wav(directory / "2026-09-12_170312_000.wav", seconds=1)
    write_wav(directory / "2026-09-12_174501_001.wav", seconds=2)
    return directory


@pytest.fixture
def vault(isolated_env, monkeypatch) -> Path:
    """An Obsidian vault whose `Memos` folder is where journals are written."""
    root = isolated_env / "vault"
    (root / ".obsidian").mkdir(parents=True)
    (root / "Memos").mkdir()
    monkeypatch.setenv("MEMO_JOURNAL_DIR", str(root / "Memos"))
    return root


def write_wav(path: Path, seconds: int = 1, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * rate * seconds)
    return path


def make_transcript(name: str, text: str, duration: float = 42.0) -> Transcript:
    from memo.store import parse_recorded_at

    recorded = parse_recorded_at(name) or datetime(2026, 9, 12, 17, 3, 12)
    return Transcript(
        name=name,
        recorded_at=recorded,
        duration_s=duration,
        language="en",
        model="fake",
        text=text,
        segments=[{"start": 0.0, "end": duration, "text": text}],
        transcribed_at=datetime(2026, 9, 12, 18, 0, 0),
    )

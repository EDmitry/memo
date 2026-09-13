"""End-to-end `memo sync` against a fake tp7 binary and a fake transcriber.

Nothing here needs the model or the device.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from memo.cli import _wait_for_unplug as real_wait_for_unplug
from memo.cli import cli
from memo.config import load as load_config
from memo.store import Store, Transcript, recorded_at_for


class FakeTranscriber:
    """Stands in for mlx-whisper; records what it was asked to transcribe."""

    calls: list[str] = []

    def __init__(self, model: str, language: str = "") -> None:
        self.model = model
        self.language = language

    def duration(self, path) -> float:
        return 3600.0 if "long" in path.stem else 42.0

    def transcribe(self, path, duration_s: float | None = None) -> Transcript:
        FakeTranscriber.calls.append(path.stem)
        return Transcript(
            name=path.stem,
            recorded_at=recorded_at_for(path),
            duration_s=self.duration(path) if duration_s is None else duration_s,
            language="en",
            model=self.model,
            text=f"transcript of {path.stem}",
            segments=[{"start": 0.0, "end": 1.0, "text": f"transcript of {path.stem}"}],
            transcribed_at=datetime(2026, 9, 12, 18, 0, 0),
        )


@pytest.fixture
def synced(isolated_env, remote, monkeypatch) -> Store:
    FakeTranscriber.calls = []
    monkeypatch.setattr("memo.cli.Transcriber", FakeTranscriber)
    # The fake recorder never gets unplugged; tests that care re-enable the wait.
    monkeypatch.setattr("memo.cli._wait_for_unplug", lambda cfg: None)
    monkeypatch.setenv("MEMO_REMOTE_DIRS", "/recordings,/memo")
    monkeypatch.setenv("FAKE_TP7_DIRS", "/recordings")
    monkeypatch.setenv("FAKE_TP7_SOURCE", str(remote))
    return Store(isolated_env / "memos")


def run(*args):
    return CliRunner().invoke(cli, ["sync", *args])


def test_sync_pulls_transcribes_and_writes_the_journal(synced):
    result = run()
    assert result.exit_code == 0, result.output

    assert sorted(path.name for path in synced.audio_files()) == [
        "2026-09-12_170312_000.wav",
        "2026-09-12_174501_001.wav",
    ]
    assert FakeTranscriber.calls == ["2026-09-12_170312_000", "2026-09-12_174501_001"]
    assert len(synced.transcripts()) == 2

    day = synced.journal_path("2026-09-12").read_text(encoding="utf-8")
    assert day.startswith(
        "---\nmemos:\n  - 2026-09-12_170312_000\n  - 2026-09-12_174501_001\n---\n"
        "# 2026-09-12\n\n## 17:03 · 0:42\n"
    )
    assert "transcript of 2026-09-12_174501_001" in day

    assert synced.claude_md.exists()
    assert synced.last_sync.exists()
    assert "pulled 2 new files" in result.output
    assert "2 memos transcribed" in result.output
    # A missing remote dir is skipped silently.
    assert "/memo" not in result.output


def test_sync_is_idempotent(synced):
    run()
    FakeTranscriber.calls = []
    result = run()
    assert result.exit_code == 0
    assert FakeTranscriber.calls == []
    assert "nothing new to transcribe" in result.output
    assert len(synced.transcripts()) == 2


def test_sync_appends_a_transcript_the_journal_is_missing(synced):
    """A crash between transcribing and appending, or a day file deleted."""
    run()
    day = synced.journal_path("2026-09-12")
    day.unlink()
    FakeTranscriber.calls = []

    result = run("--no-pull")
    assert result.exit_code == 0, result.output
    assert FakeTranscriber.calls == []  # the transcripts are still here
    assert "appended 2026-09-12_170312_000 to the journal" in result.output
    assert sorted(_ledger(day)) == ["2026-09-12_170312_000", "2026-09-12_174501_001"]


def test_sync_never_imports_mlx(synced):
    run()
    assert "mlx_whisper" not in sys.modules


def test_sync_reports_recordings_over_the_cap(synced, remote):
    from conftest import write_wav

    write_wav(remote / "2026-09-12_190000_long.wav")
    result = run("--max-minutes", "30")
    assert result.exit_code == 0
    assert "skipped 2026-09-12_190000_long.wav (60:00, over --max-minutes)" in result.output
    assert "2026-09-12_190000_long" not in FakeTranscriber.calls
    assert synced.transcript_path("2026-09-12_190000_long").exists() is False

    # The cap is liftable, and the file is still there to pick up.
    result = run("--no-pull", "--max-minutes", "0")
    assert "2026-09-12_190000_long" in FakeTranscriber.calls
    assert result.exit_code == 0


def test_sync_without_a_device_is_not_an_error(synced, monkeypatch):
    monkeypatch.setenv("FAKE_TP7_DEVICES", "0")
    result = run()
    assert result.exit_code == 0
    assert result.output.strip() == "no TP-7 connected"
    assert synced.transcripts() == []


def _ledger(path):
    from memo import ledger

    return ledger.read(path.read_text(encoding="utf-8"))


def test_no_pull_transcribes_local_audio_without_the_device(synced, monkeypatch, remote):
    monkeypatch.setenv("FAKE_TP7_DEVICES", "0")
    synced.ensure_dirs()
    (synced.audio_dir / "2026-09-12_170312_000.wav").write_bytes(
        (remote / "2026-09-12_170312_000.wav").read_bytes()
    )
    result = run("--no-pull")
    assert result.exit_code == 0
    assert FakeTranscriber.calls == ["2026-09-12_170312_000"]


def test_auto_runs_right_after_a_manual_sync(synced):
    """Nothing throttles a plug-in: the only guard is the lock."""
    assert run().exit_code == 0
    assert len(FakeTranscriber.calls) == 2

    from conftest import write_wav

    write_wav(synced.audio_dir / "2026-09-12_200000_002.wav")
    FakeTranscriber.calls = []
    assert run("--auto").exit_code == 0
    assert FakeTranscriber.calls == ["2026-09-12_200000_002"]
    assert "ok pulled=0 transcribed=1" in synced.sync_log.read_text(encoding="utf-8")


def test_auto_notifies_when_there_is_nothing_new(synced, monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr("memo.cli.notify", messages.append)

    assert run("--auto").exit_code == 0
    assert messages == ["2 new memos transcribed"]

    messages.clear()
    assert run("--auto").exit_code == 0
    assert messages == ["no new memos"]
    assert "ok pulled=0 transcribed=0" in synced.sync_log.read_text(encoding="utf-8")


def test_auto_logs_and_exits_1_when_tp7_fails(synced, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMO_TP7", str(tmp_path / "no-such-tp7"))
    result = run("--auto")
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "error:" in synced.sync_log.read_text(encoding="utf-8")


def test_sync_reports_files_tp7_refused_to_pull(synced, remote):
    from conftest import write_wav

    write_wav(remote / "huge_2026-09-12_200000_000.wav")
    result = run()
    assert result.exit_code == 0, result.output
    assert "not pulled (over max_pull_mb): /recordings/huge_2026-09-12_200000_000.wav" in result.output
    assert not (synced.audio_dir / "huge_2026-09-12_200000_000.wav").exists()
    assert "huge_2026-09-12_200000_000" not in FakeTranscriber.calls


def test_sync_refuses_to_run_twice_at_once(synced):
    import fcntl

    synced.ensure_dirs()
    with open(synced.meta_dir / "lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)

        result = run()
        assert result.exit_code == 1
        assert "another memo sync is already running" in result.output
        assert FakeTranscriber.calls == []

        result = run("--auto")
        assert result.exit_code == 0
        assert result.output == ""
        assert "skipped (another sync is running)" in synced.sync_log.read_text()


def _device(mode: str, registry_id: str):
    from memo.device import Device

    return Device.from_dict(
        {"serial_number": "TPBYO104", "mode": mode, "registry_entry_id": registry_id}
    )


def _run_auto_with_usb_history(monkeypatch, history):
    """Run `sync --auto` with `list_devices` answering from `history` in order."""
    polls = iter(history)
    monkeypatch.setattr("memo.cli.list_devices", lambda tp7: next(polls))
    monkeypatch.setattr("memo.cli.UNPLUG_POLL_SECONDS", 0)
    monkeypatch.setattr("memo.cli._wait_for_unplug", real_wait_for_unplug)
    result = run("--auto")
    assert result.exit_code == 0, result.output
    assert FakeTranscriber.calls  # the sync itself still ran first
    return polls


def test_auto_stays_alive_until_the_recorder_is_unplugged(synced, monkeypatch):
    audio = _device("audio-midi", "0xa")
    polls = _run_auto_with_usb_history(
        monkeypatch,
        [
            [audio],  # sync's own presence check
            [_device("mtp", "0x1")],  # session just closed, flip still pending
            [audio],  # settled in audio mode: this is the identity to watch
            *([[]] * 2),  # a short absence is a re-enumeration, not an unplug
            [audio],
            *([[]] * 6),  # absent for six polls: unplugged
            [audio],  # never reached
        ],
    )
    assert next(polls) == [audio]


def test_auto_exits_on_a_quick_replug(synced, monkeypatch):
    before = _device("audio-midi", "0xa")
    after = _device("audio-midi", "0xb")
    polls = _run_auto_with_usb_history(
        monkeypatch,
        [[before], [before], [before], [after], [after]],  # never reached
    )
    assert next(polls) == [after]


def test_auto_waits_for_a_powered_off_recorder_to_be_turned_on(synced, monkeypatch):
    off = _device("mass-storage", "0x1")
    on = _device("audio-midi", "0x2")
    polls = iter([[off], [off], [on]])
    monkeypatch.setattr("memo.cli.list_devices", lambda tp7: next(polls))
    monkeypatch.setattr("memo.cli.POWER_ON_POLL_SECONDS", 0)
    notices: list[str] = []
    monkeypatch.setattr("memo.cli.notify", notices.append)

    result = run("--auto")
    assert result.exit_code == 0, result.output
    assert notices[0] == "TP-7 is powered off; turn it on to sync"
    assert FakeTranscriber.calls  # synced once it came on
    assert "waiting (TP-7 is powered off)" in synced.sync_log.read_text()


def test_manual_sync_gives_up_on_a_powered_off_recorder(synced, monkeypatch):
    monkeypatch.setattr("memo.cli.list_devices", lambda tp7: [_device("mass-storage", "0x1")])
    monkeypatch.setattr("memo.cli.POWER_ON_POLL_SECONDS", 0)
    monkeypatch.setattr("memo.cli.POWER_ON_MANUAL_TIMEOUT", 0)

    result = run()
    assert result.exit_code == 1
    assert "TP-7 is powered off" in result.output
    assert "turn it on" in result.output
    assert FakeTranscriber.calls == []


# ------------------------------------------- two machines, one shared vault


@pytest.fixture
def shared(isolated_env, monkeypatch) -> Path:
    """A journal folder inside an Obsidian vault, shared by both machines."""
    journals = isolated_env / "vault" / "Memos"
    (isolated_env / "vault" / ".obsidian").mkdir(parents=True)
    journals.mkdir()
    monkeypatch.setenv("MEMO_JOURNAL_DIR", str(journals))
    return journals


def machine(isolated_env, monkeypatch, name: str) -> Store:
    """Point the CLI at one machine's own memo dir; the journal dir is shared."""
    root = isolated_env / name
    monkeypatch.setenv("MEMO_DIR", str(root))
    return Store.from_config(load_config())


def test_sync_into_a_shared_journal_never_duplicates_a_memo(synced, shared, isolated_env, monkeypatch):
    day = shared / "2026-09-12.md"

    # Machine A pulls both memos from the recorder and transcribes them.
    first = machine(isolated_env, monkeypatch, "machine-a")
    assert run().exit_code == 0
    assert len(FakeTranscriber.calls) == 2
    after_a = day.read_text(encoding="utf-8")

    # Machine B has its own (empty) dir and the same recorder, but shares the
    # journal folder: the ledger tells it both memos are already written up.
    second = machine(isolated_env, monkeypatch, "machine-b")
    FakeTranscriber.calls = []
    result = run()
    assert result.exit_code == 0, result.output
    assert FakeTranscriber.calls == []
    assert "nothing new to transcribe" in result.output
    assert day.read_text(encoding="utf-8") == after_a  # byte for byte
    assert second.transcripts() == []  # B holds no copy of A's text
    assert len(second.audio_files()) == 2  # but it did pull the audio as a backup
    assert len(CliRunner().invoke(cli, ["ls"]).output.strip().splitlines()) == 2

    # B records a new memo and syncs it.
    from conftest import write_wav

    write_wav(Path(os.environ["FAKE_TP7_SOURCE"]) / "2026-09-12_200000_002.wav")
    FakeTranscriber.calls = []
    assert run().exit_code == 0
    assert FakeTranscriber.calls == ["2026-09-12_200000_002"]

    # A syncs again: B's memo is in the ledger, so A leaves it alone.
    machine(isolated_env, monkeypatch, "machine-a")
    FakeTranscriber.calls = []
    result = run()
    assert result.exit_code == 0, result.output
    assert FakeTranscriber.calls == []

    content = day.read_text(encoding="utf-8")
    assert _ledger(day) == [
        "2026-09-12_170312_000",
        "2026-09-12_174501_001",
        "2026-09-12_200000_002",
    ]
    assert content.count("## 17:03 · 0:42") == 1
    assert len(first.transcripts()) == 2  # A only ever transcribed its own two

    # And a rebuild on either machine changes nothing.
    assert CliRunner().invoke(cli, ["rebuild"]).exit_code == 0
    assert day.read_text(encoding="utf-8") == content


def test_a_hand_edit_in_the_shared_journal_is_permanent(synced, shared, isolated_env, monkeypatch):
    machine(isolated_env, monkeypatch, "machine-a")
    assert run().exit_code == 0
    day = shared / "2026-09-12.md"
    day.write_text(
        day.read_text(encoding="utf-8").replace(
            "transcript of 2026-09-12_170312_000", "What I actually said."
        ),
        encoding="utf-8",
    )

    assert run("--no-pull").exit_code == 0
    assert CliRunner().invoke(cli, ["rebuild"]).exit_code == 0
    assert "What I actually said." in day.read_text(encoding="utf-8")
    assert "transcript of 2026-09-12_170312_000" not in day.read_text(encoding="utf-8")


def test_re_transcribing_one_memo_takes_deleting_its_entry_and_its_ledger_line(
    synced, shared, isolated_env, monkeypatch
):
    machine(isolated_env, monkeypatch, "machine-a")
    assert run().exit_code == 0
    day = shared / "2026-09-12.md"
    name = "2026-09-12_170312_000"

    text = day.read_text(encoding="utf-8")
    day.write_text(
        text.replace(f"  - {name}\n", "").replace(
            f"## 17:03 · 0:42\n\ntranscript of {name}\n\n", ""
        ),
        encoding="utf-8",
    )
    Store.from_config(load_config()).transcript_path(name).unlink()

    FakeTranscriber.calls = []
    assert run("--no-pull").exit_code == 0
    assert FakeTranscriber.calls == [name]
    assert _ledger(day).count(name) == 1
    assert day.read_text(encoding="utf-8").count("## 17:03 · 0:42") == 1


def test_no_pull_writes_into_the_journal_dir(synced, shared, remote, monkeypatch):
    monkeypatch.setenv("FAKE_TP7_DEVICES", "0")
    synced.ensure_dirs()
    (synced.audio_dir / "2026-09-12_170312_000.wav").write_bytes(
        (remote / "2026-09-12_170312_000.wav").read_bytes()
    )

    result = run("--no-pull")
    assert result.exit_code == 0, result.output
    assert not (synced.root / "2026-09-12.md").exists()
    assert (shared / "2026-09-12.md").read_text(encoding="utf-8") == (
        "---\nmemos:\n  - 2026-09-12_170312_000\n---\n"
        "# 2026-09-12\n\n## 17:03 · 0:42\n\ntranscript of 2026-09-12_170312_000\n"
    )
    assert synced.claude_md.exists()  # CLAUDE.md never leaves the memo dir

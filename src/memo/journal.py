"""Daily Markdown journals: the record, not a rendering of one.

A day file is a *head* — frontmatter, an H1, whatever ``journal_template`` says,
plus anything the user typed — followed by one ``## HH:MM · M:SS`` section per
memo. memo only ever appends: the entry goes at the end of the file and the
recording's name at the end of the ``memos:`` ledger in the frontmatter (see
:mod:`memo.ledger`). Nothing already in the file is rewritten or reordered, so
a mishearing fixed in Obsidian stays fixed and a ``modified:`` field a plugin
maintains survives.

The ledger is what makes the journal the *shared* record: a recording counts as
processed once some day file lists its name, so a second machine syncing the
same recorder into the same folder transcribes only what nobody transcribed
yet. Transcripts stay local — hidden folders may not sync at all — and are
this machine's copy of the text, not the thing the journal is generated from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import ledger
from .config import ConfigError
from .store import Store, Transcript, parse_recorded_at

#: The heading of an entry: ``## 17:03 · 0:42``. Loose on purpose: a
#: hand-edited file should still parse.
ENTRY_HEADING_RE = re.compile(r"^##[ \t]+(\d{1,2}):(\d{2})[ \t]*·[ \t]*(\d+):(\d{2})[ \t]*$")

#: ``{{date}}``, ``{{time}}``, ``{{title}}`` in a journal template.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z]+)\s*\}\}")

CLAUDE_MD = """\
# Voice memo journal

This directory is a journal of voice memos recorded on a Teenage Engineering
TP-7 and transcribed locally with Whisper by the `memo` CLI.

## Layout

{journals}
- `audio/` — the original recordings, never modified.
- `.memo/transcripts/*.json` — this machine's copy of each transcription
  (text, segments, model, timestamps).
- `.memo/sync.log`, `.memo/last-sync` — bookkeeping for automatic syncs.

## Reading these

Entries are spoken thoughts, usually short and unedited: notes to self, ideas,
reminders, half-formed reasoning. They are machine transcriptions, so expect
mishearings — names, jargon, and acronyms are the usual casualties — and
punctuation the speaker never intended. Read for intent rather than literally,
and say so when a passage looks like a transcription error rather than
inferring something confident from it.
"""

HERE_JOURNALS = """\
- `YYYY-MM-DD.md` — one file per day, one `##` section per memo, headed
  `## HH:MM · M:SS` (time of recording, duration). This is the thing to read;
  the `memos:` frontmatter property lists the recordings it already holds."""

ELSEWHERE_JOURNALS = """\
- `{journal_dir}/YYYY-MM-DD.md` — one file per day, one `##` section per memo,
  headed `## HH:MM · M:SS` (time of recording, duration). That is the thing to
  read; the `memos:` frontmatter property lists the recordings it already
  holds."""


def entry(transcript: Transcript) -> str:
    """One journal section, ending in a single newline."""
    text = " ".join(transcript.text.split()) or "_(no speech detected)_"
    return f"## {transcript.time} · {transcript.duration}\n\n{text}\n"


def default_head(date: str) -> str:
    return f"# {date}\n\n"


def render_template(text: str, *, date: str, when: datetime) -> str:
    """Fill in ``{{date}}``, ``{{time}}`` and ``{{title}}``; leave others alone."""
    values = {"date": date, "time": when.strftime("%H:%M"), "title": date}
    return PLACEHOLDER_RE.sub(
        lambda match: values.get(match.group(1).lower(), match.group(0)), text
    )


def append_entry(store: Store, transcript: Transcript) -> Path:
    """Append one memo to its day file: the entry at the end, the name in the ledger."""
    path = store.journal_path(transcript.date)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing.strip():
        existing = _new_head(store, transcript.date, transcript.recorded_at)
    return _write(path, ledger.add(_join(existing, entry(transcript)), transcript.name))


def append_missing(store: Store) -> list[Transcript]:
    """Append every local transcript no journal lists yet, oldest first.

    This is all `memo rebuild` does, and the last step of every sync. It covers
    a crash between writing the transcript and appending the entry, a day file
    deleted by hand, and transcripts made before `journal_dir` moved.
    """
    listed = store.ledger_names()
    appended: list[Transcript] = []
    for transcript in store.transcripts():
        if transcript.name in listed:
            continue
        append_entry(store, transcript)
        listed.add(transcript.name)
        appended.append(transcript)
    return appended


def ensure_claude_md(store: Store) -> bool:
    """Write ``CLAUDE.md`` if absent so ``cd <memo dir> && claude`` has context."""
    path = store.claude_md
    if path.exists():
        return False
    journals = (
        HERE_JOURNALS
        if store.journal_dir == store.root
        else ELSEWHERE_JOURNALS.format(journal_dir=store.journal_dir)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CLAUDE_MD.format(journals=journals), encoding="utf-8")
    return True


def _new_head(store: Store, date: str, when: datetime) -> str:
    """The head for a day file memo is about to create."""
    if store.journal_template is None:
        return default_head(date)
    try:
        text = store.journal_template.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"journal_template: {error}") from error
    return render_template(text, date=date, when=when)


def _join(before: str, addition: str) -> str:
    """``before`` as it stands, one blank line, then ``addition``."""
    stripped = before.rstrip("\n")
    return f"{stripped}\n\n{addition}" if stripped else addition


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------- reading them back


@dataclass(frozen=True)
class JournalEntry:
    """One memo as the journal has it — what `memo show` and `memo ls` print."""

    date: str
    time: str
    duration_s: float
    text: str
    #: The recording, when the day's ledger lines up with its entries.
    name: str | None = None

    @property
    def when(self) -> datetime:
        return datetime.strptime(f"{self.date} {self.time}", "%Y-%m-%d %H:%M")

    @property
    def duration(self) -> str:
        total = int(round(self.duration_s))
        return f"{total // 60}:{total % 60:02d}"

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "time": self.time,
            "duration_s": self.duration_s,
            "text": self.text,
            "name": self.name,
        }


def parse_day(text: str, date: str) -> list[JournalEntry]:
    """Every memo entry in one day file, tolerant of hand edits.

    Only a ``## HH:MM · M:SS`` heading starts an entry, which then runs to the
    next H2 or to the end of the file — so the head, a template, and any
    section the user adds are never mistaken for memos.
    """
    entries: list[JournalEntry] = []
    heading: re.Match[str] | None = None
    lines: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        match = ENTRY_HEADING_RE.match(line)
        if match or line.startswith("## "):
            # Any H2 ends the entry above it; only an entry heading starts one.
            if heading is not None:
                entries.append(_entry_from(date, heading, lines))
            heading, lines = match, []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        entries.append(_entry_from(date, heading, lines))
    return entries


def journal_entries(store: Store) -> list[JournalEntry]:
    """Every memo in every day file, oldest first, named from the ledgers."""
    found: list[JournalEntry] = []
    for path in store.journal_files():
        text = path.read_text(encoding="utf-8")
        found.extend(_named(parse_day(text, path.stem), ledger.read(text)))
    return sorted(found, key=lambda item: (item.date, item.time))


def _named(entries: list[JournalEntry], names: list[str]) -> list[JournalEntry]:
    """Pair a day's entries with its ledger, when the two plainly line up.

    memo appends both together, so position is the pairing. A file where that
    no longer holds — entries or ledger lines deleted by hand — gets unnamed
    entries rather than a guess: the text is what `show` is for.
    """
    if len(entries) != len(names) or not _aligned(entries, names):
        return entries
    return [replace(item, name=name) for item, name in zip(entries, names)]


def _aligned(entries: list[JournalEntry], names: list[str]) -> bool:
    """True while every name that carries a timestamp matches its entry's."""
    for item, name in zip(entries, names):
        recorded = parse_recorded_at(name)
        if recorded is not None and recorded.strftime("%H:%M") != item.time:
            return False
    return True


def _entry_from(date: str, heading: re.Match[str], lines: list[str]) -> JournalEntry:
    hour, minute, minutes, seconds = heading.groups()
    return JournalEntry(
        date=date,
        time=f"{int(hour):02d}:{minute}",
        duration_s=float(int(minutes) * 60 + int(seconds)),
        text=" ".join(" ".join(lines).split()),
    )

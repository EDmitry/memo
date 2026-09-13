"""Daily Markdown journals rendered from transcripts.

Transcripts are this machine's durable record; a journal file is a view that
:func:`rebuild` can always regenerate. Appending one entry produces exactly the
text a full render of that day would.

A journal file is a *head* — an H1, or whatever ``journal_template`` says, plus
anything the user added above the first entry — followed by memo's entries.
memo owns the entries: :func:`rebuild` replaces everything from the first entry
heading to the end of the file and leaves the head byte for byte as it found
it, so frontmatter an Obsidian plugin keeps up to date survives.

Every entry ends with an Obsidian block id naming the recording it came from,
which makes the journal the one *shared* record: when ``journal_dir`` is a
folder two machines sync, :func:`adopt` reconstructs a local transcript for
every entry this machine never transcribed itself, so neither machine
duplicates the other's memos and either can regenerate the whole day.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import ConfigError
from .obsidian import block_id, name_from_block_id
from .store import Store, Transcript

#: The heading of a rendered entry: ``## 17:03 · 0:42``. Loose on purpose: a
#: hand-edited file should still parse.
ENTRY_HEADING_RE = re.compile(r"^##[ \t]+(\d{1,2}):(\d{2})[ \t]*·[ \t]*(\d+):(\d{2})[ \t]*$", re.MULTILINE)

#: The block id line closing an entry: ``^memo-2026-09-12-170312-000``.
BLOCK_ID_RE = re.compile(r"^\^([A-Za-z0-9-]+)$")

#: ``{{date}}``, ``{{time}}``, ``{{title}}`` in a journal template.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z]+)\s*\}\}")

CLAUDE_MD = """\
# Voice memo journal

This directory is a journal of voice memos recorded on a Teenage Engineering
TP-7 and transcribed locally with Whisper by the `memo` CLI.

## Layout

{journals}
- `audio/` — the original recordings, never modified.
- `.memo/transcripts/*.json` — per-memo transcripts (text, segments, model,
  timestamps). The durable record; the daily files are rendered from these.
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
  `## HH:MM · M:SS` (time of recording, duration). This is the thing to read."""

ELSEWHERE_JOURNALS = """\
- `{journal_dir}/YYYY-MM-DD.md` — one file per day, one `##` section per memo,
  headed `## HH:MM · M:SS` (time of recording, duration). That is the thing to
  read; this directory holds the source data it is rendered from."""


def entry(transcript: Transcript) -> str:
    """One journal section, ending in a single newline.

    The trailing ``^memo-…`` block id is invisible in Obsidian's reading view;
    it is what lets another machine tell this memo from one of its own.
    """
    text = " ".join(transcript.text.split()) or "_(no speech detected)_"
    return (
        f"## {transcript.time} · {transcript.duration}\n\n"
        f"{text}\n\n"
        f"^{block_id(transcript.name)}\n"
    )


def render_entries(transcripts: list[Transcript]) -> str:
    """Every entry of one day, oldest first, separated by a blank line."""
    return "\n".join(entry(item) for item in sorted(transcripts, key=_order))


def render_day(date: str, transcripts: list[Transcript]) -> str:
    """The full Markdown file for one day, with the default head."""
    return _join(default_head(date), render_entries(transcripts))


def default_head(date: str) -> str:
    return f"# {date}\n\n"


def render_template(text: str, *, date: str, when: datetime) -> str:
    """Fill in ``{{date}}``, ``{{time}}`` and ``{{title}}``; leave others alone."""
    values = {"date": date, "time": when.strftime("%H:%M"), "title": date}
    return PLACEHOLDER_RE.sub(
        lambda match: values.get(match.group(1).lower(), match.group(0)), text
    )


def append_entry(store: Store, transcript: Transcript) -> Path:
    """Append one memo to its day file, creating the file if it is new."""
    path = store.journal_path(transcript.date)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing.strip():
        head = existing.rstrip("\n")
    else:
        head = _new_head(store, transcript.date, transcript.recorded_at)
    return _write(path, _join(head, entry(transcript)))


def rebuild(store: Store) -> list[Path]:
    """Rewrite every day's entries from the transcripts. Returns the files written."""
    by_day: dict[str, list[Transcript]] = defaultdict(list)
    for transcript in store.transcripts():
        by_day[transcript.date].append(transcript)

    written: list[Path] = []
    for date in sorted(by_day):
        transcripts = sorted(by_day[date], key=_order)
        path = store.journal_path(date)
        head = _head_of(path)
        if head is None or not head.strip():
            head = _new_head(store, date, transcripts[0].recorded_at)
        written.append(_write(path, _join(head, render_entries(transcripts))))
    return written


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


def _head_of(path: Path) -> str | None:
    """Everything an existing file has before its first entry, else ``None``."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = ENTRY_HEADING_RE.search(text)
    return text[: match.start()] if match else text


def _join(head: str, body: str) -> str:
    """``head`` unchanged, then one blank line, then the entries."""
    if not head.strip():
        return body
    if not head.endswith("\n"):
        head += "\n"
    if not head.endswith("\n\n"):
        head += "\n"
    return head + body


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _order(transcript: Transcript) -> tuple:
    return (transcript.recorded_at, transcript.name)


# ------------------------------------------------------- the shared journal


@dataclass(frozen=True)
class JournalEntry:
    """One memo as it appears in a journal file, however it got there."""

    date: str
    time: str
    duration_s: float
    text: str
    block_id: str | None

    def as_transcript(self, name: str) -> Transcript:
        """The transcript this entry stands for, marked as adopted."""
        return Transcript(
            name=name,
            recorded_at=datetime.strptime(f"{self.date} {self.time}", "%Y-%m-%d %H:%M"),
            duration_s=self.duration_s,
            language="",
            model="",
            text=self.text,
            segments=[],
            adopted=True,
        )


def parse_day(text: str, date: str) -> list[JournalEntry]:
    """Every memo entry in one day file, tolerant of hand edits.

    Only a ``## HH:MM · M:SS`` heading starts an entry, which then runs to the
    next heading, to its block id line, or to the end of the file — so a head,
    a template, and any section the user adds are never mistaken for memos.
    """
    entries: list[JournalEntry] = []
    heading: re.Match[str] | None = None
    lines: list[str] = []

    def close(identifier: str | None) -> None:
        nonlocal heading, lines
        if heading is not None:
            entries.append(_journal_entry(date, heading, lines, identifier))
        heading, lines = None, []

    for raw in text.splitlines():
        line = raw.strip()
        match = ENTRY_HEADING_RE.match(line)
        if match or line.startswith("## "):
            # Any H2 ends the entry above it; only an entry heading starts one.
            close(None)
            heading = match
        elif heading is None:
            continue
        elif found := BLOCK_ID_RE.match(line):
            close(found.group(1))
        else:
            lines.append(line)
    close(None)
    return entries


def journal_entries(store: Store) -> list[JournalEntry]:
    """Every memo entry in every day file, oldest day first."""
    found: list[JournalEntry] = []
    for path in store.journal_files():
        found.extend(parse_day(path.read_text(encoding="utf-8"), path.stem))
    return found


def adopt(store: Store) -> list[Transcript]:
    """Reconstruct local transcripts for journal entries written elsewhere.

    Two machines syncing one recorder share only the journal — hidden folders
    may not sync at all — so the journal is where identity lives. An entry with
    a block id this machine has no transcript for becomes a transcript here,
    which is also what stops the recording being transcribed a second time.
    The journal wins for adopted entries: an edit made in Obsidian survives.
    """
    local = {block_id(item.name): item for item in store.transcripts()}
    audio = {block_id(path.stem): path.stem for path in store.audio_files()}

    adopted: list[Transcript] = []
    for item in journal_entries(store):
        if item.block_id is None:
            continue
        known = local.get(item.block_id)
        name = known.name if known else _name_for(item.block_id, audio)
        candidate = item.as_transcript(name)
        if not _needs_adopting(known, candidate):
            continue
        store.write_transcript(candidate)
        local[item.block_id] = candidate
        adopted.append(candidate)
    return adopted


def _name_for(identifier: str, audio: dict[str, str]) -> str:
    """A memo's name: its recording if this machine has it, else its block id."""
    return audio.get(identifier) or name_from_block_id(identifier)


def _needs_adopting(known: Transcript | None, candidate: Transcript) -> bool:
    """A memo this machine never transcribed, or one edited in the shared journal."""
    if known is None:
        return True
    return known.adopted and entry(known) != entry(candidate)


def _journal_entry(
    date: str, heading: re.Match[str], lines: list[str], identifier: str | None
) -> JournalEntry:
    hour, minute, minutes, seconds = heading.groups()
    return JournalEntry(
        date=date,
        time=f"{int(hour):02d}:{minute}",
        duration_s=float(int(minutes) * 60 + int(seconds)),
        text=" ".join(" ".join(lines).split()),
        block_id=identifier,
    )

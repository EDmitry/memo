# memo — design

`memo` turns TP-7 voice memos into text you can read on one screen and feed to
an AI. It is a thin pipeline over two existing tools: the `tp7` CLI (device
access over MTP) and `mlx-whisper` (local transcription on Apple silicon).

## Workflow it serves

1. Record short thoughts on the TP-7 while walking.
2. Plug the TP-7 into the Mac.
3. Everything below happens without typing anything: new memos are pulled,
   transcribed, and appended to a daily journal. A macOS notification says how
   many arrived.
4. `memo show` prints recent memos on one screen. `memo show --since 7d | claude -p ...`
   feeds them to an AI. The journal is plain Markdown, so Obsidian or any
   editor can read it — and edit it: memo reads the journals back, so a
   mishearing fixed there is fixed everywhere.

## Principles

- **Filesystem is the state.** No database. A memo is "pulled" when its audio
  file exists locally, "transcribed" when its transcript JSON exists, and
  "processed" when a journal lists it. Every step is idempotent and
  re-runnable.
- **One MTP session per sync.** Each `tp7 -a ...` invocation performs the full
  MIDI switch → MTP → close lifecycle, which costs ~10 s and flips the device
  back to audio mode on close. `sync` therefore calls `tp7` exactly once:
  `tp7 -a pull <remote-dir> <audio-dir> --recursive --skip-existing --max-size ...`.
- **The device layer stays in `tp7`.** Anything about USB, MTP, or the TP-7
  belongs in the Rust CLI. `memo` only shells out to `tp7 --json`.
- **The journal is the shared record, and memo only appends to it.** A day
  file's `memos:` frontmatter property is the ledger of the recordings it
  holds; a recording is processed once some journal names it. Appending a memo
  means one entry at the end of the file and one item at the end of that list —
  nothing already written is rewritten or reordered, so a hand edit in Obsidian
  is permanent and `modified:` survives. Transcripts are this machine's local
  copy of the text, not a source the journal is re-rendered from.
- **CLI conventions follow clig.dev** like `tp7`: concise human output,
  `--json` where useful, diagnostics on stderr, non-zero exit on failure.

## Layout

```
~/Memos/                          # MEMO_DIR (config: dir)
  2026-09-12.md                   # daily journal: a memos: ledger, one H2 per memo
  audio/2026-09-12_170312_000.wav # original pulled audio, never modified
  .memo/transcripts/2026-09-12_170312_000.json  # whisper output + metadata
  .memo/sync.log                  # append-only log of automatic runs
  .memo/last-sync                 # when the last run finished, shown by `memo status`
  CLAUDE.md                       # written once by `memo sync`, explains the layout to an agent
```

`journal_dir` moves the `YYYY-MM-DD.md` files somewhere else — a folder inside
an Obsidian vault, typically. Nothing else moves: audio, transcripts, sync
state and `CLAUDE.md` always stay in `dir`.

A day file:

```markdown
---
created: 2026-09-12 13:17
modified: 2026-09-12 13:17
subjects:
memos:
  - 2026-09-12_131726_000
  - 2026-09-12_131837_000
---
# 2026-09-12

## 13:17 · 0:42

Remember to refactor the transcription pipeline tomorrow.

## 13:18 · 1:05

...
```

`memos:` is the ledger: the audio stem of every entry below it, in the order
the entries appear. Obsidian treats it as an ordinary list property and shows
it in the note's properties panel — unless properties are hidden under
Settings → Editor → Properties in document.

`memo.ledger` reads and writes only that block: a line-based reader tolerant of
what Obsidian and hands produce (`memos:` followed by indented `- item` lines,
the inline `memos: [a, b]` form, quoted items), which creates the key just
above the closing `---` when it is absent and a minimal frontmatter at the top
when the file has none. Everything else in the file comes through byte for
byte, which no YAML round-trip can promise.

## Journals in an Obsidian vault

Set `journal_dir` to a folder inside the vault and the daily journals are vault
notes like any other. Two rules keep that safe:

- **memo appends; the file is yours.** A journal file is a *head* —
  frontmatter, an H1, anything typed above the first entry — followed by the
  entries. memo adds an entry at EOF and a name to `memos:`, and touches
  nothing else, ever. So a `modified:` field an Obsidian plugin maintains
  survives, and so does a fix you typed into an entry.
- **memo creates a day file the way the vault expects.** `journal_template`
  points at a file whose contents become the head of a new journal, with
  `{{date}}`, `{{time}}` and `{{title}}` filled in (unknown `{{...}}` are left
  alone). Unset, the head is the plain `# YYYY-MM-DD` of standalone mode.

`memo open` hands the file to Obsidian (`open "obsidian://open?path=…"`) when
`journal_dir` has a `.obsidian` directory in some ancestor, and falls back to
`$EDITOR` otherwise. `memo rebuild` migrates an existing set of transcripts
into the new location: it appends every transcript no journal lists yet.

To re-transcribe one memo: delete its entry, delete its line from `memos:`,
delete `.memo/transcripts/<name>.json`, and run `memo sync --no-pull`. The new
entry lands at the end of the day file (memo never inserts), which `memo show`
and `memo ls` reorder by time anyway.

## Two machines, one vault

Two Macs can share one recorder if they share the journal folder through
iCloud, Obsidian Sync or git. Each keeps its own `dir` — its own audio and its
own transcripts — and only the `.md` files are assumed to sync, since hidden
folders like `.memo/` may not.

That is why the ledger lives in the journal. `Store.untranscribed()` is the
audio whose name is in no journal's `memos:` list and has no local transcript,
so the second machine transcribes only what nobody has transcribed yet; it
still pulls every recording, which makes its copy a backup. `memo show` and
`memo ls` read the journal files rather than the transcripts, so both machines
print the same text — including the machine that never transcribed a word of
it, and including your edits.

Sync then needs one more step, and it is the whole of `memo rebuild`: append
every local transcript no journal lists. That covers a crash between writing
the transcript and appending the entry, and a day file deleted by hand. It can
never disturb an entry that is already there.

The remaining window is sync lag: a memo can still be transcribed twice if both
machines sync it before the journal reaches the other one — which shows up as a
duplicated entry, to be deleted by hand.

The memo timestamp comes from the TP-7 filename (`YYYY-MM-DD_HHMMSS_NNN.wav`);
the MTP modified date is the fallback. Duration comes from the WAV header.

Transcript JSON: `{ "name", "recorded_at", "duration_s", "language", "model",
"text", "segments": [{start, end, text}], "transcribed_at" }`. `memo show
--json` reports what the journal holds instead — `{ "date", "time",
"duration_s", "text", "name" }`, where `name` comes from the ledger when the
day's entries and its ledger line up, and is `null` when they no longer do.

## Commands

```
memo sync            pull new memos from a connected TP-7, transcribe, append to journal
                     --auto      launchd mode: quiet, notifies on completion, waits for unplug
                     --no-pull   only transcribe audio already in audio/
memo show            print recent memos (read from the journals), newest day last
                     -n N | --today | --since 3d|2026-09-01 | --all | --json
memo ls              one line per memo: date, time, duration, first words
memo open            open today's journal (or --dir); Obsidian when it lives in a vault
memo rebuild         add any transcript missing from the journals
memo install         write the launchd agent that fires `memo sync --auto` on TP-7 plug-in
memo uninstall       remove it
memo status          config, model, memo dir, launchd agent state, last sync
```

## Automatic trigger

A user LaunchAgent (`~/Library/LaunchAgents/local.memo.sync.plist`) uses
`LaunchEvents → com.apple.iokit.matching` on `IOUSBDevice` with vendor 0x2367
and product 0x8019 (audio/MIDI personality, what the recorder enumerates as
when plugged in on firmware 2.5.x) and 0x0019 (MTP personality). launchd runs
`memo sync --auto` whenever either appears.

launchd treats an IOKit match as a level, not an edge: while a matching device
is attached it re-spawns the job every `ThrottleInterval` seconds, and
`IOMatchLaunchStream` makes no difference (observed on macOS 15.7). So the
`--auto` process does its sync and then stays alive, polling `tp7 devices`
every 5 s, until the recorder is unplugged. Two signals mean unplug: the
device absent for 30 s (closing a session takes it off USB for ~8 s on
firmware 2.5.7, so shorter gaps are re-enumeration), or a new IORegistry id
while in audio mode, which is how a quick replug looks. launchd therefore sees exactly one
run per plug-in, and the MTP session's own re-enumeration (0x0019 → 0x8019 on
close) never starts a second one. A `.memo/lock` (flock) guards against a
manual `memo sync` overlapping the automatic one: two MTP sessions on one
recorder time each other out. An earlier cooldown on `.memo/last-sync` was
removed: with one long-lived run per plug-in it only served to silently skip a
plug-in that followed a manual sync. `.memo/last-sync` is now purely the "last
sync" timestamp `memo status` prints.

Every completed run notifies, including one that found nothing ("no new
memos"), so a plug-in never looks like it did nothing.

A recorder plugged in while switched off enumerates as a bare USB
mass-storage device (product 0x0019, one bulk-only interface, no MIDI, the
kernel driver owns it), and stays that way until it is powered on, which
re-enumerates it in audio mode. The launchd match fires on 0x0019, so the
`--auto` run notifies "TP-7 is powered off; turn it on to sync" once and waits
for power-on before syncing. A manual `memo sync` waits 60 s, then gives up
with the same advice.

The agent's PATH must include `tp7`, `ffmpeg`, and `uv`/`memo`; `memo install`
bakes the resolved absolute paths into the plist.

## Transcription

- `ffmpeg -i in.wav -ar 16000 -ac 1 -f wav -` into a temp file.
- `mlx_whisper.transcribe(path, path_or_hf_repo=model)`; default model
  `mlx-community/whisper-large-v3-turbo`, language auto-detected unless
  configured. The model loads once per `sync` run, not per file.
- Files longer than `max_minutes` (default 30) are pulled but skipped by
  transcription, and listed as skipped, so a long field recording never stalls
  a sync. `memo sync --no-pull --max-minutes 0` lifts the cap explicitly.

## Configuration

`~/.config/memo/config.toml`, every key optional; env `MEMO_*` overrides:

```toml
dir = "~/Memos"
journal_dir = "~/Memos"  # where the YYYY-MM-DD.md files go; defaults to dir
journal_template = ""    # file whose contents head a new journal ({{date}}, {{time}}, {{title}})
model = "mlx-community/whisper-large-v3-turbo"
language = ""            # "" = auto
remote_dirs = ["/recordings", "/memo"]   # missing ones are skipped silently
max_minutes = 30
max_pull_mb = 512
tp7 = "tp7"              # path to the tp7 binary
```

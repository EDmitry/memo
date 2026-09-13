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
   editor can read it too.

## Principles

- **Filesystem is the state.** No database. A memo is "pulled" when its audio
  file exists locally, and "transcribed" when its transcript JSON exists. Every
  step is idempotent and re-runnable; deleting a transcript re-transcribes it.
- **One MTP session per sync.** Each `tp7 -a ...` invocation performs the full
  MIDI switch → MTP → close lifecycle, which costs ~10 s and flips the device
  back to audio mode on close. `sync` therefore calls `tp7` exactly once:
  `tp7 -a pull <remote-dir> <audio-dir> --recursive --skip-existing --max-size ...`.
- **The device layer stays in `tp7`.** Anything about USB, MTP, or the TP-7
  belongs in the Rust CLI. `memo` only shells out to `tp7 --json`.
- **Transcripts are the local durable record; the journal is the shared one.**
  `memo rebuild` regenerates a day's entries from transcript JSON. Every
  rendered entry also carries the memo's identity as an Obsidian block id, so a
  journal folder shared between machines is enough for either of them to adopt
  entries it never transcribed itself.
- **CLI conventions follow clig.dev** like `tp7`: concise human output,
  `--json` where useful, diagnostics on stderr, non-zero exit on failure.

## Layout

```
~/Memos/                          # MEMO_DIR (config: dir)
  2026-09-12.md                   # daily journal, one H2 per memo (see journal_dir)
  audio/2026-09-12_170312_000.wav # original pulled audio, never modified
  .memo/transcripts/2026-09-12_170312_000.json  # whisper output + metadata
  .memo/sync.log                  # append-only log of automatic runs
  .memo/last-sync                 # when the last run finished, shown by `memo status`
  CLAUDE.md                       # written once by `memo sync`, explains the layout to an agent
```

`journal_dir` moves the `YYYY-MM-DD.md` files somewhere else — a folder inside
an Obsidian vault, typically. Nothing else moves: audio, transcripts, sync
state and `CLAUDE.md` always stay in `dir`.

Journal entry:

```markdown
## 17:03 · 0:42

Remember to refactor the transcription pipeline tomorrow. ...

^memo-2026-09-12-170312-000
```

The trailing block id is the memo's identity in the journal: the audio stem
with every character outside `[A-Za-z0-9-]` replaced by `-`. Obsidian hides it
in reading view and links to it as
`[[2026-09-12#^memo-2026-09-12-170312-000]]`.

## Journals in an Obsidian vault

Set `journal_dir` to a folder inside the vault and the daily journals are vault
notes like any other. Two rules keep that safe:

- **memo owns the entries, not the file.** A journal file is a *head* —
  frontmatter, an H1, anything typed above the first entry — followed by the
  entries. `append_entry` appends at EOF; `rebuild` replaces everything from
  the first `## HH:MM · M:SS` heading to EOF and copies the head through byte
  for byte, so a `modified:` field an Obsidian plugin maintains survives.
- **memo creates a day file the way the vault expects.** `journal_template`
  points at a file whose contents become the head of a new journal, with
  `{{date}}`, `{{time}}` and `{{title}}` filled in (unknown `{{...}}` are left
  alone). Unset, the head is the plain `# YYYY-MM-DD` of standalone mode.

`memo open` hands the file to Obsidian (`open "obsidian://open?path=…"`) when
`journal_dir` has a `.obsidian` directory in some ancestor, and falls back to
`$EDITOR` otherwise. `memo rebuild` migrates an existing set of transcripts
into the new location.

## Two machines, one vault

Two Macs can share one recorder if they share the journal folder through
iCloud, Obsidian Sync or git. Each keeps its own `dir` — its own audio and its
own transcripts — and only the `.md` files are assumed to sync, since hidden
folders like `.memo/` may not.

That is why identity lives in the journal. `memo sync` (after the pull, before
transcription) and `memo rebuild` first *adopt*: every journal entry whose
block id has no local transcript becomes one here, reconstructed from the
entry — time and duration from the heading, text from the paragraph, named
after the local audio file if this machine has it and after the block id
otherwise, and marked `"adopted": true`. Transcription then finds nothing to
do for those memos, so neither machine transcribes the other's recordings and
`rebuild` on either regenerates the complete day. Audio is still pulled on both
machines, which makes the second copy a backup.

An adopted transcript renders back to exactly the entry it came from, so
adoption never rewrites the journal. The rule for hand edits follows from
which record owns the entry: fixing a mishearing in Obsidian sticks on every
machine that adopted it, and is reverted by `rebuild` on the machine that
transcribed it. The remaining window is sync lag — a memo can still be
transcribed twice if both machines sync it before the journal reaches the
other one.

The memo timestamp comes from the TP-7 filename (`YYYY-MM-DD_HHMMSS_NNN.wav`);
the MTP modified date is the fallback. Duration comes from the WAV header.

Transcript JSON: `{ "name", "recorded_at", "duration_s", "language", "model",
"text", "segments": [{start, end, text}], "transcribed_at" }`.

## Commands

```
memo sync            pull new memos from a connected TP-7, transcribe, append to journal
                     --auto      launchd mode: quiet, notifies on completion, waits for unplug
                     --no-pull   only transcribe audio already in audio/
memo show            print recent memos, grouped by day, newest day last
                     -n N | --today | --since 3d|2026-09-01 | --all | --json
memo ls              one line per memo: date, time, duration, first words
memo open            open today's journal (or --dir); Obsidian when it lives in a vault
memo rebuild         regenerate journal files from transcripts
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

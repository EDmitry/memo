# memo

`memo` turns [TP-7](https://teenage.engineering/products/tp-7) voice memos into
text you can read on one screen and feed to an AI. Plug the recorder in, and
new memos are pulled, transcribed locally with Whisper, and appended to a daily
Markdown journal — no typing, no cloud.

It is a thin pipeline over two tools: the `tp7` CLI for device access over MTP,
and [`mlx-whisper`](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
for transcription on Apple silicon.

## Install

```sh
brew install totocaster/tap/tp7 ffmpeg uv
uv tool install git+https://github.com/EDmitry/memo
memo install      # optional: sync automatically whenever the TP-7 is plugged in
```

For development, `uv tool install --editable .` from a checkout.

Requires macOS on Apple silicon, `tp7` and `ffmpeg` on `PATH`, and Python 3.12+.
The first transcription downloads the Whisper model (~1.5 GB) into the Hugging
Face cache.

## Quick start

```sh
memo sync                 # pull from a connected TP-7, transcribe, append to the journal
memo show                 # recent memos, grouped by day
memo show --since 7d      # everything from the last week
memo ls                   # one line per memo
memo open                 # today's journal in $EDITOR
```

The memo directory (`~/Memos` by default) is plain files:

```
~/Memos/
  2026-09-12.md                                 daily journal, one H2 per memo
  audio/2026-09-12_170312_000.wav               the original recording, never modified
  .memo/transcripts/2026-09-12_170312_000.json  whisper output + metadata
  CLAUDE.md                                     written once, so `cd ~/Memos && claude` has context
```

Transcripts are the durable record and the journal is a rendered view of them:
`memo rebuild` regenerates every day file, and deleting a transcript makes the
next `memo sync` transcribe that memo again.

## Automatic sync on plug-in

```sh
memo install     # write and load the LaunchAgent
memo status      # config, model, agent state, last sync
memo uninstall
```

`memo install` writes `~/Library/LaunchAgents/local.memo.sync.plist`, which asks
launchd to run `memo sync --auto` whenever the TP-7 appears on USB. A macOS
notification reports how many memos arrived — or says "no new memos" when
there was nothing to fetch; `.memo/sync.log` keeps the history. The automatic
run stays alive until the recorder is unplugged (launchd would otherwise
re-spawn it every 10 s while the device is attached), so there is exactly one
run per plug-in, and a lock keeps a manual `memo sync` from overlapping it.

## Configuration

`~/.config/memo/config.toml`, every key optional; the matching `MEMO_*`
environment variable overrides it (`MEMO_DIR`, `MEMO_MODEL`, …):

```toml
dir = "~/Memos"
model = "mlx-community/whisper-large-v3-turbo"
language = ""                            # "" = auto-detect
remote_dirs = ["/recordings", "/memo"]   # missing ones are skipped silently
max_minutes = 30                         # longer recordings are pulled but not transcribed
max_pull_mb = 512
tp7 = "tp7"                              # path to the tp7 binary
```

## Feeding memos to Claude

```sh
memo show --since 7d | claude -p "Group these voice memos into themes and list every action item."
memo show --today | claude -p "What did I say I would do today?"
cd ~/Memos && claude       # CLAUDE.md explains the layout
```

Output is plain text when stdout is not a terminal, so piping is safe.
`memo show --json` gives the transcripts with segments and timestamps.

## Development

```sh
uv run pytest
```

The tests use a fake `tp7` and a fake transcriber; none of them need the device
or the model.

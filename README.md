# talkie

**Push-to-talk dictation for macOS, through any speech model on OpenRouter.**

Hold a key, say a sentence, let go — the transcript is pasted wherever your cursor already is.
No switching apps, no "upload a file and wait".

```
  hold Ctrl+Q  ──▶  🎙 record  ──▶  OpenRouter  ──▶  ⌘V at the cursor
     release                          ~1 s
```

## Quick start

```sh
git clone https://github.com/KeerXiao/talkie.git
cd talkie
make install                                # deps, and PortAudio if it's missing

export OPENROUTER_API_KEY="sk-or-v1-..."    # https://openrouter.ai/keys — $5 lasts months

make check                                  # confirms the key and the permissions
make ui                                     # the app: window + menu-bar icon
```

Or `make run` for a plain terminal loop with no UI at all.

`make check` will tell you if macOS hasn't granted the permissions yet.
It needs three of them, they're independent, and two fail *silently* — [read this](#the-three-macos-permissions) before assuming the tool is broken.

You need [uv](https://docs.astral.sh/uv/) and Python 3.12+.
`make install` will point you at the one-line uv installer if it's missing.

### Every command

| Command | What it does |
|---|---|
| `make install` | Install dependencies (and PortAudio via Homebrew if needed) |
| `make check` | Verify the API key and macOS permissions, then exit |
| `make ui` | Run the app — history window plus the menu-bar icon |
| `make run` | Terminal-only hotkey loop, no UI |
| `make web` | Rebuild the history window from `ui/` (needs Node — the output is committed, so you usually don't) |
| `make record` | Record 3 s and print the transcript — no hotkey, no paste. `SECONDS=10` to change |
| `make test` | Run the test suite |
| `make clean` | Remove caches and build output |
| `make` | List all of the above |

`make record` is the fastest way to test your microphone and the model without dealing with Accessibility at all.

## Why

Local dictation tools ([Handy](https://github.com/cjpais/Handy), MacWhisper) run Whisper or Parakeet on your machine.
That is private and free, but the accuracy gap to frontier cloud models is large and obvious once you have used both — roughly **2% word error rate versus 12%** on the [Artificial Analysis leaderboard](https://artificialanalysis.ai/speech-to-text).

talkie takes the other trade: send the audio out, get transcripts that don't need fixing.
Everything goes through [OpenRouter](https://openrouter.ai), so one API key reaches every speech model and switching between them is a one-line environment variable.

At current prices this costs about **$0.10 per hour of speech** — a heavy day of dictation is a few cents.

## Status

| Milestone | What | State |
|---|---|---|
| **M1** | End-to-end flow: hotkey → record → transcribe → paste | ✅ working |
| **M2** | History window + menu-bar icon (copy text, replay audio) | ✅ working |

## The app

`make ui` opens the history window and puts talkie in the dock, like any other Mac app.
In the dock it still shows up as `python3`, with the stock Python icon: both come from an `.app` bundle, and talkie doesn't ship one yet. Its application menu is correct, at least.
Closing the window doesn't quit — it hides, and the dock icon brings it back, because the hotkey has to keep working while the window is out of the way.
Quit with **Cmd+Q**, Ctrl+C, or the menu bar's **Quit talkie**.

It also puts a 🎙 in the **menu bar** — the strip at the very top of your screen, next to the clock.
That icon is the status display, because while you're dictating you're looking at some other app:

| Icon | Meaning |
|---|---|
| 🎙 | idle |
| 🔴 | recording |
| ⏳ | transcribing |
| ⚠️ | the last attempt failed |

Clicking it shows your hotkey, your model, and today's running total — clips, minutes, and cost.

The window lists every past dictation, newest first, grouped by day.
Each row can be copied, replayed, or deleted, and there's a search box over the transcripts.
**Failed clips are kept too**, with the error in place of the transcript and their audio still playable — so when something goes wrong you can hear exactly what the model was sent.

History lives in `~/.talkie/history/` as a `.wav` and `.json` pair per dictation, capped at the 50 most recent.
Nothing is uploaded anywhere except the transcription request itself.

## The three macOS permissions

This is the part that will actually cost you time, so it's worth reading.

talkie needs **three separate grants** under System Settings → Privacy & Security.
They are independent, and each one fails in a completely different way:

| Permission | Needed for | If it's missing |
|---|---|---|
| **Microphone** | recording | **Loud** — the audio stream raises an error immediately |
| **Input Monitoring** | seeing the hotkey | **Silent** — you hold the key and nothing at all happens |
| **Accessibility** | the synthetic ⌘V | **Silent** — everything works, the transcript comes back, and the paste does nothing |

Two things that make this confusing:

1. **The grant belongs to the app you launch talkie *from*, not to talkie.**
   Running it in Terminal grants Terminal; running it in an IDE grants that IDE.
   Look for `Terminal`, `iTerm`, `PyCharm`, or `Visual Studio Code` in the permission lists — not `talkie` or `python`.
2. **A running process never picks up a new grant.**
   After toggling any of these, fully quit that app and reopen it.

The Accessibility case is nasty enough that talkie checks for it explicitly.
Without the check, talkie would copy your transcript, press ⌘V into the void, and then politely restore your old clipboard 0.3 s later — destroying the text you just paid for.
Instead, when Accessibility is missing it leaves the transcript on the clipboard, tells you so, and lets you paste it by hand.

## Using it

With `make run` going, in any app:

1. **Hold** Ctrl+Q.
   You'll hear a click, and recording starts.
2. **Speak.**
3. **Release.**
   A second sound confirms the mic closed; the transcript arrives about a second later and pastes at your cursor.

Taps shorter than 0.3 s are ignored, so a stray keypress costs nothing.
Your previous clipboard contents are restored after the paste.
Failures beep and log — error text is never pasted into your document.

## Configuration

Environment variables only.

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | *(required)* | Your OpenRouter key |
| `TALKIE_MODEL` | `microsoft/mai-transcribe-2` | Any OpenRouter speech-to-text model ID |
| `TALKIE_HOTKEY` | `ctrl+q` | Push-to-talk chord, e.g. `ctrl+alt+d`, `f5` |
| `TALKIE_LANGUAGE` | `en` | Sent as the `language` hint; set empty to let the model auto-detect |
| `TALKIE_SOUND_VOLUME` | `1.0` | Cue volume. `0` turns the sounds off; `>1` amplifies |

`TALKIE_SOUND_VOLUME` *multiplies* your system output volume rather than replacing it, so it can't make a muted Mac audible — turn the Mac up first.

### Choosing a model

| Model | WER | Price | Notes |
|---|---|---|---|
| `microsoft/mai-transcribe-2` | 2.0% | $0.10/hr | Default — most accurate *and* cheapest |
| `openai/gpt-transcribe` | 3.3% | $0.0045/min | Worth A/B-ing on your own voice |
| `openai/whisper-large-v3-turbo` | ~12% | $3e-6/sec | Cheap smoke test, not for daily use |

Switching is just `export TALKIE_MODEL=...` — same code path, same request shape.

## Development

```sh
make test      # 158 tests — no microphone, network, or permissions needed
```

Tests live **beside the code they test**, Go-style: `audio.py` next to `audio_test.py`.
They're excluded from the built wheel.

```
src/talkie/
├── app.py          orchestration — the hold/release/transcribe/paste loop
├── audio.py        sounddevice capture → in-memory WAV
├── hotkey.py       pynput chord detection
├── paste.py        clipboard borrow → ⌘V → restore
├── permissions.py  macOS TCC probes
├── sound.py        afplay cues
├── history.py      ~/.talkie/history — atomic writes, 50-entry retention
├── config.py       environment → Config
├── cli.py          argument parsing and wiring
├── client/         OpenRouter HTTP — nothing above this imports requests
└── ui/             window host (pywebview), menu bar + dock + icon (PyObjC), js_api bridge
ui/                 the history window's TypeScript source (Vite)
```

The frontend builds to a single self-contained `src/talkie/ui/web/index.html`, which is committed — so running talkie never needs Node.
`make web` rebuilds it if you change the TypeScript.

Everything is injected rather than imported at the point of use, so the whole flow is testable without a microphone or a network.

## Known limitations

- **macOS only.** The hotkey, paste and cue layers all assume it.
- **No maximum recording length.** If a key-release event is ever missed, talkie keeps recording. Bounded in M2.
- **Push-to-talk only** — no toggle mode, no streaming, no partial results.
- **Not a bundled `.app` yet.** It runs from a terminal, so macOS attributes permissions to your terminal rather than to talkie. [SPEC.md](SPEC.md) §5.7 has the py2app plan.
- **Your audio leaves your machine.** That's the entire premise. If that's not acceptable, use a local tool.

## Design

[SPEC.md](SPEC.md) carries the full design: milestones, the OpenRouter request contract, the error and retry model, why the audio cues are fired the way they are, and the M2 architecture.

## License

MIT — see [LICENSE](LICENSE).

# talkie

**Push-to-talk dictation for macOS, through any speech model on OpenRouter.**

Hold a key, say a sentence, let go — the transcript is pasted wherever your cursor already is.
No window, no dock icon, no "upload a file and wait".

```
  hold Ctrl+Q  ──▶  🎙 record  ──▶  OpenRouter  ──▶  ⌘V at the cursor
     release                          ~1 s
```

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
| **M2** | Menu-bar icon + history window (copy text, replay audio) | 📋 specced, not built |

Today talkie runs from a terminal.
The design for the menu-bar app is written up in [SPEC.md](SPEC.md) §5.

## Requirements

- macOS (the hotkey, clipboard and paste paths are all macOS-specific)
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- An [OpenRouter API key](https://openrouter.ai/keys) with a little credit on it

## Install

```sh
git clone https://github.com/KeerXiao/talkie.git
cd talkie
uv sync
```

If `sounddevice` can't find PortAudio, install it: `brew install portaudio`.

Then set your key:

```sh
export OPENROUTER_API_KEY="sk-or-v1-..."
```

Check that the key works before blaming anything else:

```sh
uv run talkie --check
```

## Grant the three macOS permissions

This is the part that will actually cost you time, so it's worth reading.

talkie needs **three separate grants** under  System Settings → Privacy & Security.
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

## Run

```sh
uv run talkie
```

Then, in any app:

1. **Hold** Ctrl+Q.
   You'll hear a click, and recording starts.
2. **Speak.**
3. **Release.**
   A second sound confirms the mic closed; the transcript arrives about a second later and pastes at your cursor.

Taps shorter than 0.3 s are ignored, so a stray keypress costs nothing.
Your previous clipboard contents are restored after the paste.
Failures beep and log — error text is never pasted into your document.

Other commands:

```sh
uv run talkie --check       # verify the API key and permissions, then exit
uv run talkie --record 3    # record 3 s and print the transcript, no hotkey or paste
uv run talkie -v            # debug logging
```

`--record` is the fastest way to test the microphone and the model without dealing with Accessibility at all.

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
uv run pytest      # 89 tests — no microphone, network, or permissions needed
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
├── config.py       environment → Config
├── cli.py          argument parsing and wiring
└── client/         OpenRouter HTTP — nothing above this imports requests
```

Everything is injected rather than imported at the point of use, so the whole flow is testable without a microphone or a network.

## Known limitations

- **macOS only.** The hotkey, paste and cue layers all assume it.
- **No maximum recording length.** If a key-release event is ever missed, talkie keeps recording. Bounded in M2.
- **Push-to-talk only** — no toggle mode, no streaming, no partial results.
- **Your audio leaves your machine.** That's the entire premise. If that's not acceptable, use a local tool.

## Design

[SPEC.md](SPEC.md) carries the full design: milestones, the OpenRouter request contract, the error and retry model, why the audio cues are fired the way they are, and the M2 architecture.

## License

MIT — see [LICENSE](LICENSE).

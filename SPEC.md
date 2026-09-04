# talkie — Cloud Push-to-Talk Dictation Tool

**Spec v0.3 · 2026-09-04**

## 1. Goal

A minimal, open Python tool that replaces Handy for dictation but sends audio to a frontier cloud speech-to-text model for much higher accuracy than local Whisper/Parakeet.

Hold a hotkey → speak → release → transcribed text is pasted at the cursor in whatever app is focused.

## 2. Background / why

- Handy is local-only (Whisper GGML, Parakeet); no cloud or custom-endpoint support.
Small local models lag frontier cloud models noticeably.
- All cloud STT goes through **OpenRouter**, so one API key and one request shape reach every model, and switching models is a string change.
- Current accuracy leaders (Artificial Analysis STT leaderboard, Sept 2026), with the OpenRouter model IDs and list prices:

  | Model | WER | OpenRouter ID | Price | / 1k min |
  |---|---|---|---|---|
  | Microsoft MAI-Transcribe-2 | 2.0% | `microsoft/mai-transcribe-2` | $0.10/hr | ~$1.67 |
  | ElevenLabs Scribe v2 | 2.2% | *(not on OpenRouter)* | — | ~$3.67 |
  | Google Gemini 3.5 Transcribe | 2.6% | *(chat-completions audio only)* | — | ~$5.00 |
  | OpenAI GPT Transcribe | 3.3% | `openai/gpt-transcribe` | $0.0045/min | ~$4.50 |
  | Whisper Large V3 Turbo | ~12% | `openai/whisper-large-v3-turbo` | $3e-6/sec | ~$0.18 |

  MAI-Transcribe-2 is both the most accurate and the cheapest of these — it is the default.
  Whisper Turbo is listed as a cheap smoke-test model, not a candidate for daily use.

- Alternative considered: OpenWhispr (open source, BYOK cloud, custom ASR shim).
Worth trying in parallel; this tool is the "own it, and practice Python" route.

## 3. Milestones

The work is split so the audio → cloud → paste path is proven before any UI exists.

| Milestone | Name | Deliverable |
|---|---|---|
| **M1** | End-to-end flow | `talkie.py`, terminal-only, no UI. Hold hotkey → transcript pasted at cursor. |
| **M2** | Minimal UI | Menu-bar icon showing state + a history window over past interactions (copy text, replay audio). |
| Later | — | Toggle mode, streaming, Windows/Linux, custom vocabulary, LLM cleanup pass, model fallback chain. |

M1 must pass its acceptance criteria before M2 starts.
M2 adds a UI layer on top of M1's functions; it does not change the recording or transcription path, only observes it.

## 4. Milestone 1 — end-to-end flow

### 4.1 Scope

**In**
- macOS
- Push-to-talk (hold to record, release to transcribe)
- OpenRouter transcription backend, model selectable by env var
- Paste result at cursor via clipboard + ⌘V, restoring the previous clipboard
- Runs from the terminal in the foreground, logging each interaction

**Out** — everything in M2 and Later.

### 4.2 Architecture

```
hold Ctrl+Q ──► record mic ──► release ──► POST WAV to OpenRouter ──► paste text at cursor
```

Single file `talkie.py`, ~120–150 lines.

| Function | Responsibility | Library |
|---|---|---|
| `start_recording()` | Open 16 kHz mono mic stream on hotkey-down; append chunks to a list | `sounddevice`, `numpy` |
| `stop_recording() -> bytes` | Close stream on hotkey-up; pack chunks into an in-memory WAV | `wave` (stdlib), `io` |
| `transcribe(wav_bytes) -> str` | Base64 the WAV, POST to OpenRouter, return `text` | `requests`, `base64` |
| `paste_text(text)` | Save clipboard → set clipboard to text → simulate ⌘V → restore clipboard | `pyperclip`, `pynput` |
| `on_press` / `on_release` | Hotkey handlers; on release, run transcribe+paste in a worker thread | `pynput`, `threading` |
| `main()` | Load `OPENROUTER_API_KEY` from env, start keyboard listener, block | — |

### 4.3 OpenRouter backend

`POST https://openrouter.ai/api/v1/audio/transcriptions`, `Authorization: Bearer $OPENROUTER_API_KEY`, JSON body:

```json
{
  "model": "microsoft/mai-transcribe-2",
  "input_audio": { "data": "<raw base64 WAV>", "format": "wav" },
  "language": "en"
}
```

Response:

```json
{
  "text": "…",
  "usage": { "seconds": 9.2, "total_tokens": 113, "cost": 0.000508 }
}
```

- **JSON + base64, not multipart.** The recording is already WAV bytes in memory; base64 avoids a temp file.
The endpoint also accepts OpenAI-style `multipart/form-data` (`file` + `model`, 25 MB cap), which is the fallback if base64 sizing ever becomes a problem — dictation clips are far under it.
- **`data` is raw base64**, with no `data:audio/wav;base64,` prefix. This is the easy thing to get wrong.
- **No prompt.** This is a purpose-built transcription endpoint, not a chat model, so there is no "output only the transcript" instruction to write and no risk of the model prepending commentary or quotes.
- **`usage.cost`** comes back per request and is logged; M2 surfaces a running total.
- Only the `transcribe()` body is provider-specific.
Any other backend (a direct vendor API, a local server) is a swap of that one function.

### 4.4 Key decisions

- **Hotkey:** **Ctrl+Q**, held down, by default.
A chord rather than a bare modifier so it can't fire from ordinary modifier use; Ctrl+Q is unbound in macOS and in most Mac apps (⌘Q is quit).
Recording starts when both keys are down and stops when either is released.
Overridable by `TALKIE_HOTKEY`.
- **Mode:** hold (push-to-talk), matching Handy.
- **Audio format:** 16 kHz, mono, int16 PCM WAV — small, and the format the endpoint documents first.
- **Threading:** transcription runs off the listener thread so the hotkey stays responsive.
One recording at a time; ignore hotkey-down while a request is in flight.
- **Clip guard:** discard recordings shorter than 0.3 s (accidental taps) — no API call.
- **Timeout:** 30 s on the HTTP request; a hung network fails as an error rather than wedging the app.
- **Errors:** on API/network failure, log the status and response body to the terminal and play the system alert sound (`afplay`/`NSBeep`).
Never paste error text.
- **Config:** env vars only.

  | Var | Default | Purpose |
  |---|---|---|
  | `OPENROUTER_API_KEY` | *(required)* | Auth |
  | `TALKIE_MODEL` | `microsoft/mai-transcribe-2` | OpenRouter model ID |
  | `TALKIE_HOTKEY` | `ctrl+q` | Push-to-talk chord |
  | `TALKIE_LANGUAGE` | `en` | Passed through as `language`; unset to let the model auto-detect |

### 4.5 Acceptance criteria

1. Hold Ctrl+Q, say a sentence, release → text appears at cursor in TextEdit, Slack, and a terminal within ~2 s.
2. Previous clipboard contents are intact afterward.
3. A <0.3 s tap produces no API call and no paste.
4. With Wi-Fi off, a recording produces a beep and a terminal log line, nothing pasted.
5. Setting `TALKIE_MODEL=openai/whisper-large-v3-turbo` transcribes through the same code path with no other change.
6. Script runs for hours without leaking mic streams or threads.

## 5. Milestone 2 — minimal UI

### 5.1 Goal

Make the tool usable without a terminal window, and make past interactions inspectable the way Handy's history panel is: read the transcript, copy it, and replay the audio that produced it.

### 5.2 Shape

- **Menu-bar icon** (`rumps`) is the always-on surface — no dock icon, no main window.
- **History window** (native, via PyObjC — already a `rumps` dependency) opens from the menu.

```
[ 🎙 ]  menu bar
 ├ Status: Idle
 ├ Hotkey: Ctrl+Q
 ├ Model: microsoft/mai-transcribe-2
 ├ Today: 14 clips · 6.2 min · $0.01
 ├ Open History…
 └ Quit
```

Icon state: 🎙 idle · 🔴 recording · ⏳ transcribing · ⚠️ last attempt failed.

History window — newest first, one row per interaction:

| Time | Duration | Transcript | Actions |
|---|---|---|---|
| 16:42 | 2.1 s | "let's ship the spec" | Copy · ▶ Play · Delete |
| 16:39 | 1.4 s | "check the WER table" | Copy · ▶ Play · Delete |

Failed interactions appear too, with the error in place of the transcript, so a bad clip can be replayed and diagnosed.
Window footer carries a **Clear history** action.

### 5.3 Storage

- Location: `~/.talkie/history/`.
- One pair of files per interaction, named by UTC timestamp: `20260904T164210Z.wav` and `20260904T164210Z.json`.
- The JSON holds: start time, duration, model, latency, transcript, `usage.cost`, error (if any).
- **Retention: the 50 most recent interactions.** Older pairs are deleted after each new write and once at startup.
- Written on the worker thread after transcription returns — success or failure.
Storage is best-effort: a write error is logged and never blocks the paste.

### 5.4 Threading note

`rumps` owns the main thread (NSApplication run loop); the `pynput` listener and the transcription worker are separate threads.
All UI mutation — icon/title changes, history rows — must be marshalled onto the main thread (a `rumps.Timer` polling a queue, or `performSelectorOnMainThread_`).
The M1 recording and transcription code must not gain any UI calls; it publishes events onto a queue that the UI drains.

### 5.5 Acceptance criteria

1. Launching the app shows a menu-bar icon and no terminal window is needed.
2. The icon changes to 🔴 while recording and ⏳ while transcribing, and back to 🎙 within one frame of finishing.
3. After a dictation, the new row appears at the top of the history window with the correct transcript and duration.
4. Copy puts exactly the transcript on the clipboard; Play plays back the original audio.
5. A failed transcription still produces a row, marked with its error, whose audio is playable.
6. The menu's daily total matches the sum of `usage.cost` across today's history entries.
7. After 60 dictations, `~/.talkie/history/` holds exactly 50 pairs.
8. Quit from the menu stops the listener and closes the mic stream cleanly.

## 6. Dependencies

```
# M1
pip install sounddevice numpy requests pynput pyperclip
# M2 adds
pip install rumps
```

(`sounddevice` needs PortAudio: `brew install portaudio` if the wheel doesn't bundle it.
`rumps` pulls in PyObjC, which the history window also uses.
No vendor SDK is needed — OpenRouter is one HTTP POST.)

## 7. macOS permissions

The terminal app (or Python binary) running the script needs, under System Settings → Privacy & Security:
- **Microphone** — for recording
- **Accessibility** — for the global hotkey and simulated ⌘V
- **Input Monitoring** — sometimes required by `pynput` for key events

First run will prompt; if the hotkey silently does nothing, check Accessibility / Input Monitoring.

## 8. Cost estimate

MAI-Transcribe-2 at $0.10/hour of audio → an hour of actual speech per day ≈ **$0.10/day**, or roughly $0.0003 per 10-second clip.
OpenRouter bills at list price with no per-model subscription, so switching models only changes the rate.

## 9. Open questions

- Whether Ctrl+Q chord detection through `pynput` is reliable across apps that grab Ctrl (terminals, Emacs) — validate during M1; fall back to a different chord if not.
- Whether MAI-Transcribe-2 or GPT Transcribe wins on *this* microphone and accent — the model env var makes an A/B cheap once history exists in M2.
- Whether to configure an OpenRouter fallback model, so a provider outage degrades to Whisper instead of beeping. Deferred past M1.
- Whether to add an optional "clean up filler words" post-processing pass (would change verbatim behavior — off by default).
This would be a second OpenRouter call to a chat model, on the same key.
- Whether M2 should ship as a bundled `.app` (py2app) or stay a `python talkie.py` launch — deferred until the menu-bar UI works.

## 10. References

- Artificial Analysis STT leaderboard — https://artificialanalysis.ai/speech-to-text
- OpenRouter transcription tutorial — https://openrouter.ai/blog/tutorials/transcription-on-openrouter/
- OpenRouter STT model list + pricing — https://openrouter.ai/collections/speech-to-text-models
- OpenRouter audio API announcement — https://openrouter.ai/blog/announcements/announcing-audio-apis/
- Handy (local-only, Rust/Tauri) — https://github.com/cjpais/Handy
- OpenWhispr (open source, BYOK cloud) — https://github.com/OpenWhispr/openwhispr
- rumps (macOS menu-bar apps in Python) — https://github.com/jaredks/rumps

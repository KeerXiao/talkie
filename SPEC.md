# talkie — Cloud Push-to-Talk Dictation Tool

**Spec v0.4 · 2026-09-04**

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

An installable package under `src/talkie/`, driven by the `talkie` console script.
Each module owns one concern and depends only on the ones below it, so a backend or UI swap touches one file.

| Module | Responsibility | Key types |
|---|---|---|
| `config.py` | Resolve env vars once at startup; fail fast on a missing key | `Config`, `ConfigError` |
| `audio.py` | Mic capture → in-memory 16 kHz mono WAV | `Recorder`, `Clip` |
| `hotkey.py` | Chord parsing, macOS key normalisation, engage/disengage edges | `Chord`, `ChordListener` |
| `client/` | Backend layer: HTTP, auth, error mapping, retries | `TranscriptionClient`, `OpenRouterClient`, `Transcript`, `ClientError` |
| `paste.py` | Clipboard borrow → ⌘V → restore; failure beep | `Paster`, `beep()` |
| `permissions.py` | Probe the macOS grants that fail silently | `accessibility_trusted()` |
| `sound.py` | Non-blocking audible cues via `afplay` | `Player` |
| `app.py` | Wire the above into the push-to-talk loop; own the busy/recording state | `Talkie` |
| `cli.py` | Argument parsing, logging setup, entry point | `main()` |

Dependencies are injected into `Talkie`, so the state machine is tested against fake recorder/client/paster objects with no mic, network, or Accessibility grant.
M2 attaches its UI to `app.py` alone.

Tests sit beside the code they cover, Go style: `hotkey.py` and `hotkey_test.py` are neighbours.
`pyproject.toml` excludes `**/*_test.py` from the wheel so they never ship.

The backend lives behind `talkie/client/`:

| File | Holds |
|---|---|
| `client/base.py` | `TranscriptionClient` protocol and the `Transcript` value type — the seam another backend implements |
| `client/errors.py` | `ClientError` and its subclasses, each carrying `.status` and `.retryable` |
| `client/openrouter.py` | `OpenRouterClient`: request envelope, status→exception mapping, bounded retries |

Nothing above `client/` imports `requests` or sees an HTTP status.
`OpenRouterClient` accepts a `requests.Session` and a `base_url`, so the request shape and every failure path are asserted without a live call.

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
- **Cues:** the user is looking at another app, so state changes are audible — Tink on hotkey down ("listening"), Bottle on release ("heard you"), Basso on failure.
- **Playback via `afplay`,** the audio player at `/usr/bin/afplay` that ships with macOS, over the `/System/Library/Sounds/*.aiff` the OS already uses for its own alerts.
A Python audio library (`playsound`, `pygame`, `simpleaudio`) or PyObjC/CoreAudio would be a dependency and a build surface for the sake of a half-second noise; a subprocess to a binary that is guaranteed present is not.
- **Cues are fire-and-forget on a throwaway thread.**
`afplay` lives for the duration of the sound (~0.6 s), and the cue is triggered from the chord callback, so a blocking call there would delay the microphone opening by that much and clip the first word.
The thread also reaps the child, so no zombies accumulate over a long session.
- **Cue ordering:** the start cue fires *before* the mic opens and the stop cue *after* it closes.
This minimises but does not eliminate bleed into the recording — `afplay` needs ~50 ms to make its first sound, by which time the stream is live, so on laptop speakers the Tink lands in the clip.
Harmless for the STT models in practice; headphones or a lower volume remove it.
- **Volume is relative, not absolute.**
`afplay -v` multiplies the system output level rather than replacing it, so `TALKIE_SOUND_VOLUME` cannot make a muted Mac audible, and a low system volume compounds with it.
The default is therefore `1.0` (no attenuation); values above 1 amplify, for a Mac habitually turned down.
- **Errors:** the client maps each failure to a type — `AuthError` (401/403), `InsufficientCreditsError` (402), `RateLimitError` (429), `ServerError` (5xx), `NetworkError`, `ResponseError`.
`app.py` logs the message and plays the system alert sound (`afplay`); error text is never pasted.
- **Retries:** the client retries only what a second attempt can fix — rate limits, 5xx and transport failures — twice, with 0.5 s then 1 s backoff.
A bad key or an empty account fails immediately rather than making the user wait.
- **Config:** env vars only.

  | Var | Default | Purpose |
  |---|---|---|
  | `OPENROUTER_API_KEY` | *(required)* | Auth |
  | `TALKIE_MODEL` | `microsoft/mai-transcribe-2` | OpenRouter model ID |
  | `TALKIE_HOTKEY` | `ctrl+q` | Push-to-talk chord |
  | `TALKIE_LANGUAGE` | `en` | Passed through as `language`; unset to let the model auto-detect |
  | `TALKIE_SOUND_VOLUME` | `1.0` | Multiplies system output volume; `0` off, `>1` amplifies |

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
- **History window** is a TypeScript app rendered in a WebView (`pywebview`, which wraps WKWebView), opened from the menu.

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

**Why TypeScript in a WebView rather than a native `NSTableView`.**
The window is a scrolling list of text plus a play button, which HTML does in a fraction of the code that `NSTableViewDataSource` needs, and `<audio controls>` supplies seeking for free.
Search, virtual scrolling, and waveform rendering all stay cheap if they are ever wanted.
The cost is one dependency, a hand-maintained type declaration at the bridge, and chrome that is styled rather than native.
Alternatives rejected: Electron (~180 MB, and retiring the Python core contradicts §2), and a Tauri or Wails shell with a Python sidecar (TCC attribution does not cross to a sidecar for free — it must carry the same Team ID and `com.apple.security.inherit`, which is a lot of machinery for a personal tool).

### 5.3 Process architecture

**Decision: two processes.**
`rumps.App.run()` and `webview.start()` each create and run an `NSApplication` event loop, and macOS permits one per process.
They cannot share a process without dropping one of them down to raw PyObjC.
So "Open History…" spawns the window as a child process that exits when its window closes.

```
┌─ daemon (rumps) ─────────┐        ┌─ history window (pywebview) ─┐
│ hotkey · audio · API     │        │  Api()  ←js_api→  TS/React   │
│ paste · writes history   │        │  reads + mutates the files   │
└──────────┬───────────────┘        └──────────────┬───────────────┘
           │                                       │
           └──────►  ~/.talkie/history/  ◄─────────┘
                     20260904T164210Z.{wav,json}
```

**The filesystem is the IPC.**
Every interaction is already a JSON+WAV pair on disk (§5.5), so the window lists a directory rather than speaking a protocol.
Copy uses `pyperclip` in the window's own process, delete unlinks the pair, and live updates come from polling the directory every ~500 ms.
There is no socket, no port, and no message format to design or version.

The daemon never reads back from the window; the window is a pure consumer plus destructive file operations.

### 5.4 The TS ↔ Python bridge

Inside the window process, `pywebview` injects an object into the page and turns every public method into a Promise.
Arguments and return values cross as JSON.

```python
# src/talkie/ui/api.py
class Api:
    def list_history(self) -> list[dict]: ...   # newest first, no audio payload
    def clip_audio(self, clip_id: str) -> str: ...  # "data:audio/wav;base64,..."
    def copy(self, clip_id: str) -> bool: ...
    def delete(self, clip_id: str) -> bool: ...
    def clear(self) -> int: ...

webview.create_window("Talkie History", "ui/dist/index.html", js_api=Api())
webview.start()   # must be the main thread
```

```ts
// ui/src/bridge.d.ts — the one seam with no type checker behind it
interface TalkieApi {
  list_history(): Promise<Interaction[]>
  clip_audio(id: string): Promise<string>
  copy(id: string): Promise<boolean>
  delete(id: string): Promise<boolean>
  clear(): Promise<number>
}
declare global { interface Window { pywebview: { api: TalkieApi } } }
```

Three constraints that shape the frontend:

| Constraint | Consequence |
|---|---|
| `window.pywebview` is injected late | every call is gated on the `pywebviewready` event |
| Methods starting with `_` are not exposed | private helpers stay private without extra work |
| The bridge is JSON only | audio cannot cross as bytes — it is a base64 data URI fetched lazily on ▶, since eager-loading 50 clips would be ~6 MB |

Python pushes to the page in the other direction with `window.evaluate_js`, calling a function the frontend registers:

```python
window.evaluate_js(f"window.talkie?.onHistoryChanged({json.dumps(ids)})")
```

`js_api` methods run on a `pywebview` worker thread, not the main thread, so anything touching AppKit must be marshalled.

**Escape hatch, if the TS iteration loop hurts.**
`js_api` requires relaunching the app to see a frontend change.
The alternative is a loopback `http.server` on `127.0.0.1:<random>` behind a token, with the frontend using plain `fetch()` — which buys `vite dev` with hot reload in a normal browser against the real backend, and lets `<audio src="http://…/clip/ID.wav">` stream instead of carrying base64.
It costs an open port and a token to guard it.
`Api` is therefore written standalone, importing nothing from `pywebview`, so it can be wrapped by an HTTP handler instead of injected without touching its body.
Start with `js_api`; switch only if relaunch-per-edit becomes the bottleneck.

### 5.5 Storage

- Location: `~/.talkie/history/`.
- One pair of files per interaction, named by UTC timestamp: `20260904T164210Z.wav` and `20260904T164210Z.json`.
- The JSON holds: start time, duration, model, latency, transcript, `usage.cost`, error (if any).
- **Retention: the 50 most recent interactions.** Older pairs are deleted after each new write and once at startup.
- Written on the worker thread after transcription returns — success or failure.
Storage is best-effort: a write error is logged and never blocks the paste.

Because this directory is also the interface to the history window (§5.3), the JSON shape is a contract, not an implementation detail.
A partially written pair must never be visible: write to a temp name and `os.replace` it into place.

### 5.6 Threading note

`rumps` owns the daemon's main thread (NSApplication run loop); the `pynput` listener and the transcription worker are separate threads.
All UI mutation — icon and title changes — must be marshalled onto the main thread (a `rumps.Timer` polling a queue, or `performSelectorOnMainThread_`).
The M1 recording and transcription code must not gain any UI calls; it publishes events onto a queue that the UI drains.

In the window process, `webview.start()` owns the main thread and blocks until the window closes, at which point the process exits.

### 5.7 Packaging

M2 ships a real `.app`, because macOS permissions attach to a bundle identity rather than to a script.
Today the grants are pinned to whatever host app launched talkie (PyCharm, a terminal), which is fragile and confusing — see §7.

- **Builder: `py2app`**, with `LSUIElement = 1` in `Info.plist` so there is a menu-bar icon and no dock icon.
- **`NSMicrophoneUsageDescription` is mandatory** in `Info.plist`; without it the app dies on first microphone access.
- The built TS bundle (`ui/dist/`) ships as bundle data, so the frontend build is a step in the app build, not a runtime dependency.
- `sounddevice` ships a PortAudio `.dylib` that `py2app` frequently misses — it needs explicit inclusion, and a bundle that runs from source but fails packaged is almost always this.

Signing tiers, in the order they are likely to be wanted:

| Tier | Cost | Effect |
|---|---|---|
| unsigned / ad-hoc | free | the signature hash changes every build, so **TCC grants go stale silently** — the exact failure mode of §7 |
| self-signed cert in the login keychain | free | stable identity, so Microphone/Accessibility/Input Monitoring grants survive rebuilds |
| Developer ID + notarization | $99/yr | anyone can run it with no Gatekeeper warning |

**M2 targets the self-signed tier.**
Notarization is only worth it when the tool is handed to someone else.

### 5.8 Acceptance criteria

1. Launching the app shows a menu-bar icon and no terminal window is needed.
2. The icon changes to 🔴 while recording and ⏳ while transcribing, and back to 🎙 within one frame of finishing.
3. After a dictation, the new row appears at the top of the history window within ~1 s, without reopening it.
4. Copy puts exactly the transcript on the clipboard; Play plays back the original audio with a working scrubber.
5. A failed transcription still produces a row, marked with its error, whose audio is playable.
6. The menu's daily total matches the sum of `usage.cost` across today's history entries.
7. After 60 dictations, `~/.talkie/history/` holds exactly 50 pairs.
8. Closing the history window leaves the daemon running and dictation working; reopening it works a second time.
9. Quit from the menu stops the listener, closes the mic stream, and leaves no orphaned window process.
10. The signed `.app` holds its own entries under Microphone, Accessibility, and Input Monitoring, and those entries survive a rebuild and relaunch.

## 6. Dependencies

```sh
uv sync                  # installs the package and its deps into .venv
uv run talkie            # hotkey loop
uv run talkie --check    # verify the API key and permissions, then exit
uv run talkie --record 3 # dev aid: record 3s, print the transcript, no paste
uv run pytest            # 89 tests; no mic, network or permissions needed
```

Runtime deps: `sounddevice`, `numpy`, `requests`, `pynput`, `pyperclip`.
M2 adds `rumps` (menu bar) and `pywebview` (history window), plus a `ui/` Vite + TypeScript project built to `ui/dist/` and shipped as bundle data.

(`sounddevice` needs PortAudio: `brew install portaudio` if the wheel doesn't bundle it.
`rumps` and `pywebview` both pull in PyObjC; `pywebview` renders through the system WKWebView, so it embeds no browser engine.
No vendor SDK is needed — OpenRouter is one HTTP POST.)

## 7. macOS permissions

The terminal app (or Python binary) running the script needs, under System Settings → Privacy & Security:
- **Microphone** — for recording
- **Accessibility** — for the global hotkey and simulated ⌘V
- **Input Monitoring** — sometimes required by `pynput` for key events

The grant is per **host app** — the terminal or IDE talkie is launched from — and a running process does not pick up a new grant, so the app must be quit and reopened.

These three fail in completely different ways, which is why `permissions.py` probes rather than guesses:

| Missing | Symptom |
|---|---|
| Microphone | Loud — the stream raises on `start()`. |
| Input Monitoring | Silent — the hotkey never fires; no `recording…` line. |
| Accessibility | Silent — everything works, the transcript comes back, and ⌘V does nothing. |

The Accessibility case is the trap: without a check, `paste()` would restore the previous clipboard 0.3 s later and destroy the transcript it just paid for.
So when `AXIsProcessTrusted()` is false, talkie copies the text and stops — the transcript stays on the clipboard for a manual ⌘V — and says so at startup and in `--check`.

## 8. Cost estimate

MAI-Transcribe-2 at $0.10/hour of audio → an hour of actual speech per day ≈ **$0.10/day**, or roughly $0.0003 per 10-second clip.
OpenRouter bills at list price with no per-model subscription, so switching models only changes the rate.

## 9. Open questions

- Whether Ctrl+Q chord detection through `pynput` is reliable across apps that grab Ctrl (terminals, Emacs) — validate during M1; fall back to a different chord if not.
- Whether MAI-Transcribe-2 or GPT Transcribe wins on *this* microphone and accent — the model env var makes an A/B cheap once history exists in M2.
- Whether to configure an OpenRouter fallback model, so a provider outage degrades to Whisper instead of beeping. Deferred past M1.
- Whether to add an optional "clean up filler words" post-processing pass (would change verbatim behavior — off by default).
This would be a second OpenRouter call to a chat model, on the same key.
- Whether `rumps` and `pywebview` genuinely cannot share a process — §5.3 rests on the one-`NSApplication`-per-process rule, and a ~20 line spike should confirm it before the two-process split is built.
- Whether polling `~/.talkie/history/` every 500 ms is enough, or the window should watch it with FSEvents — start with polling, since 50 files is nothing.

## 10. References

- Artificial Analysis STT leaderboard — https://artificialanalysis.ai/speech-to-text
- OpenRouter transcription tutorial — https://openrouter.ai/blog/tutorials/transcription-on-openrouter/
- OpenRouter STT model list + pricing — https://openrouter.ai/collections/speech-to-text-models
- OpenRouter audio API announcement — https://openrouter.ai/blog/announcements/announcing-audio-apis/
- Handy (local-only, Rust/Tauri) — https://github.com/cjpais/Handy
- OpenWhispr (open source, BYOK cloud) — https://github.com/OpenWhispr/openwhispr
- rumps (macOS menu-bar apps in Python) — https://github.com/jaredks/rumps
- pywebview (native WebView windows for Python) — https://pywebview.flowrl.com/
- py2app — https://py2app.readthedocs.io/

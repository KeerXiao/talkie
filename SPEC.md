# talkie — Cloud Push-to-Talk Dictation Tool

**Spec v0.5 · 2026-09-04**

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

talkie is a **regular app**: a dock icon, a Cmd-Tab entry, and its window open at launch.
It keeps a menu-bar icon as well, because the state it reports is only useful while you are typing in some *other* app, with talkie's window behind it.

- **History window** is a TypeScript app rendered in a WebView (`pywebview`, which wraps WKWebView), open at launch and reopened from the dock icon or the menu.
- **Menu-bar icon** (a raw PyObjC `NSStatusItem`) is the always-on state surface.

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

**Decision: one process, and `pywebview` owns the run loop.**

An earlier draft of this spec called for two processes, on the assumption that a menu bar and a WebView could not share one `NSApplication`.
Three spikes showed that assumption was half right, and the half that was wrong is the half that mattered.

| Spike | Setup | Result |
|---|---|---|
| A | `rumps` owns the loop, `webview.start()` called from a menu callback | `start()` **silently returns in ~0.4 s** having done nothing — no window, no exception |
| B | `webview` owns the loop, menu bar is a raw `NSStatusItem` | both live happily in one process |
| C | as B, plus a persistent window that hides instead of closing | show → hide → show again all work; the loop survives hiding; `start()` returns only on `destroy()` |

So the conflict is real but one-sided: **`rumps` cannot host a WebView, and it is the replaceable half.**
`rumps` is dropped. The menu bar is ~40 lines of PyObjC against `NSStatusItem` and `NSMenu`, which is less code than a subprocess, an IPC contract, and their failure modes would have been.

```
┌─ one process ────────────────────────────────────────────────┐
│  main       webview.start()  — the single NSApplication loop │
│  listener   pynput chord detection                           │
│  worker     one per clip: transcribe → paste → write history │
│                                                              │
│  NSStatusItem ◄── callAfter ──┐                              │
│  HistoryWindow ◄── js_api ──► TS/React                       │
└────────────────────────────────┬─────────────────────────────┘
                                 │
                        ~/.talkie/history/
                        20260904T164210Z.{wav,json}
```

Two consequences follow from spike C, and both are load-bearing:

- **The window is never destroyed until quit.**
`webview.start()` returns when the last window goes away, and that return would end the process and take the menu bar with it.
- **Closing the window is intercepted and turned into a hide** (`events.closing` returning `False`).
Quit therefore has to remove that veto before calling `destroy()`, or the app could never exit.

### 5.4 The UI ↔ backend boundary

**`src/talkie/ui/api.py` is the interface.** Its public methods are the whole of what the window can ask for; `ui/src/bridge.d.ts` is the TypeScript view of the same thing.

The window is a WebView with no network and no filesystem access, so this boundary is the entire surface between the TypeScript in `ui/` and the Python in `src/talkie/`.
It has exactly two directions:

| Direction | Mechanism | Lives in |
|---|---|---|
| **calls** — UI asks, Python answers | `pywebview` injects `Api` as `window.pywebview.api`, every public method becoming a Promise | `ui/api.py` |
| **events** — Python tells, UI reacts | `evaluate_js` invokes a function the page registered on `window.talkie` | `ui/window.py` |

| Call | Returns | |
|---|---|---|
| `list_history()` | `Interaction[]` | newest first, no audio |
| `clip_audio(clip_id)` | `str \| None` | `data:audio/wav;base64,…` |
| `stats()` | `Stats` | today's totals, local midnight |
| `copy(clip_id)` | `bool` | mutates the clipboard |
| `delete(clip_id)` | `bool` | mutates |
| `clear()` | `int` | mutates |

One event goes the other way: `onHistoryChanged()`, no payload — the UI re-reads through `list_history`, which keeps one path for loading history instead of two.

Three constraints shape the frontend:

| Constraint | Consequence |
|---|---|
| `window.pywebview` is injected late | the first load is gated on the `pywebviewready` event |
| Methods starting with `_` are not exposed | private helpers stay private without extra work |
| The bridge is JSON only | audio cannot cross as bytes — it is a base64 data URI fetched lazily on ▶, since eager-loading 50 clips would be ~6 MB |

Two things follow from every public method on `Api` being reachable from the page: adding one widens what the window can do, so the surface is kept deliberate; and every argument arriving there is untrusted, which is why history ids are matched against `ID_RE` before they are turned into paths (§5.5).

`js_api` methods run on a `pywebview` worker thread, not the main thread, so anything touching AppKit must be marshalled.

**`bridge.d.ts` is kept in step by hand.**
It is the one seam in the project with no type checker behind it — a method renamed in `api.py` fails at runtime, not at build.
Change the two together.

**Transport is not part of the interface.**
`Api` imports nothing from `pywebview`, so the same class can be served over loopback HTTP without its body changing.
That is the escape hatch if the TS iteration loop hurts: `js_api` requires relaunching the app to see a frontend change, whereas an `http.server` on `127.0.0.1:<random>` behind a token buys `vite dev` with hot reload against the real backend, and lets `<audio src="http://…/clip/ID.wav">` stream instead of carrying base64.
It costs an open port and a token to guard it.
Start with `js_api`; switch only if relaunch-per-edit becomes the bottleneck.

**Known asymmetry.**
The backend tracks four states — idle, recording, transcribing, error — and pushes them to the menu bar, but there is no event carrying them to the window.
So a window left open during a dictation shows nothing until the finished row appears.
An `onState` event is the natural fix; it is not in yet because nothing in the UI consumes it.

### 5.5 Storage

- Location: `~/.talkie/history/`.
- One pair of files per interaction, named by UTC timestamp: `20260904T164210Z.wav` and `20260904T164210Z.json`.
- The JSON holds: start time, duration, model, latency, transcript, `usage.cost`, error (if any).
- **Retention: the 50 most recent interactions.** Older pairs are deleted after each new write and once at startup.
- Written on the worker thread after transcription returns — success or failure.
Storage is best-effort: a write error is logged and never blocks the paste.

The JSON shape is a contract rather than an implementation detail: the window renders these fields directly, and older entries written by an older build must still load.
A partially written pair must never be visible — write to a temp name and `os.replace` it into place, and write the WAV before its JSON so a listable entry always has playable audio behind it.
Ids arrive back from JavaScript, so they are validated against a strict pattern before ever becoming a path.

### 5.6 Threading note

Three threads, and only one of them may touch AppKit.

| Thread | Owns |
|---|---|
| main | `webview.start()` — the single `NSApplication` run loop |
| listener | `pynput` chord detection |
| worker | one per clip: transcribe, paste, write history |

State changes originate on the worker (a transcription finished) but the icon lives on the main thread, so every mutation is bounced across with `AppHelper.callAfter`.
The M1 recording and transcription code gains no UI calls: `Talkie` takes optional `on_state` and `on_record` observers and knows nothing about what implements them.
A raising observer is caught and logged — a broken UI must never stop dictation.

**Quitting, and why it took four fixes.**
A terminal-launched app that ignores Ctrl+C is unacceptable, and making it work meant unpicking four separate obstacles — each of which looked like the whole problem until the next one appeared.

| # | Obstacle | Fix |
|---|---|---|
| 1 | The main thread sits in Cocoa's run loop and executes no Python bytecode, so a pending handler never gets an instruction boundary to run at | a repeating `AppHelper.callLater` tick, 4×/second |
| 2 | CPython only trips its handler when the signal lands on the **main** thread; delivered elsewhere it is silently dropped | `pthread_sigmask` blocks both signals before any thread is created, so they inherit the block; main is unblocked again from inside the loop |
| 3 | AppKit resets the main thread's mask while `NSApplication` starts | do the unblock from inside the run loop, not before `webview.start()` |
| 4 | **Something below Python replaces the SIGINT disposition after startup** — WebKit, by elimination. `signal.signal()` before the loop is silently undone, and `getsignal()` still reports the Python handler, which is what makes this so hard to see | re-register the handlers on every pump tick, reclaiming the disposition |

Obstacle 4 was found by having the app signal *itself* from the main thread inside the loop: the handler was registered, the pump was ticking, delivery was guaranteed — and nothing fired.
That ruled out every explanation except a C-level override.

Quit therefore has three equivalent paths, all verified end to end: the menu's **Quit talkie** item, Cmd+Q, and SIGINT/SIGTERM.
`stop()` is idempotent because any of them can arrive.

**The application delegate.**
Being a regular app means macOS sends two messages a menu-bar accessory never sees, and `pywebview`'s own delegate answers neither correctly.
So it is replaced from inside the run loop, after `start()` has installed its own.

| Message | pywebview's answer | Why it is wrong here | Ours |
|---|---|---|---|
| `applicationShouldTerminate:` | asks the window's `closing` handlers | talkie vetoes closing to keep the run loop alive, so **Cmd+Q would silently do nothing** | run `stop()`, return `TerminateCancel` |
| `applicationShouldHandleReopen:hasVisibleWindows:` | not implemented | clicking the dock icon of a hidden-window app would do nothing | show the window |

Returning `TerminateNow` is not an option: AppKit would `exit()` without unwinding Python, skipping the listener and recorder teardown.
Cancelling and destroying the window ourselves returns `webview.start()` and ends the process normally, so every quit route lands on one shutdown path.

`pywebview` already sets `NSApplicationActivationPolicyRegular` during `start()`, which is what we want; it is set again alongside the delegate so nothing depends on that staying true.
The app still calls `activateIgnoringOtherApps_` when reopening the window, since a background app does not come forward on its own.

**Name and icon.**
Run from a bare interpreter, macOS calls us `python3` and shows the generic Python icon, because both normally come from an `.app` bundle's `Info.plist` and there is no bundle.
Only one of the three places this shows was fixable at runtime:

| Surface | Attempt | Result |
|---|---|---|
| application menu ("About talkie", "Quit talkie") | `CFBundleName` in the main bundle's info dictionary, set before `start()` builds the menu | ✅ works |
| dock icon | `setApplicationIconImage_` | ❌ the dock still draws the interpreter's icon |
| dock label and Cmd-Tab | `LSSetApplicationInformationItem` | ❌ no longer an exported symbol (Darwin 25.5) |

The icon case is worth recording precisely, because it looks like it works from inside the process.
`applicationIconImage()` reads back the image we set, and no error is raised — but the dock is unchanged.
An in-process read only echoes the setter, so it is not evidence; the check that mattered was looking at the dock.

Both remaining surfaces therefore need the real bundle in §5.7.
The drawing code is kept regardless: it is what generates the bundle's `.icns`, so the icon exists and is tested, it just has nowhere to be displayed yet.

### 5.7 Packaging

M2 ships a real `.app`, because macOS permissions attach to a bundle identity rather than to a script.
Today the grants are pinned to whatever host app launched talkie (PyCharm, a terminal), which is fragile and confusing — see §7.

- **Builder: `py2app`**. `LSUIElement` stays unset — talkie is a regular app and wants its dock icon.
- **`CFBundleName` and an `.icns`** move into `Info.plist`. Per §5.6 this is the only way to get talkie's own icon and name in the dock at all — every runtime route was tried and only the application menu could be fixed. The `.icns` is generated from `ui/branding.py`, so there is no second copy of the artwork to keep in sync.
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

1. Launching the app opens its window and puts an icon in the dock and the menu bar; no terminal window is needed.
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
M2 adds `pywebview` (history window) and PyObjC (menu bar), plus a `ui/` Vite + TypeScript project.
The frontend builds to a single self-contained `src/talkie/ui/web/index.html`, which is committed, so running talkie never requires Node.

(`sounddevice` needs PortAudio: `brew install portaudio` if the wheel doesn't bundle it.
`pywebview` pulls in PyObjC and renders through the system WKWebView, so it embeds no browser engine.
The frontend is inlined into one HTML file because WebKit blocks ES-module and asset loads over `file://` by CORS.
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
- ~~Whether `rumps` and `pywebview` can share a process~~ — **answered by spike, §5.3.** They cannot, but `rumps` is the replaceable half; one process with a raw `NSStatusItem` works. Polling and FSEvents are both moot: the window is pushed to directly.
- Whether the `js_api` relaunch-per-edit loop becomes annoying enough to justify the loopback HTTP server in §5.4.

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

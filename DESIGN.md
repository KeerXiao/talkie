# talkie — Design

**Design v0.1 · 2026-09-06**

How talkie is built.
*What* it has to do, and why the project exists at all, is in [SPEC.md](SPEC.md); this document assumes those requirements and does not restate them.

Where a decision was expensive to reach, the reasoning is kept rather than just the outcome — the cost of rediscovering it is the reason this file exists.

## 1. Module map

```
hold Ctrl+Q ──► record mic ──► release ──► POST WAV to OpenRouter ──► paste text at cursor
```

An installable package under `src/talkie/`, driven by the `talkie` console script.
Each module owns one concern and depends only on the ones below it, so a backend or UI swap touches one file.

| Module | Responsibility | Key types |
|---|---|---|
| `config.py` | Resolve env vars once at startup; fail fast on a missing key | `Config`, `ConfigError` |
| `settings.py` | The slice of `Config` the UI may change; validate, persist, layer over the environment | `Settings`, `SettingsStore`, `SettingsError` |
| `audio.py` | Mic capture → in-memory 16 kHz mono WAV | `Recorder`, `Clip` |
| `hotkey.py` | Chord parsing, macOS key normalisation, engage/disengage edges | `Chord`, `ChordListener` |
| `client/` | Backend layer: HTTP, auth, error mapping, retries | `TranscriptionClient`, `OpenRouterClient`, `Transcript`, `ClientError` |
| `paste.py` | Clipboard borrow → ⌘V → restore; failure beep | `Paster`, `beep()` |
| `permissions.py` | Probe the macOS grants that fail silently | `accessibility_trusted()` |
| `sound.py` | Non-blocking audible cues via `afplay` | `Player` |
| `history.py` | Persist and prune past interactions | `History`, `Interaction` |
| `app.py` | Wire the above into the push-to-talk loop; own the busy/recording state | `Talkie` |
| `ui/` | Menu bar, history window, and the bridge to the frontend | see §5 |
| `cli.py` | Argument parsing, logging setup, entry point | `main()` |

Dependencies are injected into `Talkie`, so the state machine is tested against fake recorder/client/paster objects with no mic, network, or Accessibility grant.
The UI attaches to `app.py` alone.

Tests sit beside the code they cover, Go style: `hotkey.py` and `hotkey_test.py` are neighbours.
`pyproject.toml` excludes `**/*_test.py` from the wheel so they never ship.

Runtime deps: `sounddevice`, `numpy`, `requests`, `pynput`, `pyperclip`, plus `pywebview` and PyObjC for the UI, and a `ui/` Vite + TypeScript project.
The frontend builds to a single self-contained `src/talkie/ui/web/index.html`, which is committed, so running talkie never requires Node.
(`sounddevice` needs PortAudio.
`pywebview` renders through the system WKWebView, so it embeds no browser engine.
The frontend is inlined into one file because WebKit blocks ES-module and asset loads over `file://` by CORS.
No vendor SDK is needed — OpenRouter is one HTTP POST.)

## 2. Transcription backend

The backend lives behind `talkie/client/`:

| File | Holds |
|---|---|
| `client/base.py` | `TranscriptionClient` protocol and the `Transcript` value type — the seam another backend implements |
| `client/errors.py` | `ClientError` and its subclasses, each carrying `.status` and `.retryable` |
| `client/openrouter.py` | `OpenRouterClient`: request envelope, status→exception mapping, bounded retries |

Nothing above `client/` imports `requests` or sees an HTTP status.
`OpenRouterClient` accepts a `requests.Session` and a `base_url`, so the request shape and every failure path are asserted without a live call.

### 2.1 Request and response

`POST https://openrouter.ai/api/v1/audio/transcriptions`, `Authorization: Bearer $OPENROUTER_API_KEY`, JSON body:

```json
{
  "model": "microsoft/mai-transcribe-2",
  "input_audio": { "data": "<raw base64 WAV>", "format": "wav" },
  "language": "en"
}
```

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
- **`usage.cost`** comes back per request; it is logged and totalled in the menu.
- Only the `transcribe()` body is provider-specific.
Any other backend — a direct vendor API, a local server — is a swap of that one function.

### 2.2 Errors and retries

Each failure maps to a type: `AuthError` (401/403), `InsufficientCreditsError` (402), `RateLimitError` (429), `ServerError` (5xx), `NetworkError`, `ResponseError`.
`app.py` logs the message and plays the system alert sound.

The client retries only what a second attempt can fix — rate limits, 5xx, transport failures — twice, with 0.5 s then 1 s backoff.
A bad key or an empty account fails immediately rather than making the user wait.

## 3. Recording, hotkey, and cues

- **Chord over bare modifier.** Ctrl+Q is unbound in macOS and in most Mac apps (⌘Q is quit).
Recording starts when both keys are down and stops when either is released.
- **Audio format:** 16 kHz, mono, int16 PCM WAV — small, and the format the endpoint documents first.
- **Threading:** transcription runs off the listener thread so the hotkey stays responsive.
- **Playback via `afplay`,** the player at `/usr/bin/afplay` that ships with macOS, over the `/System/Library/Sounds/*.aiff` the OS already uses for its own alerts.
A Python audio library (`playsound`, `pygame`, `simpleaudio`) or PyObjC/CoreAudio would be a dependency and a build surface for the sake of a half-second noise; a subprocess to a binary guaranteed to be present is not.
- **Cues are fire-and-forget on a throwaway thread.**
`afplay` lives for the duration of the sound (~0.6 s), and the cue is triggered from the chord callback, so a blocking call there would delay the microphone opening by that much and clip the first word.
The thread also reaps the child, so no zombies accumulate over a long session.
- **Cue ordering:** the start cue fires *before* the mic opens and the stop cue *after* it closes.
This minimises but does not eliminate bleed into the recording — `afplay` needs ~50 ms to make its first sound, by which time the stream is live, so on laptop speakers the Tink lands in the clip.
Harmless for the STT models in practice; headphones or a lower volume remove it.
- **Volume is relative, not absolute.**
`afplay -v` multiplies the system output level rather than replacing it, so `TALKIE_SOUND_VOLUME` cannot make a muted Mac audible, and a low system volume compounds with it.
The default is therefore `1.0`; values above 1 amplify, for a Mac habitually turned down.

Cue assignment: Tink on hotkey down ("listening"), Bottle on release ("heard you"), Basso on failure.

### 3.1 Probing the permissions

The three grants fail in completely different ways, which is why `permissions.py` probes rather than guesses.

| Missing | Symptom |
|---|---|
| Microphone | Loud — the stream raises on `start()`. |
| Input Monitoring | Silent — the hotkey never fires; no `recording…` line. |
| Accessibility | Silent — everything works, the transcript comes back, and ⌘V does nothing. |

The Accessibility case is the trap: without a check, `paste()` would restore the previous clipboard 0.3 s later and destroy the transcript it just paid for.
So when `AXIsProcessTrusted()` is false, talkie copies the text and stops — the transcript stays on the clipboard for a manual ⌘V — and says so at startup and in `--check`.

## 4. UI process architecture

**Decision: one process, and `pywebview` owns the run loop.**

An earlier draft assumed a menu bar and a WebView could not share one `NSApplication`, and called for two processes.
Three spikes showed the assumption was half right, and the half that was wrong is the half that mattered.

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
│  HistoryWindow ◄── js_api ──► TypeScript                     │
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

**Why TypeScript in a WebView rather than a native `NSTableView`.**
The window is a scrolling list of text plus a play button, which HTML does in a fraction of the code that `NSTableViewDataSource` needs, and `<audio controls>` supplies seeking for free.
Search, virtual scrolling, and waveform rendering all stay cheap if they are ever wanted.
The cost is one dependency, a hand-maintained type declaration at the bridge, and chrome that is styled rather than native.
Alternatives rejected: Electron (~180 MB, and retiring the Python core defeats the point of the project), and a Tauri or Wails shell with a Python sidecar (TCC attribution does not cross to a sidecar for free — it must carry the same Team ID and `com.apple.security.inherit`, a lot of machinery for a personal tool).

## 5. The UI ↔ backend boundary

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
| `get_settings()` | `SettingsForm` | current values *and* the choices to render |
| `copy(clip_id)` | `bool` | mutates the clipboard |
| `delete(clip_id)` | `bool` | mutates |
| `clear()` | `int` | mutates |
| `update_settings(patch)` | `SettingsResult` | mutates; validates, persists, applies |

One event goes the other way: `onHistoryChanged()`, no payload — the UI re-reads through `list_history`, which keeps one path for loading history instead of two.

Three constraints shape the frontend:

| Constraint | Consequence |
|---|---|
| `window.pywebview` is injected late | the first load is gated on the `pywebviewready` event |
| Methods starting with `_` are not exposed | private helpers stay private without extra work |
| The bridge is JSON only | audio cannot cross as bytes — it is a base64 data URI fetched lazily on ▶, since eager-loading 50 clips would be ~6 MB |

Two things follow from every public method on `Api` being reachable from the page: adding one widens what the window can do, so the surface is kept deliberate; and every argument arriving there is untrusted, which is why history ids are matched against `ID_RE` before they are turned into paths (§6).

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

## 6. History storage

One pair of files per interaction under `~/.talkie/history/`, named by UTC timestamp: `20260904T164210Z.wav` and `20260904T164210Z.json`.
Written on the worker thread after transcription returns, success or failure.

Three rules keep the directory readable by a future build and by a human:

- **A partially written pair must never be visible** — write to a temp name and `os.replace` it into place.
- **The WAV is written before its JSON**, so a listable entry always has playable audio behind it.
- **Ids arrive back from JavaScript**, so they are validated against a strict pattern before ever becoming a path.

Pruning to the 50 most recent runs after each new write and once at startup.
A write error is logged and swallowed; `record()` never raises into the dictation path.

## 7. Settings

`Config` is frozen and resolved once, which is right for a process that never changes its mind — and wrong the moment a user can change the language from a window.
`settings.py` is the reconciliation: a second, smaller value type naming exactly the fields the UI may touch, with `Config` still the single thing every component is handed.

```
Config.from_env()  ->  Settings.from_config()  ->  store.load()  ->  settings.apply_to(config)
   the environment        the editable slice        the saved file      what everything is built from
```

`resolve()` is that whole pipeline, and both builds call it from `cli.py`, so `talkie` and `talkie --ui` cannot drift apart about what is configured.

**The file wins over the environment.**
The UI is the user's most recent explicit choice; a shell export made months ago should not silently undo a dropdown touched a second ago.
The environment still seeds a first run, which is what keeps `TALKIE_MODEL=… talkie` useful for a one-off.

**The API key is not a setting.**
It never enters `Settings`, so it can never be written to `settings.json` — and because `merge` ignores keys it does not know, a patch arriving from JavaScript cannot introduce one either.

**Validation lives with the type, not the page.**
`Settings.merge` coerces and bounds every field and raises `SettingsError` carrying the offending field name; the page renders the message beside that control.
`Api.update_settings` catches it and returns a result instead, because an exception crossing the pywebview bridge arrives in JavaScript opaque, with nothing to point at.

**Reading is tolerant; writing is strict.**
A hand-edited file with one bad value loses that value and keeps the rest (`tolerant_merge`), and an unparseable file falls back to the environment rather than refusing to start.
A save is atomic, the same temp-and-`os.replace` as history, and reports failure rather than raising: the change has already applied to the running process, and the page says it will not survive a restart rather than pretending otherwise.

**Applying is live, and one-directional.**
`Talkie.apply(config)` rebinds the client, the cue volume and the history cap.
The client is rebuilt rather than mutated, because a client is bound to one model and language (§2) — `client_factory` is the seam that lets a test observe it.
It runs on the bridge thread while a dictation may be in flight, which is safe only because it does nothing but rebind: a clip already inside `transcribe()` finishes against the client it started with, so its history row names the model that actually did the work.

**The hotkey is not there.**
Every other setting is a value read at the moment it is used; the hotkey is held by a running pynput listener, so changing it means stopping that listener and starting another, and getting the engaged/pressed state right across the swap.
That is more machinery than the rest of the page combined, so it stays on `TALKIE_HOTKEY` until it is asked for.

## 8. Threading and shutdown

Three threads, and only one of them may touch AppKit.

| Thread | Owns |
|---|---|
| main | `webview.start()` — the single `NSApplication` run loop |
| listener | `pynput` chord detection |
| worker | one per clip: transcribe, paste, write history |

State changes originate on the worker (a transcription finished) but the icon lives on the main thread, so every mutation is bounced across with `AppHelper.callAfter`.
The recording and transcription code gains no UI calls: `Talkie` takes optional `on_state` and `on_record` observers and knows nothing about what implements them.
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
The app calls `activateIgnoringOtherApps_` when reopening the window, since a background app does not come forward on its own.

## 9. Name and icon

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

Both remaining surfaces therefore need the real bundle in §10.
`ui/branding.py` is kept regardless: it draws the artwork in code, which is what will generate the bundle's `.icns`, so the icon exists and is tested — it just has nowhere to be displayed yet.

## 10. Packaging

- **Builder: `py2app`**. `LSUIElement` stays unset — talkie is a regular app and wants its dock icon.
- **`CFBundleName` and an `.icns`** move into `Info.plist`. Per §9 this is the only way to get talkie's own icon and name in the dock at all.
The `.icns` is generated from `ui/branding.py`, so there is no second copy of the artwork to keep in sync.
- **`NSMicrophoneUsageDescription` is mandatory** in `Info.plist`; without it the app dies on first microphone access.
- The built TS bundle ships as bundle data, so the frontend build is a step in the app build, not a runtime dependency.
- `sounddevice` ships a PortAudio `.dylib` that `py2app` frequently misses — it needs explicit inclusion, and a bundle that runs from source but fails packaged is almost always this.

A thinner alternative to `py2app`, if it fights back: a hand-written bundle — `Info.plist`, an `.icns`, and a shell script that `exec`s `uv run talkie --ui`.
That buys the identity, the icon, and talkie's own TCC entries without a freezer in the loop; it costs a bundle that depends on the checkout still being there.

Signing tiers are a requirement, not a design choice — see SPEC §5.4.

## 11. Open technical questions

- Whether the `js_api` relaunch-per-edit loop becomes annoying enough to justify the loopback HTTP server in §5.
- ~~Whether `rumps` and `pywebview` can share a process~~ — **answered by spike, §4.** They cannot, but `rumps` is the replaceable half. Polling and FSEvents are both moot: the window is pushed to directly.
- Whether the four-state push to the menu bar should be widened into an `onState` event for the window (§5).

## 12. References

- pywebview (native WebView windows for Python) — https://pywebview.flowrl.com/
- rumps (macOS menu-bar apps in Python) — https://github.com/jaredks/rumps
- py2app — https://py2app.readthedocs.io/

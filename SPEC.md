# talkie — Cloud Push-to-Talk Dictation Tool

**Spec v0.6 · 2026-09-06** — requirements only.
How any of it is built lives in [DESIGN.md](DESIGN.md).

## 1. Goal

A minimal, open Python tool that replaces Handy for dictation but sends audio to a frontier cloud speech-to-text model, for much higher accuracy than local Whisper/Parakeet.

Hold a hotkey → speak → release → the transcript is pasted at the cursor in whatever app is focused.

## 2. Why

- Handy is local-only (Whisper GGML, Parakeet); no cloud or custom-endpoint support.
Small local models lag frontier cloud models noticeably.
- All cloud STT goes through **OpenRouter**, so one API key and one request shape reach every model, and switching models is a string change.
- Current accuracy leaders (Artificial Analysis STT leaderboard, Sept 2026), with OpenRouter model IDs and list prices:

  | Model | WER | OpenRouter ID | Price | / 1k min |
  |---|---|---|---|---|
  | Microsoft MAI-Transcribe-2 | 2.0% | `microsoft/mai-transcribe-2` | $0.10/hr | ~$1.67 |
  | ElevenLabs Scribe v2 | 2.2% | *(not on OpenRouter)* | — | ~$3.67 |
  | Google Gemini 3.5 Transcribe | 2.6% | *(chat-completions audio only)* | — | ~$5.00 |
  | OpenAI GPT Transcribe | 3.3% | `openai/gpt-transcribe` | $0.0045/min | ~$4.50 |
  | Whisper Large V3 Turbo | ~12% | `openai/whisper-large-v3-turbo` | $3e-6/sec | ~$0.18 |

  MAI-Transcribe-2 is both the most accurate and the cheapest of these — it is the default.
  Whisper Turbo is a cheap smoke-test model, not a candidate for daily use.

- **Bring-your-own-key, always.** Audio goes from the user's machine straight to the provider they chose; it never passes through a server this project operates.
That is what keeps the tool free, MIT, and free of any data-protection obligation, so it is a requirement rather than an implementation detail.
- Alternative considered: OpenWhispr (open source, BYOK cloud, custom ASR shim).
Worth trying in parallel; this tool is the "own it, and practice Python" route.

## 3. Milestones

The work is split so the audio → cloud → paste path is proven before any UI exists.

| Milestone | Name | Deliverable |
|---|---|---|
| **M1** | End-to-end flow | Terminal-only, no UI. Hold hotkey → transcript pasted at cursor. |
| **M2** | Minimal UI | A window over past interactions (copy text, replay audio), plus a menu-bar state icon. |
| Later | — | Toggle mode, streaming, Windows/Linux, custom vocabulary, LLM cleanup pass, model fallback chain. |

M1 must pass its acceptance criteria before M2 starts.
M2 observes M1's recording and transcription path; it does not change it.

## 4. Milestone 1 — end-to-end flow

### 4.1 Scope

**In** — macOS; push-to-talk; an OpenRouter backend with the model selectable by env var; paste at the cursor; run from a terminal in the foreground, logging each interaction.

**Out** — everything in M2 and Later.

### 4.2 Behaviour

- **Push-to-talk, not toggle.** Recording runs from hotkey-down to hotkey-up, matching Handy.
- **The hotkey is a chord**, `ctrl+q` by default, so it cannot fire from ordinary modifier use.
- **The clipboard is borrowed, not spent.** Whatever was on it before a dictation is there afterwards.
- **Accidental taps cost nothing.** A recording under 0.3 s is discarded without an API call.
- **State is audible.** The user is looking at another app, so start, success, and failure each have a distinct sound.
- **Failures are reported, never pasted.** An error produces a sound and a log line; error text never reaches the cursor.
- **A hung request fails rather than wedges** — within 30 s.
- **One dictation at a time.** The hotkey is ignored while a request is in flight.
- **Switching models is configuration, not code.**

### 4.3 Configuration

Env vars only.

| Var | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | *(required)* | Auth |
| `TALKIE_MODEL` | `microsoft/mai-transcribe-2` | OpenRouter model ID |
| `TALKIE_HOTKEY` | `ctrl+q` | Push-to-talk chord |
| `TALKIE_LANGUAGE` | `en` | Passed through as `language`; unset to let the model auto-detect |
| `TALKIE_SOUND_VOLUME` | `1.0` | Multiplies system output volume; `0` off, `>1` amplifies |

### 4.4 Acceptance criteria

1. Hold Ctrl+Q, say a sentence, release → text appears at the cursor in TextEdit, Slack, and a terminal within ~2 s.
2. Previous clipboard contents are intact afterward.
3. A <0.3 s tap produces no API call and no paste.
4. With Wi-Fi off, a recording produces a beep and a log line, nothing pasted.
5. Setting `TALKIE_MODEL=openai/whisper-large-v3-turbo` transcribes through the same code path with no other change.
6. It runs for hours without leaking mic streams or threads.

## 5. Milestone 2 — minimal UI

### 5.1 Goal

Make the tool usable without a terminal window, and make past interactions inspectable the way Handy's history panel is: read the transcript, copy it, replay the audio that produced it.

### 5.2 Shape

talkie is a **regular app** — a dock icon, a Cmd-Tab entry, and its window open at launch.
It keeps a menu-bar icon as well, because the state it reports is only useful while you are typing in some *other* app, with talkie's window behind it.

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
The footer carries a **Clear history** action.

Closing the window must not stop dictation, and quitting must be possible from the menu, Cmd+Q, and Ctrl+C alike.

### 5.3 History

- Kept under `~/.talkie/history/`, one audio file and one JSON file per interaction, named by UTC timestamp.
- The JSON holds start time, duration, model, latency, transcript, cost, and error if any.
- **Retention: the 50 most recent interactions.** Older ones are deleted.
- History is best-effort: a failed write is logged and never blocks the paste.
- The on-disk shape is a contract — entries written by an older build must still load.

### 5.4 Packaging and permissions

talkie must ship as a **signed `.app` bundle**, because macOS attaches permissions to a bundle identity rather than to a script.
Without one, the grants belong to whatever terminal or IDE launched talkie, and the dock shows a Python icon under a Python name.

Signing tiers, in the order they are likely to be wanted:

| Tier | Cost | Effect |
|---|---|---|
| unsigned / ad-hoc | free | the signature hash changes every build, so TCC grants go stale silently |
| self-signed cert in the login keychain | free | stable identity, so grants survive rebuilds |
| Developer ID + notarization | $99/yr | anyone can run it with no Gatekeeper warning |

**M2 targets the self-signed tier.**
Notarization is only worth it when the tool is handed to someone else.

### 5.5 Acceptance criteria

1. Launching the app opens its window and puts an icon in the dock and the menu bar; no terminal window is needed.
2. The icon changes to 🔴 while recording and ⏳ while transcribing, and back to 🎙 within one frame of finishing.
3. After a dictation, the new row appears at the top of the history window within ~1 s, without reopening it.
4. Copy puts exactly the transcript on the clipboard; Play plays back the original audio with a working scrubber.
5. A failed transcription still produces a row, marked with its error, whose audio is playable.
6. The menu's daily total matches the sum of the cost of today's history entries.
7. After 60 dictations, `~/.talkie/history/` holds exactly 50 pairs.
8. Closing the history window leaves dictation working; reopening it works a second time.
9. Quit from the menu stops the listener, closes the mic stream, and leaves no orphaned process.
10. The signed `.app` holds its own entries under Microphone, Accessibility, and Input Monitoring, and those entries survive a rebuild and relaunch.

## 6. macOS permissions

talkie needs three grants under System Settings → Privacy & Security:

| Grant | For |
|---|---|
| **Microphone** | recording |
| **Accessibility** | the global hotkey and the simulated ⌘V |
| **Input Monitoring** | key events, required by some `pynput` paths |

Until §5.4 ships, the grant belongs to the **host app** talkie was launched from, and a running process does not pick up a new grant — it must be quit and reopened.

Two of the three fail *silently*, which sets a requirement of its own: **talkie must probe each grant and say which one is missing**, at startup and on demand.
Without Accessibility in particular the transcript comes back and ⌘V does nothing, so talkie must leave the text on the clipboard rather than restore the old contents over it.

## 7. Cost

MAI-Transcribe-2 at $0.10/hour of audio → an hour of actual speech per day ≈ **$0.10/day**, or roughly $0.0003 per 10-second clip.
OpenRouter bills at list price with no per-model subscription, so switching models only changes the rate.

## 8. Open questions

- Whether Ctrl+Q chord detection is reliable across apps that grab Ctrl (terminals, Emacs) — fall back to a different chord if not.
- Whether MAI-Transcribe-2 or GPT Transcribe wins on *this* microphone and accent — the model env var makes an A/B cheap now that history exists.
- Whether to configure an OpenRouter fallback model, so a provider outage degrades to Whisper instead of beeping.
- Whether to add an optional "clean up filler words" pass — a second call to a chat model on the same key.
It changes verbatim behaviour, so it would be off by default.
- Whether the window should show live recording state, not just finished rows.

## 9. References

- Artificial Analysis STT leaderboard — https://artificialanalysis.ai/speech-to-text
- OpenRouter transcription tutorial — https://openrouter.ai/blog/tutorials/transcription-on-openrouter/
- OpenRouter STT model list + pricing — https://openrouter.ai/collections/speech-to-text-models
- OpenRouter audio API announcement — https://openrouter.ai/blog/announcements/announcing-audio-apis/
- Handy (local-only, Rust/Tauri) — https://github.com/cjpais/Handy
- OpenWhispr (open source, BYOK cloud) — https://github.com/OpenWhispr/openwhispr

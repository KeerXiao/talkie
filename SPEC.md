# talkie — Cloud Push-to-Talk Dictation Tool

**Spec v0.7 · 2026-09-23** — requirements only.
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

- **OpenRouter cannot stream.**
Its `/audio/transcriptions` endpoint is request/response only — no `stream` parameter, no SSE, no WebSocket, no partial results.
Watching a transcript appear while speaking therefore needs a provider spoken to directly, which is what M3 adds.
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
| **M3** | Live transcripts | A second provider that streams, and an overlay that shows the words while they are being spoken. |
| Later | — | Toggle mode, Windows/Linux, custom vocabulary, LLM cleanup pass, model fallback chain. |

M1, M2 and M3 are delivered.

M1 must pass its acceptance criteria before M2 starts.
M2 observes M1's recording and transcription path; it does not change it.
M3 is the first milestone that changes that path, and it does so behind a mode switch: the one-shot path stays exactly as M1 left it.

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

Env vars, layered under the settings page (5.5) where the two overlap.

| Var | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | *(required)* | Auth |
| `TALKIE_MODEL` | `microsoft/mai-transcribe-2` | OpenRouter model ID |
| `TALKIE_HOTKEY` | `ctrl+q` | Push-to-talk chord |
| `TALKIE_LANGUAGE` | `en` | Passed through as `language`; unset to let the model auto-detect |
| `TALKIE_SOUND_VOLUME` | `1.0` | Multiplies system output volume; `0` off, `>1` amplifies |
| `TALKIE_HISTORY_KEEP` | `50` | How many interactions history keeps |

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

### 5.5 Settings

A second tab in the window, alongside History, for the knobs a user actually
reaches for twice.
Everything else stays environment-only.

| Setting | Shape | Notes |
|---|---|---|
| Language | Dropdown, including **Auto-detect** | Auto sends no `language` field at all |
| Model | Free text, with the known-good ids suggested | OpenRouter's catalogue moves faster than a fixed list |
| Sound cues | Slider, 0–3× | 0 turns the cues off |
| Keep history | Number, 1–500 | Lowering it prunes immediately |

Four rules:

- **Saved to `~/.talkie/settings.json`,** and it wins over the environment — the UI is the more recent explicit choice.
  The environment still seeds a first run, before the file exists.
- **The API key is never written there.** It stays environment-only; a settings file that could leak a key is a liability the project does not want.
- **A change applies to the running app,** so the next thing you dictate already uses it. No restart, and no Save button.
- **Both builds read the same file,** so `talkie` and `talkie --ui` never disagree about what is configured.

The hotkey is deliberately not here yet — changing it means tearing down the
pynput listener, which is more machinery than the rest of the page put together.
`TALKIE_HOTKEY` still sets it.

### 5.6 Acceptance criteria

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
11. Switching Language to Chinese and dictating immediately afterwards transcribes as Chinese, with no restart.
12. That choice survives a quit and relaunch, and `talkie` in a terminal honours it too.
13. Lowering Keep history to 10 with 40 clips stored leaves exactly 10 pairs on disk.

## 6. Milestone 3 — live transcripts and a second provider

### 6.1 Goal

See the words while they are being spoken, so a mistake is caught during the dictation rather than after it has been pasted somewhere.

Today the only feedback during a recording is a sound.
The transcript appears at the cursor about a second after release, and checking it means reading whatever app was pasted into, or opening the history window.
Neither is available at the moment it would help.

### 6.2 Why this needs a second provider

OpenRouter is batch-only (§2), so no model reached through it can show a word before the recording ends.
OpenAI serves the same kind of models over a realtime WebSocket that emits partial text as audio arrives, and its key is already a credential a user of this tool is likely to hold.
It becomes the second provider, spoken to directly.

OpenRouter stays the default.
It is ten times cheaper for the same job (§8) and it prices every request in the response, which the history window depends on.

### 6.3 Scope

**In** — a provider choice; a per-model capability table; an explicit streaming/one-shot mode; an OpenAI backend for both modes; an on-screen overlay showing the live transcript; cost for models billed by duration.

**Out** — any provider beyond these two; keeping a socket open between dictations; word-level timestamps, speaker labels, or confidence; editing the transcript in the overlay; a local model.

### 6.4 Behaviour

- **The dictation gesture does not change.**
Hold, speak, release, and the transcript is pasted at the cursor.
Streaming changes what is visible during the hold, not what happens at the end of it.
- **One mode per clip, decided before the mic opens.**
A dictation is either streamed or sent as one finished file.
The two are never combined, and a failure in one is never retried through the other.
- **Capability belongs to the model, not the provider.**
A model that cannot stream does not offer streaming; a model that only exists in a realtime session does not offer one-shot.
A model id typed by hand is assumed one-shot, because guessing wrong fails mid-dictation with the mic already open.
- **The overlay never takes focus.**
It sits above other windows without becoming the frontmost app and without accepting clicks, because the app that has focus is the one the transcript is about to be pasted into.
- **The overlay serves both modes.**
Streaming fills it word by word; one-shot shows the finished transcript when it arrives.
Either way it removes the need to go and look somewhere else.
- **A streaming failure is a failed clip**, with the same sound, the same history row, and the same silence at the cursor as any other failure.
- **Keys stay environment-only**, one per provider, and are never written to disk.
- **Provider, model and mode are settings**, applying to the running app with no restart, like every other setting.
- **Cost is reported or it is blank.**
OpenRouter prices each request in its response.
OpenAI bills per minute of audio and reports the duration back, so the figure is arithmetic against a per-minute price held in the code.
A model priced per token shows no cost at all, rather than a number nobody can check.

### 6.5 Configuration

| Var | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | *(one key required)* | Auth for OpenRouter |
| `OPENAI_API_KEY` | *(one key required)* | Auth for OpenAI |
| `TALKIE_PROVIDER` | `openrouter` | Which cloud to use |
| `TALKIE_MODE` | `batch` | `stream` or `batch`, overridden by what the model supports |

At least one key must be set.
With both, OpenRouter wins; with one, that provider is selected — exporting a single key is an unambiguous choice.

### 6.6 Models and cost

| Provider | Model | Modes | Price | / hour |
|---|---|---|---|---|
| OpenRouter | `microsoft/mai-transcribe-2` | one-shot | reported per request | ~$0.10 |
| OpenAI | `gpt-live-transcribe` | **streaming** | $0.017/min | $1.02 |
| OpenAI | `gpt-realtime-whisper` | **streaming** | $0.017/min | $1.02 |
| OpenAI | `gpt-transcribe` | one-shot | $0.0045/min | $0.27 |
| OpenAI | `whisper-1` | one-shot | $0.006/min | $0.36 |
| OpenAI | `gpt-4o-transcribe`, `-mini` | one-shot | per token | *(not shown)* |

Live transcription costs roughly **ten times** the default.
That is the price of the feature, and it is why streaming is a choice rather than the new default.

`gpt-transcribe` and the 4o models also run inside a realtime session, but there they transcribe only once the turn is committed — which is what one-shot already does, over a socket.
They are listed as one-shot so the UI never promises partials that will not come.

### 6.7 Settings additions

| Setting | Shape | Notes |
|---|---|---|
| Provider | Dropdown | Says which variable is missing when a provider has no key |
| Model | Dropdown per provider, still free text | Suggestions come from the capability table |
| Mode | Two choices | Locked, with a reason, when the model supports only one |

### 6.8 Delivery

Six changes, each shippable on its own, in this order:

| # | Lands | Usable after it |
|---|---|---|
| 1 | Capability and price table; provider, key and mode config; one client factory | Nothing new — the table the rest is built against |
| 2 | OpenAI one-shot client | OpenAI usable end to end, no streaming |
| 3 | Realtime session, mic tap, streaming path in the app loop | Streaming works; partials only in the log |
| 4 | The overlay | The feature, visible |
| 5 | Provider, model and mode in the settings page | Configurable without env vars |
| 6 | README, SPEC and DESIGN brought up to date | — |

### 6.9 Acceptance criteria

1. With `gpt-live-transcribe` selected, holding the hotkey and speaking shows text on screen within ~1 s, growing as the sentence continues.
2. Releasing pastes the text the overlay last showed, into the app that had focus before the hotkey was pressed.
3. The overlay never becomes the frontmost app, and a click where it sits reaches the window underneath.
4. Selecting a one-shot-only model disables the streaming choice and says why; selecting a streaming-only model does the reverse.
5. Turning Wi-Fi off mid-dictation produces the failure sound and an error row, pastes nothing, and makes no second request through the other API.
6. A 60-second clip on `gpt-live-transcribe` records a cost of $0.017; a clip on `gpt-4o-transcribe` records no cost rather than $0.00.
7. Switching provider in settings takes effect on the next dictation, with no restart.
8. With only `OPENROUTER_API_KEY` set, OpenAI still appears in the settings page, and choosing it reports which variable to set instead of failing at the next dictation.
9. In one-shot mode the overlay still shows the finished transcript, so the feature is not conditional on paying for streaming.
10. Fifty consecutive streamed dictations leave no extra threads or sockets behind.

## 7. macOS permissions

talkie needs three grants under System Settings → Privacy & Security:

| Grant | For |
|---|---|
| **Microphone** | recording |
| **Accessibility** | the global hotkey and the simulated ⌘V |
| **Input Monitoring** | key events, required by some `pynput` paths |

Until §5.4 ships, the grant belongs to the **host app** talkie was launched from, and a running process does not pick up a new grant — it must be quit and reopened.

Two of the three fail *silently*, which sets a requirement of its own: **talkie must probe each grant and say which one is missing**, at startup and on demand.
Without Accessibility in particular the transcript comes back and ⌘V does nothing, so talkie must leave the text on the clipboard rather than restore the old contents over it.

## 8. Cost

MAI-Transcribe-2 at $0.10/hour of audio → an hour of actual speech per day ≈ **$0.10/day**, or roughly $0.0003 per 10-second clip.
OpenRouter bills at list price with no per-model subscription, so switching models only changes the rate.

Streaming is the expensive option and always will be: a realtime session is billed per minute of audio at roughly ten times the one-shot rate (§6.6).
An hour of speech a day through `gpt-live-transcribe` is about **$1/day** against $0.10 on the default.
Still small in absolute terms, but large enough that it stays an explicit choice rather than a default, and large enough to be worth seeing in the history window.

## 9. Open questions

- Whether Ctrl+Q chord detection is reliable across apps that grab Ctrl (terminals, Emacs) — fall back to a different chord if not.
- Whether MAI-Transcribe-2 or GPT Transcribe wins on *this* microphone and accent — the model env var makes an A/B cheap now that history exists.
- Whether to configure an OpenRouter fallback model, so a provider outage degrades to Whisper instead of beeping.
- Whether to add an optional "clean up filler words" pass — a second call to a chat model on the same key.
It changes verbatim behaviour, so it would be off by default.
- ~~Whether the window should show live recording state, not just finished rows.~~ — **answered by M3**, and not in the window.
The window is behind whatever app is being dictated into, so live state belongs in an overlay above everything, not in a tab nobody can see at the time.
- Whether a streaming session should stay open between dictations, trading an idle socket for the connect time at the start of each clip.
Push-to-talk clips are short enough that the connect overlaps the first words, so it is not obviously worth the reconnect machinery.
- Whether the overlay should be readable enough to proofread against, or deliberately peripheral so it does not pull attention off the app being typed into.
- Whether streaming's ten-times cost survives daily use, or whether live partials turn out to be a thing worth paying for only when drafting something long.

## 10. References

- Artificial Analysis STT leaderboard — https://artificialanalysis.ai/speech-to-text
- OpenRouter transcription tutorial — https://openrouter.ai/blog/tutorials/transcription-on-openrouter/
- OpenRouter STT model list + pricing — https://openrouter.ai/collections/speech-to-text-models
- OpenRouter audio API announcement — https://openrouter.ai/blog/announcements/announcing-audio-apis/
- OpenAI realtime transcription — https://developers.openai.com/api/docs/guides/realtime-transcription
- OpenAI speech-to-text (file endpoint) — https://developers.openai.com/api/docs/guides/speech-to-text
- OpenAI model pages, for the per-minute prices in §6.6 — https://developers.openai.com/api/docs/models/gpt-live-transcribe
- Handy (local-only, Rust/Tauri) — https://github.com/cjpais/Handy
- OpenWhispr (open source, BYOK cloud) — https://github.com/OpenWhispr/openwhispr

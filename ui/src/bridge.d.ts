/**
 * The Python bridge — the TypeScript view of src/talkie/ui/api.py.
 *
 * pywebview injects that class as `window.pywebview.api` and turns every public
 * method into a Promise. This file is the only seam in the project with no type
 * checker behind it: if a method changes in api.py, nothing here will complain
 * until it fails at runtime. Keep the two in step by hand.
 */

/**
 * One hold-talk-release, successful or not.
 * Values arrive raw; every bit of formatting happens on this side.
 */
export interface Interaction {
  /** Also the filename stem on disk, and a path guard. */
  id: string
  /** ISO-8601 with an offset. */
  startedAt: string
  /** Seconds of audio, 2dp. */
  duration: number
  /** The transcript. Empty when the attempt failed. */
  text: string
  /** Set iff the attempt failed. */
  error: string | null
  /** `error === null`. Sent so the UI never re-derives it. */
  ok: boolean
  /** USD, when the model reported it. */
  cost: number | null
  /** Empty when the request never reached a model. */
  model: string
  /** Round-trip seconds. */
  latency: number | null
}

/** Totals since local midnight. Drives the menu bar's summary line. */
export interface Stats {
  clips: number
  /** Total audio recorded, 1dp. */
  seconds: number
  /** USD, 6dp. */
  cost: number
  /** How many of `clips` carry an error. */
  failures: number
}

/**
 * The fields the settings page may change — the TypeScript view of
 * `talkie.settings.Settings`.
 *
 * snake_case, unlike Interaction, and deliberately: settings cross the bridge
 * and land in ~/.talkie/settings.json unreshaped, so one naming convention
 * serves the wire and the file. Interaction is camelCase because this side
 * reformats every field of it anyway.
 */
export interface Settings {
  /** A language code, or the literal 'auto' to let the model detect it. */
  language: string
  /** Which cloud to transcribe through: 'openrouter' or 'openai'. */
  provider: string
  /** A model id belonging to `provider`. Free text — see SettingsForm.models. */
  model: string
  /**
   * The preference: 'stream' or 'batch'. What actually runs is `runningMode`,
   * because a model offering only one way to be driven overrides this.
   */
  mode: string
  /** Multiplies system output volume. 0 silences the cues. */
  sound_volume: number
  /** How many interactions history keeps before pruning the oldest. */
  history_keep: number
}

/** One entry in the language dropdown. */
export interface Language {
  code: string
  label: string
}

/** One transcription model, as `talkie.providers` describes it. */
export interface Model {
  id: string
  label: string
  /** Which of 'stream' / 'batch' this model can be driven as. */
  modes: string[]
  /** What it runs as when the user has expressed no preference it can honour. */
  defaultMode: string
  /** USD per minute of audio, or null when the model is priced per token. */
  pricePerMinute: number | null
  note: string
}

/** One cloud talkie can transcribe through. */
export interface Provider {
  id: string
  label: string
  /** The environment variable holding its key. Named when the key is missing. */
  envVar: string
  defaultModel: string
  models: Model[]
  /** Whether any of its models stream. */
  streams: boolean
  note: string
}

/** One way a model can be driven. */
export interface Mode {
  id: string
  label: string
}

/** Everything the settings page needs to draw itself, in one call. */
export interface SettingsForm {
  settings: Settings
  /** Offered in the dropdown; an unlisted code still round-trips. */
  languages: Language[]
  /** Suggestions for the model field, which stays free text. */
  models: string[]
  /** The whole capability table, so the page can lock an impossible mode
   * without a round trip per keystroke. */
  providers: Provider[]
  modes: Mode[]
  /** What the current provider+model+mode combination actually runs as. */
  runningMode: string
  limits: { maxVolume: number; maxKeep: number }
}

/**
 * The outcome of one save. Never a rejected Promise: a thrown Python exception
 * arrives here opaque, with no field to point the message at.
 */
export type SettingsResult =
  | {
      ok: true
      /** False when the change applied but could not be written to disk. */
      persisted: boolean
      settings: Settings
      /** What the saved combination actually runs as. */
      runningMode: string
    }
  | {
      ok: false
      /** Which field was rejected; empty when the whole patch was unreadable. */
      field: string
      error: string
    }

/** Everything the window may ask of Python. */
export interface TalkieApi {
  /**
   * Every stored interaction, newest first.
   * Audio is deliberately excluded — see clip_audio.
   */
  list_history(): Promise<Interaction[]>
  /**
   * A `data:audio/wav;base64,...` URI for one clip, or null if the audio is gone.
   * Separate from list_history because the bridge is JSON: bytes cannot cross, so
   * the base64 of every clip would otherwise be built on every refresh.
   */
  clip_audio(clip_id: string): Promise<string | null>
  /** Today's totals, in the user's local timezone. */
  stats(): Promise<Stats>
  /**
   * Put one transcript on the system clipboard.
   * False when the clip is unknown or has no text.
   */
  copy(clip_id: string): Promise<boolean>
  /** Remove one interaction and its audio. False if it was already gone. */
  delete(clip_id: string): Promise<boolean>
  /** Remove every interaction. Returns how many were deleted. */
  clear(): Promise<number>
  /** Current settings plus the choices the form renders. */
  get_settings(): Promise<SettingsForm>
  /**
   * Validate, apply and persist a partial change; absent keys keep their value.
   * Takes effect on the running app immediately — no restart. A change that
   * cannot be applied (a provider whose key was never exported) is rolled back
   * and never written, so `ok: false` means nothing changed.
   */
  update_settings(patch: Partial<Settings>): Promise<SettingsResult>
}

/**
 * Everything Python may tell the window, unprompted.
 * The frontend assigns this to `window.talkie` once it is ready to listen;
 * Python invokes it with `evaluate_js` from window.py.
 */
export interface TalkieEvents {
  /**
   * A dictation finished — successfully or not — so the list is stale.
   * Carries no payload: the UI re-reads through list_history, which keeps one path
   * for loading history instead of two.
   */
  onHistoryChanged: () => void
}

declare global {
  interface Window {
    pywebview?: { api: TalkieApi }
    talkie?: TalkieEvents
  }
}

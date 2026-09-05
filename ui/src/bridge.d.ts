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

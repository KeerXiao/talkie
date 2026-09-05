/**
 * The Python bridge.
 *
 * pywebview injects `window.pywebview.api` from talkie/ui/api.py. This file is
 * the only seam in the project with no type checker behind it: if a method
 * changes there, nothing here will complain until it fails at runtime. Keep
 * the two in step by hand.
 */

export interface Interaction {
  id: string
  /** ISO-8601, UTC. Formatted for display on this side. */
  startedAt: string
  duration: number
  text: string
  error: string | null
  ok: boolean
  cost: number | null
  model: string
  latency: number | null
}

export interface Stats {
  clips: number
  seconds: number
  cost: number
  failures: number
}

export interface TalkieApi {
  list_history(): Promise<Interaction[]>
  /** A `data:audio/wav;base64,...` URI, or null when the audio is gone. */
  clip_audio(id: string): Promise<string | null>
  copy(id: string): Promise<boolean>
  delete(id: string): Promise<boolean>
  clear(): Promise<number>
  stats(): Promise<Stats>
}

declare global {
  interface Window {
    pywebview?: { api: TalkieApi }
    /** Python calls this via evaluate_js when a new clip lands. */
    talkie?: { onHistoryChanged: () => void }
  }
}

/**
 * The settings panel.
 *
 * Two decisions shape it:
 *
 * **There is no Save button.** Every control writes on change, the way a macOS
 * preference pane does, and Python applies it to the running app immediately —
 * so the next thing you dictate already uses the new language. A Save button
 * would imply a pending state that does not exist.
 *
 * **The panel owns its own DOM.** It builds once and mutates in place rather
 * than re-rendering, because a re-render while you are halfway through typing a
 * model id would take the caret with it.
 *
 * The choices — the language list, the limits — come from `get_settings()`
 * rather than being repeated here; settings.py is where they are defined.
 */
import type { Settings, SettingsForm, SettingsResult } from './bridge'
import { api, el } from './dom'

/** Cached so switching tabs does not blank the form while it refetches. */
let form: SettingsForm | null = null

const HINTS: Record<keyof Settings, string> = {
  language:
    'Pin this when you always dictate in one language — it stops the model ' +
    'guessing wrong on a short clip. Auto-detect handles switching mid-session.',
  model: 'Any OpenRouter transcription model id. Takes effect on the next clip.',
  sound_volume:
    'The start, stop and error cues. You are looking at another app while ' +
    'dictating, so these are the only feedback you get. 0 turns them off.',
  history_keep:
    'Older clips and their audio are deleted. Lowering this prunes right away.',
}

export function settingsPanel(): HTMLElement {
  const panel = el('main', 'settings')
  const status = el('p', 'settings-status')

  function draw(): void {
    if (!form) return
    panel.replaceChildren(
      field('Language', languageInput(), HINTS.language),
      field('Model', modelInput(), HINTS.model),
      field('Sound cues', volumeInput(), HINTS.sound_volume),
      field('Keep history', keepInput(), HINTS.history_keep),
      status,
    )
  }

  function report(result: SettingsResult, control: HTMLElement, field: keyof Settings): void {
    control.classList.toggle('invalid', !result.ok)
    if (!result.ok) {
      status.className = 'settings-status bad'
      status.textContent = result.error
      return
    }
    form = { ...form!, settings: result.settings }
    status.className = 'settings-status'
    status.textContent = result.persisted
      ? 'Saved.'
      : 'Applied, but it could not be written to ~/.talkie/settings.json — ' +
        'it will not survive a restart.'
    // Python may normalise what it stored (2.5 clamped, ' zh ' trimmed), so
    // show what actually landed rather than what was typed.
    const stored = result.settings[field]
    if (control instanceof HTMLInputElement || control instanceof HTMLSelectElement) {
      if (control.value !== String(stored)) control.value = String(stored)
    }
  }

  async function save(
    patch: Partial<Settings>,
    control: HTMLElement,
    field: keyof Settings,
  ): Promise<void> {
    status.className = 'settings-status'
    status.textContent = 'Saving…'
    try {
      report(await api().update_settings(patch), control, field)
    } catch {
      status.className = 'settings-status bad'
      status.textContent = 'Could not reach the app. Is it still running?'
    }
  }

  // -- controls -----------------------------------------------------------

  function languageInput(): HTMLSelectElement {
    const select = el('select', 'control')
    const current = form!.settings.language
    const known = form!.languages.some((l) => l.code === current)
    // A code set in the environment or by hand need not be in the list; keep
    // it selectable rather than silently switching the user to English.
    const options = known
      ? form!.languages
      : [...form!.languages, { code: current, label: current }]
    for (const { code, label } of options) {
      const option = el('option', undefined, label)
      option.value = code
      select.append(option)
    }
    select.value = current
    select.addEventListener('change', () =>
      save({ language: select.value }, select, 'language'),
    )
    return select
  }

  function modelInput(): HTMLElement {
    const wrap = el('div', 'inline')
    const input = el('input', 'control wide')
    input.type = 'text'
    input.value = form!.settings.model
    input.spellcheck = false
    input.autocapitalize = 'off'
    input.setAttribute('list', 'models')

    // A sibling, not a child: an input cannot contain elements, and the
    // suggestions only appear if the browser can resolve the id.
    const list = el('datalist')
    list.id = 'models'
    for (const model of form!.models) {
      const option = el('option')
      option.value = model
      list.append(option)
    }

    // On blur and on Enter, not on every keystroke: each save is a disk write
    // and a client rebuild, and half a model id is never valid anyway.
    const commit = () => {
      if (input.value.trim() !== form!.settings.model) {
        void save({ model: input.value }, input, 'model')
      }
    }
    input.addEventListener('change', commit)
    input.addEventListener('blur', commit)
    wrap.append(input, list)
    return wrap
  }

  function volumeInput(): HTMLElement {
    const wrap = el('div', 'inline')
    const range = el('input', 'control')
    range.type = 'range'
    range.min = '0'
    range.max = String(form!.limits.maxVolume)
    range.step = '0.1'
    range.value = String(form!.settings.sound_volume)

    const readout = el('span', 'readout', volumeLabel(form!.settings.sound_volume))
    // input fires per pixel dragged; change fires once on release, which is
    // the one worth a disk write.
    range.addEventListener('input', () => {
      readout.textContent = volumeLabel(Number(range.value))
    })
    range.addEventListener('change', () =>
      save({ sound_volume: Number(range.value) }, range, 'sound_volume'),
    )
    wrap.append(range, readout)
    return wrap
  }

  function keepInput(): HTMLInputElement {
    const input = el('input', 'control narrow')
    input.type = 'number'
    input.min = '1'
    input.max = String(form!.limits.maxKeep)
    input.step = '1'
    input.value = String(form!.settings.history_keep)
    input.addEventListener('change', () =>
      save({ history_keep: Number(input.value) }, input, 'history_keep'),
    )
    return input
  }

  panel.append(el('p', 'settings-status', 'Loading…'))
  void (async () => {
    try {
      form = await api().get_settings()
      draw()
    } catch {
      panel.replaceChildren(el('p', 'settings-status bad', 'Settings are unavailable.'))
    }
  })()
  if (form) draw()
  return panel
}

function volumeLabel(volume: number): string {
  if (volume === 0) return 'Off'
  return `${Math.round(volume * 100)}%`
}

function field(name: string, control: HTMLElement, hint: string): HTMLElement {
  const row = el('div', 'field')
  const heading = el('label', 'field-name', name)
  const target = control.querySelector('input, select') ?? control
  if (target instanceof HTMLElement && target.id === '') {
    target.id = `f-${name.toLowerCase().replace(/\s+/g, '-')}`
  }
  if (target instanceof HTMLElement) heading.htmlFor = target.id
  row.append(heading, control, el('p', 'hint', hint))
  return row
}

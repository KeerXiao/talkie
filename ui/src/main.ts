import './styles.css'
import type { Interaction } from './bridge'
import { api, button, el, flash } from './dom'
import { byDay, cost, duration, matches, time } from './format'
import { settingsPanel } from './settings'

const root = document.getElementById('app')!
/** Which tab is showing. History is what the window is for; settings is a detour. */
type View = 'history' | 'settings'
let view: View = 'history'
let entries: Interaction[] = []
let query = ''
let playing: string | null = null
let playingUri: string | null = null

async function load(): Promise<void> {
  try {
    entries = await api().list_history()
  } catch {
    entries = []
  }
  // Only redraw if the list is what is on screen. A dictation landing while
  // the settings tab is open would otherwise rebuild the form under the
  // user's caret, halfway through a model id.
  if (view === 'history') render()
}

async function play(entry: Interaction, trigger: HTMLButtonElement): Promise<void> {
  if (playing === entry.id) {
    playing = null
    playingUri = null
    render()
    return
  }
  trigger.disabled = true
  try {
    const uri = await api().clip_audio(entry.id)
    if (!uri) {
      flash(trigger, 'gone')
      return
    }
    // A real <audio controls> rather than a bare Audio(): the point of keeping
    // the clip is being able to scrub through it, not just hear it once.
    playing = entry.id
    playingUri = uri
    render()
  } catch {
    flash(trigger, 'failed')
  } finally {
    trigger.disabled = false
  }
}

function row(entry: Interaction): HTMLElement {
  const item = el('div', entry.ok ? 'row' : 'row failed')

  const meta = el('div', 'meta')
  meta.append(
    el('span', 'time', time(entry.startedAt)),
    el('span', 'dur', duration(entry.duration)),
  )
  const price = cost(entry.cost)
  if (price) meta.append(el('span', 'cost', price))

  const body = el('div', 'body')
  if (entry.ok) {
    body.append(el('p', 'text', entry.text))
  } else {
    body.append(el('p', 'error', entry.error ?? 'failed'))
  }
  if (playing === entry.id && playingUri) {
    const player = el('audio', 'player')
    player.controls = true
    player.autoplay = true
    player.src = playingUri
    player.addEventListener('ended', () => {
      playing = null
      playingUri = null
      render()
    })
    body.append(player)
  }

  const actions = el('div', 'actions')
  if (entry.ok && entry.text) {
    const copy = button('Copy', 'Copy this transcript', async () => {
      if (await api().copy(entry.id)) flash(copy, 'Copied')
    })
    actions.append(copy)
  }
  if (!entry.ok) {
    // Only on a failure: the audio is still on disk, so a clip that did not
    // come back is one click from being sent again rather than said again.
    const retry = button('Retry', 'Send this audio to the model again', async () => {
      retry.disabled = true
      retry.textContent = 'Sending…'
      const result = await api().retry(entry.id)
      // The row is rewritten in place, so the whole list is reloaded rather
      // than this one row patched — one path for loading history, as ever.
      await load()
      if (!result.ok) flash(retry, result.error ?? 'Failed')
    })
    actions.append(retry)
  }
  const isPlaying = playing === entry.id
  const playBtn = button(isPlaying ? '■ Close' : '▶ Play', 'Play the original audio', () =>
    play(entry, playBtn),
  )
  actions.append(playBtn)
  actions.append(
    button('Delete', 'Delete this clip and its audio', async () => {
      await api().delete(entry.id)
      await load()
    }),
  )

  item.append(meta, body, actions)
  return item
}

function tab(label: string, target: View): HTMLButtonElement {
  const b = el('button', view === target ? 'tab on' : 'tab', label)
  b.setAttribute('aria-current', String(view === target))
  b.addEventListener('click', () => {
    if (view === target) return
    view = target
    render()
    // Dictations that landed while this tab was hidden were fetched but not
    // drawn; pick them up now.
    if (target === 'history') void load()
  })
  return b
}

function header(): HTMLElement {
  const bar = el('header')
  const tabs = el('nav', 'tabs')
  tabs.append(tab('History', 'history'), tab('Settings', 'settings'))
  bar.append(tabs)

  // The search box and Clear all only mean anything over a list of clips.
  if (view !== 'history') return bar

  const search = el('input', 'search')
  search.type = 'search'
  search.placeholder = 'Search transcripts…'
  search.value = query
  search.addEventListener('input', () => {
    query = search.value
    render()
    // Rebuilding the list drops focus; put it back where the user is typing.
    const next = document.querySelector<HTMLInputElement>('.search')
    next?.focus()
    next?.setSelectionRange(next.value.length, next.value.length)
  })
  bar.append(search)

  if (entries.length) {
    bar.append(
      button('Clear all', 'Delete every clip', async () => {
        await api().clear()
        await load()
      }),
    )
  }
  return bar
}

function empty(): HTMLElement {
  const box = el('div', 'empty')
  box.append(
    el('p', 'big', query ? 'Nothing matches that.' : 'No dictations yet.'),
    el(
      'p',
      undefined,
      query ? 'Try a different search.' : 'Hold your hotkey and say something.',
    ),
  )
  return box
}

function render(): void {
  root.replaceChildren(header())
  if (view === 'settings') {
    root.append(settingsPanel())
    return
  }

  const visible = entries.filter((e) => matches(e, query))
  if (!visible.length) {
    root.append(empty())
    return
  }
  const list = el('main')
  for (const [label, group] of byDay(visible)) {
    list.append(el('h2', 'day', label))
    for (const entry of group) list.append(row(entry))
  }
  root.append(list)
}

window.talkie = { onHistoryChanged: () => void load() }

// The bridge is injected late; calling before this event throws.
if (window.pywebview?.api) void load()
else window.addEventListener('pywebviewready', () => void load())

render()

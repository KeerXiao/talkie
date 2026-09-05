import './styles.css'
import type { Interaction, TalkieApi } from './bridge'
import { byDay, cost, duration, matches, time } from './format'

const root = document.getElementById('app')!
let entries: Interaction[] = []
let query = ''
let playing: string | null = null
let playingUri: string | null = null

function api(): TalkieApi {
  const bridge = window.pywebview?.api
  if (!bridge) throw new Error('bridge not ready')
  return bridge
}

async function load(): Promise<void> {
  try {
    entries = await api().list_history()
  } catch {
    entries = []
  }
  render()
}

/** Transcripts are arbitrary user speech, so build nodes — never innerHTML. */
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function button(label: string, title: string, onClick: () => void): HTMLButtonElement {
  const b = el('button', 'action', label)
  b.title = title
  b.addEventListener('click', onClick)
  return b
}

async function flash(target: HTMLElement, label: string): Promise<void> {
  const original = target.textContent
  target.textContent = label
  target.classList.add('done')
  setTimeout(() => {
    target.textContent = original
    target.classList.remove('done')
  }, 1200)
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
      await flash(trigger, 'gone')
      return
    }
    // A real <audio controls> rather than a bare Audio(): the point of keeping
    // the clip is being able to scrub through it, not just hear it once.
    playing = entry.id
    playingUri = uri
    render()
  } catch {
    await flash(trigger, 'failed')
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
      if (await api().copy(entry.id)) await flash(copy, 'Copied')
    })
    actions.append(copy)
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

function header(): HTMLElement {
  const bar = el('header')
  bar.append(el('h1', undefined, 'History'))

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
  const visible = entries.filter((e) => matches(e, query))
  root.replaceChildren(header())

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

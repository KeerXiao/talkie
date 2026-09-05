import type { Interaction } from './bridge'

export function time(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function day(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date()
  const isToday = d.toDateString() === today.toDateString()
  if (isToday) return 'Today'
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  return `${m}m ${Math.round(seconds % 60)}s`
}

export function cost(value: number | null): string {
  if (value === null || value === 0) return ''
  // Sub-cent costs are the norm; two decimals would render every clip as $0.00.
  return value < 0.01 ? `$${value.toFixed(5)}` : `$${value.toFixed(2)}`
}

/** Group into day buckets, preserving the newest-first order within each. */
export function byDay(entries: Interaction[]): [string, Interaction[]][] {
  const groups = new Map<string, Interaction[]>()
  for (const entry of entries) {
    const key = day(entry.startedAt)
    const bucket = groups.get(key)
    if (bucket) bucket.push(entry)
    else groups.set(key, [entry])
  }
  return [...groups.entries()]
}

export function matches(entry: Interaction, query: string): boolean {
  if (!query) return true
  const needle = query.toLowerCase()
  return (
    entry.text.toLowerCase().includes(needle) ||
    (entry.error ?? '').toLowerCase().includes(needle)
  )
}

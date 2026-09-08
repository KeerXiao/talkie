/**
 * The handful of DOM helpers both views share.
 *
 * `el` exists so that nothing is ever built with innerHTML: transcripts are
 * arbitrary user speech, and model ids and error strings arrive from Python.
 */
import type { TalkieApi } from './bridge'

export function api(): TalkieApi {
  const bridge = window.pywebview?.api
  if (!bridge) throw new Error('bridge not ready')
  return bridge
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

export function button(
  label: string,
  title: string,
  onClick: () => void,
): HTMLButtonElement {
  const b = el('button', 'action', label)
  b.title = title
  b.addEventListener('click', onClick)
  return b
}

/** Say something on the button itself for a moment, then put it back. */
export function flash(target: HTMLElement, label: string): void {
  const original = target.textContent
  target.textContent = label
  target.classList.add('done')
  setTimeout(() => {
    target.textContent = original
    target.classList.remove('done')
  }, 1200)
}

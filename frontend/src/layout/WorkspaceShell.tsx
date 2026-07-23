/* WorkspaceShell — 3-column flex container for the workspace page.
 *
 * Quarry design 2026-06-21 — 250 left + flexible center + 360 right.
 * The shell exposes a ``rightExpanded`` prop that flips a data
 * attribute on the root; the WorkspaceShell.css promotes the right
 * panel to a full-width overlay covering main + left when set. The
 * RightPanel's expand button is the typical trigger but any caller
 * can drive the state.
 *
 * Mobile (<768px): both side panels collapse to 56px icon rails.
 */
import { type ReactNode } from 'react'
import './WorkspaceShell.css'

interface Props {
  left: ReactNode
  main: ReactNode
  right: ReactNode
  /** Top bar — AppHeader with theme toggle slot. */
  header: ReactNode
  /** When true, the right panel covers the full body area as an overlay. */
  rightExpanded?: boolean
}

export default function WorkspaceShell({
  header, left, main, right, rightExpanded = false,
}: Props) {
  return (
    <div className="workspace-shell" data-right-expanded={rightExpanded ? 'true' : 'false'}>
      <div className="workspace-shell__header">{header}</div>
      <div className="workspace-shell__body">
        <aside className="workspace-shell__left" aria-label="Project navigation">{left}</aside>
        <main className="workspace-shell__main">{main}</main>
        <aside className="workspace-shell__right" aria-label="Sources and documents">{right}</aside>
      </div>
    </div>
  )
}

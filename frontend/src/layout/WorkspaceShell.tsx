/* WorkspaceShell — 3-column flex container for the workspace page.
 *
 * Layout:
 *   ┌────────────┬──────────────────────┬────────────┐
 *   │ LeftPanel  │    main (chat)       │ RightPanel │
 *   │  240 px    │    flex 1            │   300 px   │
 *   └────────────┴──────────────────────┴────────────┘
 *
 * Mobile (<768px): both panels collapse into icon rails. The actual
 * collapse is owned by LeftPanel / RightPanel via internal state; the
 * shell just lets them shrink. See MobileRail for the rail UI.
 */
import { type ReactNode } from 'react'
import './WorkspaceShell.css'

interface Props {
  left: ReactNode
  main: ReactNode
  right: ReactNode
  /** Top bar — AppHeader with theme toggle slot. */
  header: ReactNode
}

export default function WorkspaceShell({ header, left, main, right }: Props) {
  return (
    <div className="workspace-shell">
      <div className="workspace-shell__header">{header}</div>
      <div className="workspace-shell__body">
        <aside className="workspace-shell__left" aria-label="Project navigation">{left}</aside>
        <main className="workspace-shell__main">{main}</main>
        <aside className="workspace-shell__right" aria-label="Sources and documents">{right}</aside>
      </div>
    </div>
  )
}

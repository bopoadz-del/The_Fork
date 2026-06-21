/* LeftPanel — Quarry design 2026-06-21.
 *
 * Sections, top-to-bottom:
 *   • Brand: "The Shovel"
 *   • PROJECTS — empty-state until PR #102 wires /v1/projects + the
 *     active-project highlight. No stub project names; the design's
 *     example labels were never meant to ship as real content.
 *   • CHAT HISTORY — empty-state until per-project history wiring lands.
 *   • Sign out — bottom of rail.
 *
 * Documents + Drive panels used to live here in the PR #90 layout.
 * They've moved to the ChatComposer's + popover so the left rail stays
 * focused on navigation (per the Quarry design).
 */
import { Plus, LogOut } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import './LeftPanel.css'

interface Props {
  /** Active project's display name. Used by the Quarry header's
   *  "ACTIVE PROJECT" label upstream; left rail just shows it as the
   *  one entry in the Projects list until /v1/projects is wired. */
  activeProjectName?: string
}

export default function LeftPanel({ activeProjectName }: Props) {
  const { logout } = useAuth()

  return (
    <div className="left-panel">
      <div className="left-panel__brand">
        <span className="left-panel__brand-text">The Shovel</span>
      </div>

      <section className="left-panel__section">
        <header className="left-panel__section-head">Projects</header>
        {activeProjectName ? (
          <ul className="left-panel__list">
            <li>
              <span className="left-panel__list-item left-panel__list-item--active">
                {activeProjectName}
              </span>
            </li>
          </ul>
        ) : (
          <p className="left-panel__empty">No projects yet.</p>
        )}
        <Link to="/" className="left-panel__new-project">
          <Plus size={14} />
          <span>New project</span>
        </Link>
      </section>

      <section className="left-panel__section">
        <header className="left-panel__section-head">Chat history</header>
        <p className="left-panel__empty">No conversations yet.</p>
      </section>

      <div className="left-panel__footer">
        <button
          type="button"
          className="left-panel__signout"
          onClick={() => logout()}
        >
          <LogOut size={14} />
          <span>Sign out</span>
        </button>
      </div>
    </div>
  )
}

/* LeftPanel — Quarry design 2026-06-21.
 *
 * Sections, top-to-bottom:
 *   • Brand: "The Shovel"
 *   • PROJECTS — stub list (Demolition active + sample names + New Project).
 *     Real data wiring deferred to PR #92 (project-scoping work).
 *   • CHAT HISTORY — stub list with "Current session".
 *     Real per-project history wiring deferred to PR #92.
 *   • Sign out — at the bottom (auth context provides it).
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
  /** Active project's display name. Highlighted in the projects list. */
  activeProjectName?: string
}

/** Stub project list — pre-PR-92 placeholders matching the Quarry design.
 *  Will be replaced with real `/v1/projects` data once project scoping
 *  + per-project chat history land. */
const STUB_PROJECTS = ['Demolition', 'Ha Long Xai', 'Hon Mon Island']

export default function LeftPanel({ activeProjectName }: Props) {
  const { logout } = useAuth()
  const activeName = activeProjectName ?? STUB_PROJECTS[0]

  return (
    <div className="left-panel">
      <div className="left-panel__brand">
        <span className="left-panel__brand-text">The Shovel</span>
      </div>

      <section className="left-panel__section">
        <header className="left-panel__section-head">Projects</header>
        <ul className="left-panel__list">
          {STUB_PROJECTS.map((name) => (
            <li key={name}>
              <Link
                to="/"
                className={
                  'left-panel__list-item' +
                  (name === activeName ? ' left-panel__list-item--active' : '')
                }
              >
                {name}
              </Link>
            </li>
          ))}
          <li>
            <Link to="/" className="left-panel__new-project">
              <Plus size={14} />
              <span>New Project</span>
            </Link>
          </li>
        </ul>
      </section>

      <section className="left-panel__section">
        <header className="left-panel__section-head">Chat history</header>
        <ul className="left-panel__list">
          <li>
            <span className="left-panel__list-item left-panel__list-item--active">
              Current session
            </span>
          </li>
        </ul>
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

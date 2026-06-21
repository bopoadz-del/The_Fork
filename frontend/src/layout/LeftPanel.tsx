/* LeftPanel — Quarry design 2026-06-21.
 *
 * PR #103 — real projects list. Fetches /v1/projects on mount.
 *
 * Sections, top-to-bottom:
 *   • Brand: "The Shovel"
 *   • PROJECTS — real list from /v1/projects, with the active project
 *     highlighted. Empty state when the user has none yet. "New project"
 *     link below the list deep-links to / (Projects page) where the
 *     creation modal lives.
 *   • CHAT HISTORY — empty state until per-project history wiring lands
 *     (next PR).
 *   • Sign out — bottom of rail.
 */
import { useEffect, useState } from 'react'
import { Plus, LogOut } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { apiGet, ApiError } from '../lib/api'
import './LeftPanel.css'

interface ProjectRow {
  id: string
  name: string
}

interface ProjectsResponse {
  projects: ProjectRow[]
}

interface Props {
  /** Active project's id — drives the highlight in the Projects list. */
  activeProjectId?: string
  /** Active project's display name — used as a fallback first-render label
   *  while the /v1/projects fetch is in flight so the active row never
   *  flashes "(unknown project)". */
  activeProjectName?: string
}

type LoadState =
  | { tag: 'loading' }
  | { tag: 'error'; message: string }
  | { tag: 'loaded'; projects: ProjectRow[] }

export default function LeftPanel({ activeProjectId, activeProjectName }: Props) {
  const { logout } = useAuth()
  const [state, setState] = useState<LoadState>({ tag: 'loading' })

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const data = await apiGet<ProjectsResponse>('/v1/projects')
        if (cancelled) return
        setState({ tag: 'loaded', projects: data.projects ?? [] })
      } catch (err: unknown) {
        if (cancelled) return
        const msg = err instanceof ApiError ? err.message
          : err instanceof Error ? err.message
          : 'Failed to load projects.'
        setState({ tag: 'error', message: msg })
      }
    }
    void load()
    return () => { cancelled = true }
  }, [])

  function renderProjectsBody() {
    if (state.tag === 'loading') {
      // While loading, if we know the active project's name, show it
      // optimistically so the rail doesn't flicker.
      if (activeProjectId && activeProjectName) {
        return (
          <ul className="left-panel__list">
            <li>
              <span className="left-panel__list-item left-panel__list-item--active">
                {activeProjectName}
              </span>
            </li>
          </ul>
        )
      }
      return <p className="left-panel__empty">Loading…</p>
    }

    if (state.tag === 'error') {
      return <p className="left-panel__empty">Couldn't load projects.</p>
    }

    if (state.projects.length === 0) {
      return <p className="left-panel__empty">No projects yet.</p>
    }

    return (
      <ul className="left-panel__list">
        {state.projects.map((p) => {
          const isActive = p.id === activeProjectId
          return (
            <li key={p.id}>
              <Link
                to={`/projects/${p.id}`}
                className={
                  'left-panel__list-item' +
                  (isActive ? ' left-panel__list-item--active' : '')
                }
                aria-current={isActive ? 'page' : undefined}
              >
                {p.name}
              </Link>
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <div className="left-panel">
      <div className="left-panel__brand">
        <span className="left-panel__brand-text">The Shovel</span>
      </div>

      <section className="left-panel__section">
        <header className="left-panel__section-head">Projects</header>
        {renderProjectsBody()}
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

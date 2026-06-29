/* LeftPanel — Quarry design, wired to real backend (PR #104).
 *
 * Sections, top-to-bottom:
 *   • Brand: "The Shovel"
 *   • PROJECTS — real list from /v1/projects, active row highlighted.
 *   • DOCUMENTS — the active project's uploaded files. Slot-rendered
 *     so the existing DocumentsPanel (in ProjectWorkspace) provides
 *     upload + delete + status. Hidden when no project is active.
 *   • CONVERSATION — what the backend actually supports: ONE per
 *     project, addressed by ws-{projectId}. Shows message count +
 *     Export + Clear actions wired to the existing handlers. There
 *     is no multi-thread history API today, so the section is named
 *     for what it is, not what it isn't.
 *   • Sign out — bottom of rail.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { Plus, LogOut, Download, RotateCcw, Settings } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { apiGet, ApiError } from '../lib/api'
import './LeftPanel.css'

interface ProjectRow {
  id: string
  name: string
  /** Origin of the project. Only admin-approved Drive projects (and the
   *  injected master corpus) appear in the sidebar. */
  origin?: string
  is_master_corpus?: boolean
}

/** The sidebar lists ONLY admin-approved projects (those an admin turned into
 *  a project from Drive) plus the master corpus. Unapproved shells / backing
 *  corpora / personal scratch projects stay out of the list — without deleting
 *  anything. Admins manage + approve the rest from the Admin page. */
function isSidebarVisible(p: ProjectRow): boolean {
  return p.is_master_corpus === true || p.origin === 'admin_drive_approved'
}

interface ProjectsResponse {
  projects: ProjectRow[]
}

interface Props {
  /** Active project id — drives Projects highlight + visibility of Documents
   *  and Conversation sections. */
  activeProjectId?: string
  /** Active project name — used as the fallback active row while the
   *  /v1/projects fetch is in flight. */
  activeProjectName?: string
  /** DocumentsPanel rendered by the caller (ProjectWorkspace) so the
   *  existing upload + delete wiring is reused. Optional: render the
   *  Documents section only when both this and activeProjectId are set. */
  documents?: ReactNode
  /** Number of messages in the active conversation. Drives the "X messages"
   *  label and gates the Export + Clear actions. */
  messageCount?: number
  /** Export the active conversation as a docx. */
  onExportConversation?: () => void
  /** Clear the active conversation server-side + reset the UI. */
  onClearConversation?: () => void
}

type LoadState =
  | { tag: 'loading' }
  | { tag: 'error'; message: string }
  | { tag: 'loaded'; projects: ProjectRow[] }

export default function LeftPanel({
  activeProjectId,
  activeProjectName,
  documents,
  messageCount,
  onExportConversation,
  onClearConversation,
}: Props) {
  const { logout } = useAuth()
  const [state, setState] = useState<LoadState>({ tag: 'loading' })

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const data = await apiGet<ProjectsResponse>('/v1/projects')
        if (cancelled) return
        const visible = (data.projects ?? []).filter(isSidebarVisible)
        setState({ tag: 'loaded', projects: visible })
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

  const showDocsSection = !!activeProjectId && !!documents
  const showConvoSection = !!activeProjectId

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

      {showDocsSection && (
        <section className="left-panel__section">
          <header className="left-panel__section-head">Documents</header>
          <div className="left-panel__slot">{documents}</div>
        </section>
      )}

      {showConvoSection && (
        <section className="left-panel__section">
          <header className="left-panel__section-head">Conversation</header>
          {messageCount && messageCount > 0 ? (
            <>
              <p className="left-panel__convo-summary">
                {messageCount} message{messageCount === 1 ? '' : 's'} in the
                current thread.
              </p>
              <div className="left-panel__convo-actions">
                {onExportConversation && (
                  <button
                    type="button"
                    className="left-panel__convo-btn"
                    onClick={onExportConversation}
                    title="Export this conversation as a .docx file"
                  >
                    <Download size={13} />
                    <span>Export</span>
                  </button>
                )}
                {onClearConversation && (
                  <button
                    type="button"
                    className="left-panel__convo-btn left-panel__convo-btn--danger"
                    onClick={onClearConversation}
                    title="Clear server-side history (cannot be undone)"
                  >
                    <RotateCcw size={13} />
                    <span>Clear</span>
                  </button>
                )}
              </div>
            </>
          ) : (
            <p className="left-panel__empty">No messages yet. Start the chat.</p>
          )}
        </section>
      )}

      <div className="left-panel__footer">
        <Link to="/admin" className="left-panel__admin">
          <Settings size={14} />
          <span>Admin</span>
        </Link>
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

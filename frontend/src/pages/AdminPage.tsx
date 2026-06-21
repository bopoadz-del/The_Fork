/* AdminPage — operator-supplied requirement 2026-06-21.
 *
 * Single page mounted at /admin. Three first-class sections:
 *
 *   1. Google Drive — the full Drive integration (connected account,
 *      browse, search, folder navigation, import). Drive is the pilot's
 *      project server; this is where it lives.
 *
 *   2. Project corpus status — table of every project with chunk count,
 *      document count, last indexed date, and a re-index trigger.
 *
 *   3. Header — "Connected as: <email>" up top (not in the LeftPanel).
 *
 * No stubs, no "coming soon" placeholders. Wired to live backend
 * endpoints:
 *   GET  /v1/drive/status              → connection + email
 *   GET  /v1/drive/connect             → OAuth redirect
 *   POST /v1/drive/disconnect          → unlink
 *   GET  /v1/drive/files               → search / browse
 *   POST /v1/projects/{id}/drive/import → import a file into a project
 *   GET  /v1/projects                  → list of projects
 *   GET  /v1/admin/corpus/collections  → chunk/doc counts per project
 *   POST /v1/admin/debug/project-reindex → re-index a project
 */
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  RefreshCw, FolderTree, Search, Plug, LogOut, ArrowLeft,
} from 'lucide-react'
import AppHeader from '../components/AppHeader'
import { apiGet, apiPost, ApiError } from '../lib/api'
import { getToken } from '../lib/token'
import { useAuth } from '../auth/AuthContext'
import './admin.css'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

interface DriveStatus {
  configured: boolean
  connected: boolean
  email?: string
}

interface DriveFile {
  id: string
  name: string
  is_folder?: boolean
  mime_type?: string
}

interface FolderCrumb {
  id: string
  name: string
}

interface ProjectRow {
  id: string
  name: string
  status?: string
  user_id?: string
}

interface CorpusCollection {
  project_id: string
  documents: number
  chunks: number
}

interface CorpusResponse {
  collections: CorpusCollection[]
  total_project_ids: number
  total_documents: number
  total_chunks: number
}

export default function AdminPage() {
  const navigate = useNavigate()
  const { logout } = useAuth()

  return (
    <div className="admin-page">
      <AppHeader breadcrumb={
        <nav className="admin-breadcrumb" aria-label="Admin breadcrumb">
          <Link to="/" className="admin-breadcrumb__link">
            <ArrowLeft size={14} /> <span>Projects</span>
          </Link>
          <span className="admin-breadcrumb__sep">/</span>
          <span className="admin-breadcrumb__current">Admin</span>
        </nav>
      } />

      <main className="admin-main">
        <header className="admin-main__header">
          <h1 className="admin-main__title">Admin</h1>
          <p className="admin-main__subtitle">
            Data management layer. Google Drive integration + project corpus status.
          </p>
        </header>

        <DriveSection />

        <CorpusSection onPickProject={(pid) => navigate(`/projects/${pid}`)} />

        <footer className="admin-main__footer">
          <button
            type="button"
            className="admin-signout"
            onClick={() => logout()}
          >
            <LogOut size={14} />
            <span>Sign out</span>
          </button>
        </footer>
      </main>
    </div>
  )
}

// ─── Google Drive section ────────────────────────────────────────────────

function DriveSection() {
  const [status, setStatus] = useState<DriveStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [query, setQuery] = useState('')
  const [files, setFiles] = useState<DriveFile[]>([])
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [folderStack, setFolderStack] = useState<FolderCrumb[]>([])
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [importTarget, setImportTarget] = useState<string>('')
  const [importingId, setImportingId] = useState<string | null>(null)
  const [importErrors, setImportErrors] = useState<Record<string, string>>({})
  const [importedFlash, setImportedFlash] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [s, p] = await Promise.all([
          apiGet<DriveStatus>('/v1/drive/status'),
          apiGet<{ projects: ProjectRow[] }>('/v1/projects'),
        ])
        if (cancelled) return
        setStatus(s)
        setProjects(p.projects ?? [])
        if (p.projects && p.projects.length > 0 && !importTarget) {
          setImportTarget(p.projects[0].id)
        }
      } catch (err) {
        if (cancelled) return
        setStatusError(err instanceof Error ? err.message : 'Failed to load Drive status.')
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function openFolder(folder: FolderCrumb, push: boolean) {
    setSearchError(null); setFiles([]); setSearching(true)
    try {
      const url = folder.id
        ? `/v1/drive/files?folder_id=${encodeURIComponent(folder.id)}`
        : '/v1/drive/files'
      const resp = await apiGet<{ files: DriveFile[] }>(url)
      setFiles(resp.files)
      setHasSearched(true)
      setFolderStack((prev) =>
        push ? [...prev, folder]
             : prev.slice(0, prev.findIndex((c) => c.id === folder.id) + 1),
      )
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : 'Drive browse failed.')
    } finally { setSearching(false) }
  }

  async function handleConnect() {
    setConnecting(true)
    try {
      const resp = await apiGet<{ auth_url: string }>('/v1/drive/connect')
      window.location.href = resp.auth_url
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : 'Connect failed.')
      setConnecting(false)
    }
  }

  async function handleDisconnect() {
    if (!window.confirm('Disconnect Google Drive? You will need to re-authorize to import again.')) return
    setDisconnecting(true)
    try {
      await apiPost('/v1/drive/disconnect', {})
      setStatus({ configured: status?.configured ?? true, connected: false })
      setFiles([]); setHasSearched(false); setFolderStack([])
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : 'Disconnect failed.')
    } finally { setDisconnecting(false) }
  }

  async function handleSearch(e: FormEvent) {
    e.preventDefault()
    setSearchError(null); setFiles([]); setHasSearched(false); setSearching(true)
    setFolderStack([])
    try {
      const resp = await apiGet<{ files: DriveFile[] }>(
        `/v1/drive/files?q=${encodeURIComponent(query)}`,
      )
      setFiles(resp.files); setHasSearched(true)
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : 'Drive search failed.')
    } finally { setSearching(false) }
  }

  async function handleImport(file: DriveFile) {
    if (!importTarget) {
      setImportErrors((p) => ({ ...p, [file.id]: 'Pick a project first.' }))
      return
    }
    setImportingId(file.id)
    setImportErrors((p) => { const n = { ...p }; delete n[file.id]; return n })
    try {
      await apiPost(`/v1/projects/${importTarget}/drive/import`,
        { file_id: file.id, name: file.name })
      setFiles((p) => p.filter((f) => f.id !== file.id))
      setImportedFlash(`Imported "${file.name}" into ${projects.find(p => p.id === importTarget)?.name ?? importTarget}.`)
      setTimeout(() => setImportedFlash(null), 4000)
    } catch (err) {
      setImportErrors((p) => ({ ...p, [file.id]: err instanceof Error ? err.message : 'Import failed.' }))
    } finally { setImportingId(null) }
  }

  return (
    <section className="admin-section">
      <header className="admin-section__head">
        <Plug size={16} />
        <h2 className="admin-section__title">Google Drive</h2>
      </header>

      {!status && !statusError && <p className="admin-loading">Checking Drive…</p>}
      {statusError && <p className="admin-alert">{statusError}</p>}

      {status && !status.configured && (
        <p className="admin-empty">
          Google Drive is not configured on this server. Add the OAuth client + redirect
          URI to enable. See docs for setup.
        </p>
      )}

      {status && status.configured && !status.connected && (
        <div className="admin-cta">
          <p>Connect a Google account to browse and import Drive files into projects.</p>
          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={() => void handleConnect()}
            disabled={connecting}
          >
            {connecting ? 'Redirecting…' : 'Connect Google Drive'}
          </button>
        </div>
      )}

      {status && status.connected && (
        <>
          <div className="admin-drive-meta">
            <div>
              <span className="admin-drive-meta__label">Connected as</span>{' '}
              <span className="admin-drive-meta__email">{status.email ?? '(unknown)'}</span>
            </div>
            <button
              type="button"
              className="admin-btn admin-btn--ghost"
              onClick={() => void handleDisconnect()}
              disabled={disconnecting}
            >
              {disconnecting ? 'Disconnecting…' : 'Disconnect'}
            </button>
          </div>

          <div className="admin-drive-importer">
            <label className="admin-drive-importer__label">
              Import target
              <select
                value={importTarget}
                onChange={(e) => setImportTarget(e.target.value)}
                className="admin-drive-importer__select"
                disabled={projects.length === 0}
              >
                {projects.length === 0 && <option value="">No projects yet</option>}
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
          </div>

          <form className="admin-drive-search" onSubmit={(e) => void handleSearch(e)}>
            <Search size={14} />
            <input
              type="search"
              className="admin-drive-search__input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search Drive files…"
              disabled={searching}
            />
            <button type="submit" className="admin-btn admin-btn--ghost" disabled={searching}>
              {searching ? '…' : 'Search'}
            </button>
            <button
              type="button"
              className="admin-btn admin-btn--ghost"
              disabled={searching}
              onClick={() => void openFolder({ id: '', name: 'My Drive' }, true)}
            >
              <FolderTree size={13} />
              <span>Browse</span>
            </button>
          </form>

          {searchError && <p className="admin-alert">{searchError}</p>}
          {importedFlash && <p className="admin-flash">{importedFlash}</p>}

          {folderStack.length > 0 && (
            <nav className="admin-crumbs" aria-label="Drive folder">
              <button
                type="button"
                className="admin-crumbs__back"
                onClick={() => { setFolderStack([]); setFiles([]); setHasSearched(false) }}
              >
                ↑ Back to search
              </button>
              {folderStack.map((c, i) => (
                <span key={c.id || `c-${i}`}>
                  <span className="admin-crumbs__sep">/</span>
                  <button
                    type="button"
                    className="admin-crumbs__item"
                    onClick={() => void openFolder(c, false)}
                    disabled={i === folderStack.length - 1 || searching}
                  >
                    {c.name}
                  </button>
                </span>
              ))}
            </nav>
          )}

          {files.length > 0 && (
            <ul className="admin-files">
              {files.map((f) => (
                <li key={f.id} className={'admin-file' + (f.is_folder ? ' admin-file--folder' : '')}>
                  <span className="admin-file__name">
                    {f.is_folder && <span className="admin-file__folder-mark">[folder]</span>}
                    {f.is_folder ? ' ' : ''}{f.name}
                  </span>
                  <div className="admin-file__actions">
                    {importErrors[f.id] && (
                      <span className="admin-file__error">{importErrors[f.id]}</span>
                    )}
                    {f.is_folder ? (
                      <button
                        type="button"
                        className="admin-btn admin-btn--ghost admin-btn--small"
                        disabled={searching}
                        onClick={() => void openFolder({ id: f.id, name: f.name }, true)}
                      >Open</button>
                    ) : (
                      <button
                        type="button"
                        className="admin-btn admin-btn--primary admin-btn--small"
                        disabled={importingId === f.id || !importTarget}
                        onClick={() => void handleImport(f)}
                      >{importingId === f.id ? '…' : 'Add'}</button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {!searching && hasSearched && files.length === 0 && !searchError && (
            <p className="admin-empty">No files found.</p>
          )}
        </>
      )}
    </section>
  )
}

// ─── Corpus status section ──────────────────────────────────────────────

function CorpusSection({ onPickProject }: { onPickProject: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [corpus, setCorpus] = useState<Record<string, CorpusCollection>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reindexingId, setReindexingId] = useState<string | null>(null)
  const [reindexFlash, setReindexFlash] = useState<string | null>(null)

  async function load() {
    setLoading(true); setError(null)
    try {
      const [pResp, cResp] = await Promise.all([
        apiGet<{ projects: ProjectRow[] }>('/v1/projects'),
        apiGet<CorpusResponse>('/v1/admin/corpus/collections?folder_breakdown=false'),
      ])
      setProjects(pResp.projects ?? [])
      const map: Record<string, CorpusCollection> = {}
      for (const c of cResp.collections ?? []) map[c.project_id] = c
      setCorpus(map)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load corpus.'
      setError(err instanceof ApiError && err.status === 403
        ? 'Admin role required to view corpus status. You are logged in but not as an admin.'
        : msg)
    } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  async function handleReindex(p: ProjectRow) {
    if (!window.confirm(`Re-index "${p.name}"? This re-extracts text + rebuilds chunks for every document in this project.`)) return
    setReindexingId(p.id)
    setReindexFlash(null)
    try {
      const token = getToken() || ''
      const res = await fetch(
        `${API_BASE}/v1/admin/debug/project-reindex?project_id=${encodeURIComponent(p.id)}`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        setReindexFlash(`Re-index failed (${res.status}): ${detail.slice(0, 200)}`)
        return
      }
      const body = await res.json().catch(() => ({}))
      setReindexFlash(
        `Re-indexed "${p.name}": ${body.indexed ?? '?'} documents, ` +
        `${body.skipped_unsupported ?? 0} skipped (unsupported), ` +
        `${body.total_chunks ?? '?'} chunks.`,
      )
      await load()
    } catch (err) {
      setReindexFlash(`Re-index failed: ${(err as Error).message}`)
    } finally { setReindexingId(null) }
  }

  return (
    <section className="admin-section">
      <header className="admin-section__head">
        <RefreshCw size={16} />
        <h2 className="admin-section__title">Project corpus</h2>
        <button
          type="button"
          className="admin-btn admin-btn--ghost admin-btn--small"
          onClick={() => void load()}
          disabled={loading}
          style={{ marginLeft: 'auto' }}
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </header>

      {error && <p className="admin-alert">{error}</p>}
      {reindexFlash && <p className="admin-flash">{reindexFlash}</p>}

      {!error && projects.length === 0 && !loading && (
        <p className="admin-empty">No projects yet. <Link to="/">Create one</Link>.</p>
      )}

      {projects.length > 0 && (
        <table className="admin-corpus">
          <thead>
            <tr>
              <th>Project</th>
              <th className="num">Documents</th>
              <th className="num">Chunks</th>
              <th className="num">Re-index</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => {
              const c = corpus[p.id]
              return (
                <tr key={p.id}>
                  <td>
                    <button
                      type="button"
                      className="admin-corpus__namelink"
                      onClick={() => onPickProject(p.id)}
                    >
                      {p.name}
                    </button>
                    <span className="admin-corpus__pid mono">{p.id}</span>
                  </td>
                  <td className="num">{c?.documents ?? 0}</td>
                  <td className="num">{c?.chunks ?? 0}</td>
                  <td className="num">
                    <button
                      type="button"
                      className="admin-btn admin-btn--ghost admin-btn--small"
                      onClick={() => void handleReindex(p)}
                      disabled={reindexingId !== null}
                    >
                      {reindexingId === p.id ? '…' : 'Re-index'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}

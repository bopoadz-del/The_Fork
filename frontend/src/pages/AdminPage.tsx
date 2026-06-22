/* AdminPage — operator-supplied requirement 2026-06-21 + 2026-06-22.
 *
 * Single page mounted at /admin. Four first-class sections:
 *
 *   1. Google Drive — the full Drive integration (connected account,
 *      browse, search, folder navigation, import). Drive is the pilot's
 *      project server; this is where it lives.
 *
 *   2. Detected from Drive — cascaded folder tree from /v1/admin/drive/scan
 *      (PR A). Each candidate folder gets an "Approve as project" action
 *      that POSTs /v1/admin/projects/approve-from-drive and creates a
 *      row with origin='admin_drive_approved'.
 *
 *   3. Approved projects — filtered to origin='admin_drive_approved'
 *      (so chadi/bopo-style user-created rows do NOT appear here). Each
 *      row carries documents + chunks counts plus Re-index and Delete.
 *
 *   4. Header — "Connected as: <email>" up top (not in the LeftPanel).
 *
 * No stubs, no "coming soon" placeholders. Wired to live backend
 * endpoints:
 *   GET  /v1/drive/status                         → connection + email
 *   GET  /v1/drive/connect                        → OAuth redirect
 *   POST /v1/drive/disconnect                     → unlink
 *   GET  /v1/drive/files                          → search / browse
 *   POST /v1/projects/{id}/drive/import           → import file into project
 *   GET  /v1/projects                             → all project rows
 *   GET  /v1/admin/drive/scan                     → cascaded folder tree (PR A)
 *   POST /v1/admin/projects/approve-from-drive    → approve folder (PR A)
 *   GET  /v1/admin/corpus/collections             → chunk/doc counts
 *   POST /v1/admin/debug/project-reindex          → re-index a project
 *   DELETE /v1/projects/{id}                      → delete a project
 */
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  RefreshCw, FolderTree, Search, Plug, LogOut, ArrowLeft,
  FolderSearch, CheckCircle2, Trash2, ChevronRight, ChevronDown,
} from 'lucide-react'
import AppHeader from '../components/AppHeader'
import { apiGet, apiPost, apiDelete, ApiError } from '../lib/api'
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
  is_approved?: boolean
  origin?: string
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

interface ScanFolder {
  folder_id: string
  name: string
  direct_file_count: number
  subfolder_count: number
  is_candidate: boolean
  children: ScanFolder[]
}

interface ScanResponse {
  max_depth: number
  root_file_count: number
  candidates_total: number
  tree: ScanFolder[]
}

export default function AdminPage() {
  const navigate = useNavigate()
  const { logout } = useAuth()
  // Bumped every time a Drive folder is approved. ApprovedProjectsSection
  // re-fetches when this changes so the new row appears without a manual
  // Refresh click — fix for "I approved 2 and nothing showed" (PR F).
  const [refreshKey, setRefreshKey] = useState(0)

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

        <DetectedFromDriveSection onApproved={() => setRefreshKey((k) => k + 1)} />

        <ApprovedProjectsSection
          refreshKey={refreshKey}
          onPickProject={(pid) => navigate(`/projects/${pid}`)}
        />

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

// ─── Detected from Drive section ────────────────────────────────────────
//
// Renders /v1/admin/drive/scan as a collapsible cascaded tree. Folders
// flagged is_candidate (direct_file_count > 0) get an Approve button
// that POSTs /v1/admin/projects/approve-from-drive — the backend slugs
// the folder name into a project id, creates the row with
// origin='admin_drive_approved', and queues a recursive Drive import
// in the background. The Approved projects section below then shows
// the new row once it's created.

function DetectedFromDriveSection({ onApproved }: { onApproved?: () => void }) {
  const [scan, setScan] = useState<ScanResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [approvedFlash, setApprovedFlash] = useState<string | null>(null)
  const [approveErrors, setApproveErrors] = useState<Record<string, string>>({})
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  // Two-step confirm: first click flips the button to "Click again to
  // confirm" for 5s, second click within that window fires the approve.
  // Replaces native window.confirm(), which silently no-ops if the
  // browser throttles dialogs (the symptom from the "I approved 2 and
  // nothing showed" report).
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [approvedIds, setApprovedIds] = useState<Set<string>>(new Set())

  async function load() {
    setLoading(true); setError(null)
    try {
      const resp = await apiGet<ScanResponse>('/v1/admin/drive/scan?max_depth=2')
      setScan(resp)
      // Auto-expand top-level by default; depth-2 stays collapsed unless clicked.
      const next: Record<string, boolean> = {}
      for (const f of resp.tree) next[f.folder_id] = true
      setExpanded(next)
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError('Admin role required to scan Drive.')
      } else if (err instanceof ApiError && err.status === 409) {
        setError('Connect Google Drive in the section above before scanning.')
      } else {
        setError(err instanceof Error ? err.message : 'Drive scan failed.')
      }
    } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  function handleApproveClick(folder: ScanFolder) {
    // First click on a folder: arm the confirm state for 5 seconds.
    // Second click within that window: actually fire the approve.
    if (confirmingId !== folder.folder_id) {
      setConfirmingId(folder.folder_id)
      window.setTimeout(() => {
        setConfirmingId((cur) => (cur === folder.folder_id ? null : cur))
      }, 5000)
      return
    }
    setConfirmingId(null)
    void handleApprove(folder)
  }

  async function handleApprove(folder: ScanFolder) {
    setApprovingId(folder.folder_id)
    setApproveErrors((p) => { const n = { ...p }; delete n[folder.folder_id]; return n })
    setApprovedFlash(null)
    try {
      const body = await apiPost<{ project: ProjectRow }>(
        '/v1/admin/projects/approve-from-drive',
        { folder_id: folder.folder_id, name: folder.name },
      )
      setApprovedIds((p) => new Set(p).add(folder.folder_id))
      setApprovedFlash(
        `Approved "${folder.name}" → project "${body.project.id}". `
        + `Indexing runs in the background; the row appears in Approved projects below.`,
      )
      // Tell the parent so the Approved Projects table re-fetches and
      // the new row appears without a manual Refresh click.
      onApproved?.()
    } catch (err) {
      setApproveErrors((p) => ({
        ...p, [folder.folder_id]: err instanceof Error ? err.message : 'Approve failed.',
      }))
    } finally { setApprovingId(null) }
  }

  function toggle(folderId: string) {
    setExpanded((p) => ({ ...p, [folderId]: !p[folderId] }))
  }

  function renderFolder(folder: ScanFolder, depth: number) {
    const open = !!expanded[folder.folder_id]
    const hasChildren = folder.children.length > 0
    return (
      <li key={folder.folder_id} className="admin-tree__node" style={{ paddingLeft: `${depth * 16}px` }}>
        <div className="admin-tree__row">
          <button
            type="button"
            className="admin-tree__toggle"
            onClick={() => toggle(folder.folder_id)}
            disabled={!hasChildren}
            aria-label={open ? 'Collapse' : 'Expand'}
          >
            {hasChildren ? (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />)
                         : <span className="admin-tree__leaf-mark" />}
          </button>
          <span className="admin-tree__name">{folder.name}</span>
          <span className="admin-tree__counts">
            {folder.direct_file_count} file{folder.direct_file_count === 1 ? '' : 's'}
            {folder.subfolder_count > 0 && (
              <>{' · '}{folder.subfolder_count} folder{folder.subfolder_count === 1 ? '' : 's'}</>
            )}
          </span>
          <div className="admin-tree__actions">
            {approveErrors[folder.folder_id] && (
              <span className="admin-file__error">{approveErrors[folder.folder_id]}</span>
            )}
            {folder.is_candidate ? (
              approvedIds.has(folder.folder_id) ? (
                <span className="admin-tree__approved-flag">
                  <CheckCircle2 size={13} /> Approved
                </span>
              ) : (
                <button
                  type="button"
                  className={
                    'admin-btn admin-btn--small '
                    + (confirmingId === folder.folder_id
                        ? 'admin-btn--danger'
                        : 'admin-btn--primary')
                  }
                  onClick={() => handleApproveClick(folder)}
                  disabled={approvingId !== null}
                >
                  {approvingId === folder.folder_id
                    ? 'Approving…'
                    : confirmingId === folder.folder_id
                      ? 'Click again to confirm'
                      : <><CheckCircle2 size={13} /><span>Approve</span></>}
                </button>
              )
            ) : (
              <span className="admin-tree__nocand">no direct files</span>
            )}
          </div>
        </div>
        {open && hasChildren && (
          <ul className="admin-tree__children">
            {folder.children.map((c) => renderFolder(c, depth + 1))}
          </ul>
        )}
      </li>
    )
  }

  return (
    <section className="admin-section">
      <header className="admin-section__head">
        <FolderSearch size={16} />
        <h2 className="admin-section__title">Detected from Drive</h2>
        <button
          type="button"
          className="admin-btn admin-btn--ghost admin-btn--small"
          onClick={() => void load()}
          disabled={loading}
          style={{ marginLeft: 'auto' }}
        >
          {loading ? 'Scanning…' : 'Rescan'}
        </button>
      </header>

      <p className="admin-section__hint">
        Top-level Drive folders the platform sees. Approve a folder to turn it into a
        project — only its documents will be indexed and visible to users.
      </p>

      {error && <p className="admin-alert">{error}</p>}
      {approvedFlash && <p className="admin-flash">{approvedFlash}</p>}

      {!error && scan && scan.tree.length === 0 && !loading && (
        <p className="admin-empty">No folders at Drive root.</p>
      )}

      {scan && scan.tree.length > 0 && (
        <ul className="admin-tree">
          {scan.tree.map((f) => renderFolder(f, 0))}
        </ul>
      )}
    </section>
  )
}

// ─── Approved projects section ──────────────────────────────────────────
//
// Filtered to origin='admin_drive_approved'. Operator requirement:
// chadi/bopo-style user-created rows MUST NOT appear here — admin owns
// the platform-canonical project list. Each row exposes Re-index and
// Delete actions.

function ApprovedProjectsSection({
  onPickProject,
  refreshKey = 0,
}: {
  onPickProject: (id: string) => void
  refreshKey?: number
}) {
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [corpus, setCorpus] = useState<Record<string, CorpusCollection>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reindexingId, setReindexingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

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
      const msg = err instanceof Error ? err.message : 'Failed to load approved projects.'
      setError(err instanceof ApiError && err.status === 403
        ? 'Admin role required to view approved projects. You are logged in but not as an admin.'
        : msg)
    } finally { setLoading(false) }
  }

  // Re-fetch when the parent bumps refreshKey (a Drive approve just
  // landed) so the new row appears without a manual click.
  useEffect(() => { void load() }, [refreshKey])

  async function handleReindex(p: ProjectRow) {
    if (!window.confirm(`Re-index "${p.name}"? This re-extracts text + rebuilds chunks for every document in this project.`)) return
    setReindexingId(p.id)
    setFlash(null)
    try {
      const token = getToken() || ''
      const res = await fetch(
        `${API_BASE}/v1/admin/debug/project-reindex?project_id=${encodeURIComponent(p.id)}`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        setFlash(`Re-index failed (${res.status}): ${detail.slice(0, 200)}`)
        return
      }
      const body = await res.json().catch(() => ({}))
      setFlash(
        `Re-indexed "${p.name}": ${body.indexed ?? '?'} documents, ` +
        `${body.skipped_unsupported ?? 0} skipped (unsupported), ` +
        `${body.total_chunks ?? '?'} chunks.`,
      )
      await load()
    } catch (err) {
      setFlash(`Re-index failed: ${(err as Error).message}`)
    } finally { setReindexingId(null) }
  }

  async function handleDelete(p: ProjectRow) {
    if (!window.confirm(
      `Delete project "${p.name}"? This removes the project and all its documents + chunks. This cannot be undone.`,
    )) return
    setDeletingId(p.id)
    setFlash(null)
    try {
      await apiDelete(`/v1/projects/${encodeURIComponent(p.id)}`)
      setFlash(`Deleted "${p.name}".`)
      await load()
    } catch (err) {
      setFlash(`Delete failed: ${(err as Error).message}`)
    } finally { setDeletingId(null) }
  }

  // The filter that solves the operator's "no chadi no bopo" requirement.
  // Only rows the admin explicitly approved via /v1/admin/projects/approve-from-drive
  // make it into this table.
  const approved = projects.filter((p) => (p.origin ?? 'user_create') === 'admin_drive_approved')

  return (
    <section className="admin-section">
      <header className="admin-section__head">
        <RefreshCw size={16} />
        <h2 className="admin-section__title">Approved projects</h2>
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

      <p className="admin-section__hint">
        Projects approved from a Drive folder. User-created personal projects
        live on their owners' profiles and do not appear here.
      </p>

      {error && <p className="admin-alert">{error}</p>}
      {flash && <p className="admin-flash">{flash}</p>}

      {!error && approved.length === 0 && !loading && (
        <p className="admin-empty">No approved projects yet. Approve a folder above to create one.</p>
      )}

      {approved.length > 0 && (
        <table className="admin-corpus">
          <thead>
            <tr>
              <th>Project</th>
              <th className="num">Documents</th>
              <th className="num">Chunks</th>
              <th className="num">Actions</th>
            </tr>
          </thead>
          <tbody>
            {approved.map((p) => {
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
                  <td className="num admin-corpus__actions">
                    <button
                      type="button"
                      className="admin-btn admin-btn--ghost admin-btn--small"
                      onClick={() => void handleReindex(p)}
                      disabled={reindexingId !== null || deletingId !== null}
                    >
                      {reindexingId === p.id ? '…' : 'Re-index'}
                    </button>
                    <button
                      type="button"
                      className="admin-btn admin-btn--ghost admin-btn--small admin-btn--danger"
                      onClick={() => void handleDelete(p)}
                      disabled={reindexingId !== null || deletingId !== null}
                      aria-label={`Delete ${p.name}`}
                    >
                      {deletingId === p.id ? '…' : <><Trash2 size={13} /><span>Delete</span></>}
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

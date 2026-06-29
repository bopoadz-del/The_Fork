/* ProjectWorkspace — project detail + streaming chat + documents/Drive.
 *
 * Post-redesign coordinator: state, SSE wiring, and inline DocumentsPanel +
 * DrivePanel definitions live here. The visual shell (3-column layout, panels,
 * chat bubbles/composer/sources) come from layout/, chat/, and documents/.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import AppHeader from '../components/AppHeader'
import { type Project } from './ProjectCard'
import { apiGet, apiPost, apiPostForm, ApiError } from '../lib/api'
import { getToken } from '../lib/token'
import WorkspaceShell from '../layout/WorkspaceShell'
import LeftPanel from '../layout/LeftPanel'
import RightPanel from '../layout/RightPanel'
import ChatList from '../chat/ChatList'
import ChatComposer from '../chat/ChatComposer'
import SourcesList from '../chat/SourcesList'
import DocumentGraph from '../documents/DocumentGraph'
import './pages.css'
import './workspace.css'

// ── Types ──────────────────────────────────────────────────────────────────

interface ProjectDetail extends Project {
  documents?: DocumentRecord[]
  readiness?: ProjectReadiness
}

interface DocumentRecord {
  id: string
  project_id?: string
  original_name: string
  doc_type?: string
  doc_role?: string
  size?: number
  uploaded_at?: string
  /** Number of chunks the indexer stored for this doc. 0 = extraction failed. */
  chunk_count?: number
}

interface DriveFile {
  id: string
  name: string
  /** Backend returns `mime_type` (snake_case). Used to render folder
   * rows differently. */
  mime_type?: string
  /** Convenience boolean populated by the backend listing endpoint —
   * true when mime_type === application/vnd.google-apps.folder. */
  is_folder?: boolean
  modified?: string
}

interface FolderCrumb {
  /** '' represents the top of the user's Drive (My Drive root). */
  id: string
  name: string
}

interface DriveStatus {
  connected: boolean
  email?: string | null
  configured: boolean
}

interface ProjectReadiness {
  ready: boolean
  missing?: string[]
  label?: string
  status?: string
  score?: number
  percent?: number
}

type MessageRole = 'user' | 'assistant'

interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  streaming?: boolean
  error?: boolean
  /** Transient tool-activity label shown while the agent is calling tools. Cleared on first token. */
  toolStatus?: string
  /** Top retrieved sources, populated from the SSE 'end' event when RAG injection fired. */
  sources?: Array<{
    doc_id: string
    doc_name: string
    page_or_section: string
    score: number
    confidence: 'High' | 'Medium' | 'Low'
  }>
}

// ── Constants ──────────────────────────────────────────────────────────────

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

// ── Helpers ────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return iso
  }
}

function formatRelativeDate(iso: string): string {
  try {
    const ts = new Date(iso).getTime()
    if (Number.isNaN(ts)) return iso
    const diffSec = Math.round((Date.now() - ts) / 1000)
    if (diffSec < 60) return 'just now'
    const diffMin = Math.round(diffSec / 60)
    if (diffMin < 60) return `${diffMin} min ago`
    const diffHr = Math.round(diffMin / 60)
    if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? '' : 's'} ago`
    const diffDay = Math.round(diffHr / 24)
    if (diffDay < 7) return `${diffDay} day${diffDay === 1 ? '' : 's'} ago`
    if (diffDay < 30) {
      const w = Math.round(diffDay / 7)
      return `${w} week${w === 1 ? '' : 's'} ago`
    }
    return formatDate(iso)
  } catch {
    return iso
  }
}

/**
 * Classify a filename into a coarse type for the document badge.
 * Returns null for unknown — caller should fall back to ``doc_type`` from the
 * server or render no badge.
 */
function fileTypeBadge(filename: string): { label: string; kind: 'pdf' | 'docx' | 'xlsx' | 'image' | 'text' } | null {
  const ext = filename.toLowerCase().split('.').pop() || ''
  if (ext === 'pdf') return { label: 'PDF', kind: 'pdf' }
  if (ext === 'docx' || ext === 'doc') return { label: 'DOCX', kind: 'docx' }
  if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') return { label: ext.toUpperCase(), kind: 'xlsx' }
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'tif', 'tiff', 'bmp'].includes(ext)) return { label: ext.toUpperCase(), kind: 'image' }
  if (['txt', 'md', 'json', 'xml'].includes(ext)) return { label: ext.toUpperCase(), kind: 'text' }
  return null
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Combined readiness + LLM-availability state for the rail badge and the
 * composer send-button. Three mutually-exclusive modes:
 *   setting-up     — backend reports project-execution gates unsatisfied
 *                    (baseline schedule, daily reports, Aconex). Badge only;
 *                    does NOT block chat — the chat surface is functional as
 *                    soon as the project exists.
 *   ai-unavailable — last LLM call errored. Blocks send.
 *   ready          — green light, send enabled.
 */
type ReadinessMode = 'setting-up' | 'ai-unavailable' | 'ready'

function readinessMode(
  readiness: ProjectReadiness | null | undefined,
  llmAvailable: boolean,
): ReadinessMode {
  if (!llmAvailable) return 'ai-unavailable'
  if (!readiness || !readiness.ready) return 'setting-up'
  return 'ready'
}

// readinessModeLabel removed during redesign — the rail badge no longer
// surfaces this label. Tooltip still in use for the composer disabled state.

function readinessModeTooltip(mode: ReadinessMode): string {
  switch (mode) {
    case 'setting-up': return 'Project execution gates (baseline schedule, daily reports, connectors) are not yet satisfied. Chat is available.'
    case 'ai-unavailable': return 'The assistant is temporarily unreachable. Sending will retry once it recovers.'
    case 'ready': return ''
  }
}

/** Send button is gated only on streaming + actual LLM failure, not on
 *  project-execution readiness — chat must work even when the broader
 *  project setup is incomplete. */
function composerBlocked(mode: ReadinessMode): boolean {
  return mode === 'ai-unavailable'
}

function msgId(): string {
  return Math.random().toString(36).slice(2)
}

/**
 * Map raw exception text (Python tracebacks, fetch errors, HTTP status strings)
 * to a single user-safe sentence. We never want to show "Errno -2", stack
 * traces, or "HTTP 502" verbatim — the operator sees that in logs; users see
 * the friendly version.
 */
function friendlyErrorMessage(raw: string): string {
  const r = raw.toLowerCase()
  if (
    r.includes('errno -2') ||
    r.includes('errno -3') ||
    r.includes('name or service not known') ||
    r.includes('getaddrinfo') ||
    r.includes('connection refused') ||
    r.includes('connection error') ||
    r.includes('502') ||
    r.includes('503') ||
    r.includes('504') ||
    r.includes('failed to fetch') ||
    r.includes('llm call failed')
  ) {
    return 'The assistant is temporarily unavailable. Please try again in a moment.'
  }
  if (r.includes('timeout') || r.includes('timed out') || r.includes('etimedout')) {
    return 'The assistant took too long to respond. Please try again.'
  }
  if (r.includes('429') || r.includes('rate limit')) {
    return 'Too many requests right now. Please wait a moment and try again.'
  }
  if (r.includes('401') || r.includes('unauthorized') || r.includes('403')) {
    return 'Your session has expired. Please refresh the page and sign in again.'
  }
  if (r.includes('aborterror') || r.includes('aborted')) {
    return 'Request was cancelled.'
  }
  return 'Something went wrong. Please try again.'
}

// ── DocumentsPanel ─────────────────────────────────────────────────────────

interface DocumentsPanelProps {
  projectId: string
  documents: DocumentRecord[]
  onDocumentAdded: (doc: DocumentRecord) => void
  onDocumentRemoved: (docId: string) => void
}

// Cap how many document rows we render at once. The master-corpus alias backs
// thousands of documents; rendering them all in one synchronous commit froze
// the workspace on open (a ~2,700-node render blocks the main thread). 200 is
// ample for the pilot; the remainder are summarised in a footer.
const MAX_VISIBLE_DOCS = 200

function DocumentsPanel({
  projectId,
  documents,
  onDocumentAdded,
  onDocumentRemoved,
}: DocumentsPanelProps) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploadError(null)
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const resp = await apiPostForm<{ document: DocumentRecord }>(
        `/v1/projects/${projectId}/documents`,
        form,
      )
      onDocumentAdded(resp.document)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setUploading(false)
      // Reset the input so the same file can be re-uploaded if needed
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function handleDeleteDirect(doc: DocumentRecord) {
    if (!window.confirm(`Delete "${doc.original_name}"? This cannot be undone.`)) return
    setDeletingId(doc.id)
    setDeleteError(null)
    try {
      await deleteDocument(projectId, doc.id)
      onDocumentRemoved(doc.id)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Delete failed.')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="docs-panel">
      {documents.length === 0 ? (
        <div className="docs-empty">
          <span className="docs-empty__icon" aria-hidden="true">◫</span>
          <span className="docs-empty__label">No documents yet</span>
        </div>
      ) : (
        <ul className="doc-list" aria-label="Project documents">
          {documents.slice(0, MAX_VISIBLE_DOCS).map((doc) => {
            const typeBadge = fileTypeBadge(doc.original_name)
            const notIndexed = doc.chunk_count === 0
            return (
              <li key={doc.id} className="doc-row">
                <div className="doc-row__main">
                  <span className="doc-row__name" title={doc.original_name}>
                    {doc.original_name}
                  </span>
                  <span className="doc-row__meta">
                    {notIndexed ? (
                      <span
                        className="doc-tag doc-tag--not-indexed"
                        title="Extraction returned no usable text; the assistant cannot read this document"
                      >
                        Not indexed
                      </span>
                    ) : typeBadge ? (
                      <span className={`doc-tag doc-tag--type doc-tag--type-${typeBadge.kind}`}>
                        {typeBadge.label}
                      </span>
                    ) : doc.doc_type ? (
                      <span className="doc-tag">{doc.doc_type}</span>
                    ) : null}
                    {doc.doc_role && doc.doc_role !== 'other' && (
                      <span className="doc-tag doc-tag--role">{doc.doc_role.replace('_', ' ')}</span>
                    )}
                  </span>
                </div>
                <div className="doc-row__data">
                  {doc.size != null && (
                    <span className="mono doc-row__size">{formatSize(doc.size)}</span>
                  )}
                  {doc.uploaded_at && (
                    <span
                      className="doc-row__date"
                      title={formatDate(doc.uploaded_at)}
                    >
                      {formatRelativeDate(doc.uploaded_at)}
                    </span>
                  )}
                  <button
                    type="button"
                    className="doc-row__delete"
                    aria-label={`Delete ${doc.original_name}`}
                    disabled={deletingId === doc.id}
                    onClick={() => void handleDeleteDirect(doc)}
                  >
                    {deletingId === doc.id ? '…' : '×'}
                  </button>
                </div>
              </li>
            )
          })}
          {documents.length > MAX_VISIBLE_DOCS && (
            <li className="doc-row doc-row--overflow">
              Showing {MAX_VISIBLE_DOCS} of {documents.length} documents.
            </li>
          )}
        </ul>
      )}

      <div className="docs-upload">
        <input
          ref={fileInputRef}
          type="file"
          id="doc-file-input"
          className="docs-upload__input"
          onChange={(e) => void handleFileChange(e)}
          disabled={uploading}
          aria-label="Choose file to upload"
        />
        <label
          htmlFor="doc-file-input"
          className={`docs-upload__btn btn btn--ghost${uploading ? ' btn--disabled' : ''}`}
          aria-busy={uploading}
        >
          {uploading ? 'Uploading…' : '+ Upload file'}
        </label>
        {uploadError && (
          <p className="docs-upload__error" role="alert">{uploadError}</p>
        )}
        {deleteError && (
          <p className="docs-upload__error" role="alert">{deleteError}</p>
        )}
      </div>
    </div>
  )
}

// ── deleteDocument helper (DELETE verb not in api.ts) ──────────────────────

async function deleteDocument(projectId: string, documentId: string): Promise<void> {
  const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'
  const token = getToken()
  const res = await fetch(`${API_BASE}/v1/projects/${projectId}/documents/${documentId}`, {
    method: 'DELETE',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json() as { detail?: string }
      if (typeof body.detail === 'string') msg = body.detail
    } catch { /* not JSON */ }
    throw new Error(msg)
  }
}

// ── DrivePanel ─────────────────────────────────────────────────────────────

interface DrivePanelProps {
  projectId: string
  onDocumentAdded: (doc: DocumentRecord) => void
}

function DrivePanel({ projectId, onDocumentAdded }: DrivePanelProps) {
  const [status, setStatus] = useState<DriveStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [query, setQuery] = useState('')
  const [driveFiles, setDriveFiles] = useState<DriveFile[]>([])
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [importingId, setImportingId] = useState<string | null>(null)
  const [importErrors, setImportErrors] = useState<Record<string, string>>({})
  /** Breadcrumb stack — last entry is the currently-open folder. Empty
   * means the picker isn't in browse mode (search-results view or
   * idle). */
  const [folderStack, setFolderStack] = useState<FolderCrumb[]>([])

  /** Open a Drive folder (or My Drive root when id=''), refresh the
   * file list, and update the breadcrumb stack. `push=true` means we're
   * descending; `push=false` means we're clicking a crumb to go back. */
  async function openFolder(folder: FolderCrumb, push: boolean) {
    setSearchError(null)
    setDriveFiles([])
    setSearching(true)
    try {
      const url = folder.id
        ? `/v1/drive/files?folder_id=${encodeURIComponent(folder.id)}`
        : '/v1/drive/files'
      const resp = await apiGet<{ files: DriveFile[] }>(url)
      setDriveFiles(resp.files)
      setHasSearched(true)
      setFolderStack((prev) =>
        push
          ? [...prev, folder]
          : prev.slice(0, prev.findIndex((c) => c.id === folder.id) + 1),
      )
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : 'Drive browse failed.')
    } finally {
      setSearching(false)
    }
  }

  // Load Drive status on mount
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const s = await apiGet<DriveStatus>('/v1/drive/status')
        if (!cancelled) setStatus(s)
      } catch (err) {
        if (!cancelled) setStatusError(err instanceof Error ? err.message : 'Failed to load Drive status.')
      }
    })()
    return () => { cancelled = true }
  }, [])

  async function handleConnect() {
    setConnecting(true)
    try {
      const resp = await apiGet<{ auth_url: string }>('/v1/drive/connect')
      window.location.href = resp.auth_url
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : 'Failed to start Drive connection.')
      setConnecting(false)
    }
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    setSearchError(null)
    setDriveFiles([])
    setHasSearched(false)
    setSearching(true)
    try {
      const resp = await apiGet<{ files: DriveFile[] }>(`/v1/drive/files?q=${encodeURIComponent(query)}`)
      setDriveFiles(resp.files)
      setHasSearched(true)
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : 'Drive search failed.')
    } finally {
      setSearching(false)
    }
  }

  async function handleImport(file: DriveFile) {
    setImportingId(file.id)
    setImportErrors((prev) => { const next = { ...prev }; delete next[file.id]; return next })
    try {
      const resp = await apiPost<{ document: DocumentRecord }>(
        `/v1/projects/${projectId}/drive/import`,
        { file_id: file.id, name: file.name },
      )
      onDocumentAdded(resp.document)
      // Remove from search results to avoid double-import
      setDriveFiles((prev) => prev.filter((f) => f.id !== file.id))
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Import failed.'
      setImportErrors((prev) => ({ ...prev, [file.id]: msg }))
    } finally {
      setImportingId(null)
    }
  }

  if (statusError && !status) {
    return (
      <p className="drive-status-error" role="alert">{statusError}</p>
    )
  }

  if (!status) {
    return <p className="drive-loading">Checking Drive…</p>
  }

  if (!status.configured) {
    return (
      <p className="drive-not-configured">
        Google Drive not configured on this server.
      </p>
    )
  }

  if (!status.connected) {
    return (
      <div className="drive-connect">
        {statusError && <p className="drive-status-error" role="alert">{statusError}</p>}
        <button
          type="button"
          className="btn btn--ghost drive-connect__btn"
          onClick={() => void handleConnect()}
          disabled={connecting}
        >
          {connecting ? 'Redirecting…' : 'Connect Google Drive'}
        </button>
      </div>
    )
  }

  return (
    <div className="drive-connected">
      {status.email && (
        <p className="drive-email">
          Connected as <span className="mono">{status.email}</span>
        </p>
      )}
      <form className="drive-search" onSubmit={(e) => void handleSearch(e)}>
        <input
          type="search"
          className="drive-search__input"
          placeholder="Search Drive files…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search Google Drive"
          disabled={searching}
        />
        <button
          type="submit"
          className="btn btn--ghost drive-search__btn"
          disabled={searching}
          aria-label="Search"
        >
          {searching ? '…' : 'Search'}
        </button>
        <button
          type="button"
          className="btn btn--ghost drive-search__browse"
          disabled={searching}
          onClick={() => void openFolder({ id: '', name: 'My Drive' }, true)}
          aria-label="Browse my Drive"
        >
          Browse
        </button>
      </form>
      {searchError && <p className="drive-search__error" role="alert">{searchError}</p>}
      {folderStack.length > 0 && (
        <nav className="drive-breadcrumbs" aria-label="Drive folder navigation">
          <button
            type="button"
            className="drive-breadcrumb"
            onClick={() => { setFolderStack([]); setDriveFiles([]); setHasSearched(false) }}
          >
            ↑ Back to search
          </button>
          {folderStack.map((crumb, i) => (
            <span key={crumb.id || `crumb-${i}`}>
              <span className="drive-breadcrumb-sep"> / </span>
              <button
                type="button"
                className="drive-breadcrumb"
                onClick={() => void openFolder(crumb, false)}
                disabled={i === folderStack.length - 1 || searching}
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>
      )}
      {driveFiles.length > 0 && (
        <ul className="drive-file-list" aria-label="Google Drive files">
          {driveFiles.map((file) => (
            <li
              key={file.id}
              className={`drive-file-row${file.is_folder ? ' drive-file-row--folder' : ''}`}
            >
              <span className="drive-file-row__name" title={file.name}>
                {file.is_folder && (
                  <span className="drive-file-row__folder-marker">[folder]</span>
                )}
                {file.is_folder ? ' ' : ''}{file.name}
              </span>
              <div className="drive-file-row__actions">
                {importErrors[file.id] && (
                  <span className="drive-file-row__error" role="alert">
                    {importErrors[file.id]}
                  </span>
                )}
                {file.is_folder ? (
                  <button
                    type="button"
                    className="btn btn--ghost drive-file-row__open"
                    disabled={searching}
                    onClick={() => void openFolder({ id: file.id, name: file.name }, true)}
                  >
                    Open
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn--ghost drive-file-row__add"
                    disabled={importingId === file.id}
                    onClick={() => void handleImport(file)}
                  >
                    {importingId === file.id ? '…' : 'Add'}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {!searching && hasSearched && driveFiles.length === 0 && !searchError && (
        <p className="drive-no-results">No files found.</p>
      )}
    </div>
  )
}

// ── ProjectWorkspace ────────────────────────────────────────────────────────

type WorkspaceState =
  | { tag: 'loading' }
  | { tag: 'not-found' }
  | { tag: 'error'; message: string }
  | { tag: 'ready'; project: ProjectDetail }

export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>()

  const [wsState, setWsState] = useState<WorkspaceState>({ tag: 'loading' })
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  // LLM availability — flipped false when a stream errors, true again once a
  // stream completes cleanly. Drives the rail badge's "AI unavailable" state.
  const [llmAvailable, setLlmAvailable] = useState(true)

  // PR #101 / Quarry redesign:
  //   rightExpanded — right panel covers main + left as a full-width overlay
  //   driveModalOpen — DrivePanel rendered as a modal (opened from + popover)
  const [rightExpanded, setRightExpanded] = useState(false)
  const [driveModalOpen, setDriveModalOpen] = useState(false)

  // Stable conversation id tied to this project — persists across page reloads
  // so the backend agent memory carries context forward.
  const conversationId = id ? `ws-${id}` : null

  // Mirror messages into a ref so handleSend can read current history without
  // being a stale closure or needing messages in its dependency array.
  const messagesRef = useRef<ChatMessage[]>([])
  useEffect(() => { messagesRef.current = messages }, [messages])

  // Abort controller — cancel in-flight stream on unmount or re-send
  const abortRef = useRef<AbortController | null>(null)

  // ── Load project ──────────────────────────────────────────────────────────

  useEffect(() => {
    // Abort any in-flight stream and reset chat when the project id changes
    abortRef.current?.abort()
    setMessages([])

    if (!id) {
      setWsState({ tag: 'not-found' })
      return
    }
    setWsState({ tag: 'loading' })
    let cancelled = false
    void (async () => {
      try {
        const project = await apiGet<ProjectDetail>(`/v1/projects/${id}`)
        if (cancelled) return
        setWsState({ tag: 'ready', project })
        setDocuments(project.documents ?? [])

        // Load persisted conversation history for this workspace.
        // If the user has already sent a message before this resolves,
        // skip loading to avoid clobbering their in-progress turn.
        try {
          const hist = await apiGet<{
            conversation_id: string
            messages: Array<{ role: string; content: string }>
          }>(`/v1/agents/conversations/ws-${id}/messages`)
          if (cancelled) return
          if (hist.messages.length > 0 && messagesRef.current.length === 0) {
            setMessages(
              hist.messages.map((m) => ({
                id: msgId(),
                role: (m.role === 'user' ? 'user' : 'assistant') as MessageRole,
                content: m.content,
              }))
            )
          }
        } catch {
          // History fetch failed — start with an empty thread, don't block the workspace
        }
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setWsState({ tag: 'not-found' })
        } else {
          setWsState({
            tag: 'error',
            message: err instanceof Error ? err.message : 'Failed to load project.',
          })
        }
      }
    })()
    return () => { cancelled = true }
  }, [id])

  // ── Document mutation callbacks ───────────────────────────────────────────

  const handleDocumentAdded = useCallback((doc: DocumentRecord) => {
    setDocuments((prev) => [...prev, doc])
  }, [])

  // PR #104: DocumentsPanel is back in the LeftPanel — handler used again.
  const handleDocumentRemoved = useCallback((docId: string) => {
    setDocuments((prev) => prev.filter((d) => d.id !== docId))
  }, [])

  // Cleanup on unmount
  useEffect(() => () => { abortRef.current?.abort() }, [])

  // ── Send + stream ─────────────────────────────────────────────────────────

  const handleSend = useCallback(async (userText: string) => {
    if (streaming) return

    // Snapshot complete prior turns for history (before this user turn)
    const priorMessages = messagesRef.current.filter((m) => !m.streaming && !m.error)
    const historyForRequest = priorMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }))

    // Append user message + empty streaming assistant bubble.
    // Drop any stale error bubbles from a prior turn so they don't linger.
    const userMsgId = msgId()
    const assistantMsgId = msgId()

    setMessages((prev) => [
      ...prev.filter((m) => !m.error),
      { id: userMsgId, role: 'user', content: userText },
      { id: assistantMsgId, role: 'assistant', content: '', streaming: true },
    ])
    setStreaming(true)

    // Cancel any previous in-flight stream
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    // Reader-side wall-clock deadline (FOLLOW-UP #92). Resets on every
    // chunk read — token, heartbeat, or tool event all count as proof of
    // life. If 95s pass with no bytes from the server, abort the fetch and
    // surface a friendly timeout banner instead of an indefinite spinner.
    // 95s is intentionally larger than the server's CHAT_STREAM_TIMEOUT_SECONDS
    // (90s default) so the server's structured error reaches us first.
    const READER_TIMEOUT_MS = 95_000
    let didTimeout = false
    let readerTimer: ReturnType<typeof setTimeout> | null = null
    const resetReaderDeadline = () => {
      if (readerTimer !== null) clearTimeout(readerTimer)
      readerTimer = setTimeout(() => {
        didTimeout = true
        controller.abort()
      }, READER_TIMEOUT_MS)
    }
    const clearReaderDeadline = () => {
      if (readerTimer !== null) {
        clearTimeout(readerTimer)
        readerTimer = null
      }
    }

    /** Build a human-readable status label for an agent tool_call event. */
    function toolStatusLabel(toolName: string, argsPreview: string): string {
      let args: Record<string, unknown> = {}
      try {
        args = JSON.parse(argsPreview) as Record<string, unknown>
      } catch { /* truncated JSON — ignore */ }

      switch (toolName) {
        case 'search_project_documents': {
          const q = typeof args['query'] === 'string' ? args['query'] : ''
          return q ? `Searching documents for "${q}"…` : 'Searching documents…'
        }
        case 'delegate_to_agent': {
          const agent = typeof args['agent_name'] === 'string' ? args['agent_name'] : ''
          return agent ? `Delegating to ${agent}…` : 'Delegating to specialist…'
        }
        case 'remember_fact':
          return 'Saving fact to memory…'
        default:
          return `Running ${toolName}…`
      }
    }

    try {
      const res = await fetch(`${API_BASE}/v1/agents/project-assistant/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        },
        body: JSON.stringify({
          message: userText,
          project_id: id ?? null,
          conversation_id: conversationId,
          history: historyForRequest,
        }),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        let errMsg = `HTTP ${res.status}`
        try {
          const body = await res.json() as { detail?: string }
          if (typeof body.detail === 'string') errMsg = body.detail
        } catch { /* not JSON */ }
        throw new Error(errMsg)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let sseBuffer = ''
      let accumulatedContent = ''
      let firstTokenReceived = false

      // Arm the wall-clock deadline now that the response has started.
      resetReaderDeadline()

      // Read the stream chunk by chunk
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break

        // Any byte from the server counts as proof of life — covers tokens,
        // heartbeats, tool events, anything. Reset the wall-clock deadline.
        resetReaderDeadline()

        sseBuffer += decoder.decode(value, { stream: true })

        // SSE events are separated by \n\n
        const events = sseBuffer.split('\n\n')
        // Last element may be a partial event — keep it in the buffer
        sseBuffer = events.pop() ?? ''

        for (const eventBlock of events) {
          for (const line of eventBlock.split('\n')) {
            if (!line.startsWith('data:')) continue
            const jsonStr = line.slice('data:'.length).trim()
            if (!jsonStr) continue

            let evt: Record<string, unknown>
            try {
              evt = JSON.parse(jsonStr) as Record<string, unknown>
            } catch {
              continue // malformed line — skip
            }

            const evtType = evt['type'] as string | undefined

            if (evtType === 'start') {
              // Agent stream start — {type, agent}. No session_id to echo back.
            } else if (evtType === 'heartbeat') {
              // Server is alive but the LLM hasn't produced a token yet.
              // The byte arrival already reset the reader deadline above;
              // we render nothing for heartbeats — they exist solely as
              // proof of life for the consumer-side wall-clock.
            } else if (evtType === 'tool_call') {
              // Show ephemeral status inside the assistant bubble while tools run.
              const toolName = typeof evt['tool'] === 'string' ? evt['tool'] : 'tool'
              const argsPreview = typeof evt['args_preview'] === 'string' ? evt['args_preview'] : ''
              const status = toolStatusLabel(toolName, argsPreview)
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, toolStatus: status }
                    : m
                )
              )
            } else if (evtType === 'tool_result') {
              // Tool finished — clear the status; real tokens follow.
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, toolStatus: undefined }
                    : m
                )
              )
            } else if (evtType === 'token') {
              const token = typeof evt['content'] === 'string' ? evt['content'] : ''
              // Clear toolStatus on the first real token so no ghost label appears.
              if (!firstTokenReceived) {
                firstTokenReceived = true
                accumulatedContent += token
                const snap = accumulatedContent
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsgId
                      ? { ...m, content: snap, streaming: true, toolStatus: undefined }
                      : m
                  )
                )
              } else {
                accumulatedContent += token
                const snap = accumulatedContent
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsgId
                      ? { ...m, content: snap, streaming: true }
                      : m
                  )
                )
              }
            } else if (evtType === 'end') {
              const finalContent = accumulatedContent
              const rawSources = Array.isArray(evt['sources']) ? (evt['sources'] as ChatMessage['sources']) : []
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: finalContent, streaming: false, toolStatus: undefined, sources: rawSources }
                    : m
                )
              )
              setLlmAvailable(true)
            } else if (evtType === 'error') {
              const rawMsg =
                typeof evt['message'] === 'string'
                  ? evt['message']
                  : 'stream error'
              const friendly = friendlyErrorMessage(rawMsg)
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: friendly, streaming: false, error: true, toolStatus: undefined }
                    : m
                )
              )
              setLlmAvailable(false)
              setTimeout(() => {
                setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId))
                setLlmAvailable(true)
              }, 8000)
            }
          }
        }
      }

      // If no explicit 'end' event arrived, finalise anyway
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId && m.streaming
            ? { ...m, streaming: false, toolStatus: undefined }
            : m
        )
      )
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        if (didTimeout) {
          // OUR timeout fired (95s of reader silence) — surface a friendly
          // error rather than the silent-cancel UX, otherwise the user is
          // left with an indefinite spinner that just vanished.
          const friendly = friendlyErrorMessage('timeout')
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? { ...m, content: friendly, streaming: false, error: true, toolStatus: undefined }
                : m
            )
          )
          setLlmAvailable(false)
          setTimeout(() => {
            setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId))
            setLlmAvailable(true)
          }, 8000)
          return
        }
        // Intentional cancel — mark assistant message done, keep content, clear tool status
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId && m.streaming
              ? { ...m, streaming: false, toolStatus: undefined }
              : m
          )
        )
        return
      }
      const rawMsg = err instanceof Error ? err.message : 'stream failed'
      const friendly = friendlyErrorMessage(rawMsg)
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId
            ? { ...m, content: friendly, streaming: false, error: true, toolStatus: undefined }
            : m
        )
      )
      setLlmAvailable(false)
      setTimeout(() => {
        setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId))
        setLlmAvailable(true)
      }, 8000)
    } finally {
      clearReaderDeadline()
      setStreaming(false)
    }
  }, [id, conversationId, streaming])

  // ── Render ────────────────────────────────────────────────────────────────

  const projectName = wsState.tag === 'ready' ? wsState.project.name : (id ?? '—')

  const breadcrumb = (
    <>
      <Link to="/" className="nav-item">Projects</Link>
      <span className="nav-sep">/</span>
      <span className="nav-item nav-item--active mono">{projectName}</span>
    </>
  )

  if (wsState.tag === 'loading') {
    return (
      <div className="workspace-shell page-shell">
        <AppHeader breadcrumb={breadcrumb} />
        <div className="workspace-loading">
          <span className="workspace-loading__label">Loading project…</span>
        </div>
      </div>
    )
  }

  if (wsState.tag === 'not-found') {
    return (
      <div className="workspace-shell page-shell">
        <AppHeader breadcrumb={breadcrumb} />
        <div className="workspace-notfound">
          <span className="workspace-notfound__code">404</span>
          <h2>Project not found</h2>
          <p className="workspace-notfound__message">
            This project doesn&apos;t exist or you don&apos;t have access to it.
          </p>
          <Link to="/" className="btn btn--primary" style={{ textDecoration: 'none' }}>
            ← Back to projects
          </Link>
        </div>
      </div>
    )
  }

  if (wsState.tag === 'error') {
    return (
      <div className="workspace-shell page-shell">
        <AppHeader breadcrumb={breadcrumb} />
        <div className="workspace-notfound">
          <span className="workspace-notfound__code">Error</span>
          <h2>Could not load project</h2>
          <p className="workspace-notfound__message">{wsState.message}</p>
          <Link to="/" className="btn btn--ghost" style={{ textDecoration: 'none' }}>
            ← Back to projects
          </Link>
        </div>
      </div>
    )
  }

  const { project } = wsState
  const mode = readinessMode(project.readiness, llmAvailable)
  const composerIsBlocked = composerBlocked(mode)
  const composerBlockedReason = composerIsBlocked ? readinessModeTooltip(mode) : undefined

  // Latest assistant message's sources drive the right-panel SourcesList and
  // the DocumentGraph "cited" highlight. Falls back to undefined when the
  // last message is the user, an error bubble, or has no sources yet.
  const latestAssistant = [...messages].reverse().find(
    (m) => m.role === 'assistant' && !m.error,
  )
  const latestSources = latestAssistant?.sources ?? []
  const citedDocIds = latestSources.map((s) => s.doc_id)
  void mode  // mode used inside left panel via DocumentsPanel/onClear hooks below

  // PR #104: DocumentsPanel is back, rendered as a slot inside LeftPanel
  // (between Projects and Conversation). Conversation Clear + Export
  // handlers are also lifted out so the LeftPanel can drive them directly
  // without duplicating fetch wiring. Drive still opens as a modal from
  // the ChatComposer's + popover.

  async function exportConversation() {
    if (!id || !conversationId) return
    const token = getToken() || ''
    try {
      const res = await fetch(
        `${API_BASE}/v1/projects/${id}/conversations/${conversationId}/export?format=docx`,
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        alert(`Export failed (${res.status}): ${detail.slice(0, 200)}`)
        return
      }
      const blob = await res.blob()
      const a = document.createElement('a')
      const objUrl = URL.createObjectURL(blob)
      a.href = objUrl
      const cd = res.headers.get('Content-Disposition') || ''
      const m = /filename="?([^";]+)"?/.exec(cd)
      a.download = m?.[1] || `the-fork-${conversationId.slice(0, 8)}.docx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(objUrl)
    } catch (err) {
      alert(`Export error: ${(err as Error).message}`)
    }
  }

  async function clearConversation() {
    if (!id || !conversationId) return
    if (!window.confirm(
      'Clear this conversation? The chat history on the server will be wiped. This cannot be undone.',
    )) return
    try {
      const token = getToken() || ''
      const res = await fetch(
        `${API_BASE}/v1/projects/${id}/conversations/${conversationId}/clear`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: '{}',
        },
      )
      if (!res.ok) {
        alert(`Clear failed (${res.status})`)
        return
      }
      setMessages([])
    } catch (err) {
      alert(`Clear failed: ${(err as Error).message}`)
    }
  }

  return (
    <>
    <WorkspaceShell
      header={<AppHeader breadcrumb={breadcrumb} />}
      rightExpanded={rightExpanded}
      left={
        <LeftPanel
          activeProjectId={id}
          activeProjectName={projectName}
          messageCount={messages.length}
          onExportConversation={exportConversation}
          onClearConversation={clearConversation}
          documents={
            <DocumentsPanel
              projectId={id ?? ''}
              documents={documents}
              onDocumentAdded={handleDocumentAdded}
              onDocumentRemoved={handleDocumentRemoved}
            />
          }
        />
      }
      main={
        <div className="workspace-main">
          <ChatList
            messages={messages}
            documentCount={documents.length}
            onSuggestion={(text) => void handleSend(text)}
            suggestionsDisabled={streaming || composerIsBlocked}
            onDownloadMessage={(assistantIndex) => {
              if (!id || !conversationId) return
              const token = getToken() || ''
              const url = `${API_BASE}/v1/projects/${id}/conversations/${conversationId}/export?format=docx&message_index=${assistantIndex}`
              void fetch(url, {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
              })
                .then(async (res) => {
                  if (!res.ok) {
                    const detail = await res.text().catch(() => '')
                    alert(`Download failed (${res.status}): ${detail.slice(0, 200)}`)
                    return
                  }
                  const blob = await res.blob()
                  const a = document.createElement('a')
                  const objUrl = URL.createObjectURL(blob)
                  a.href = objUrl
                  const cd = res.headers.get('Content-Disposition') || ''
                  const m = /filename="?([^";]+)"?/.exec(cd)
                  a.download = m?.[1] || `the-fork-${conversationId.slice(0, 8)}.docx`
                  document.body.appendChild(a)
                  a.click()
                  a.remove()
                  URL.revokeObjectURL(objUrl)
                })
                .catch((e) => alert(`Download error: ${(e as Error).message}`))
            }}
          />
          <ChatComposer
            onSend={(text) => void handleSend(text)}
            disabled={streaming || composerIsBlocked}
            disabledReason={composerBlockedReason}
            projectId={id ?? ''}
            hasHistory={messages.length > 0}
            onOpenDrive={() => setDriveModalOpen(true)}
            onClear={clearConversation}
          />
        </div>
      }
      right={
        <RightPanel
          title="Sources"
          expanded={rightExpanded}
          onToggleExpand={() => setRightExpanded((v) => !v)}
          sources={
            <SourcesList
              sources={latestSources}
              streaming={latestAssistant?.streaming}
            />
          }
          graph={
            <DocumentGraph
              documents={documents.map((d) => ({
                id: d.id,
                original_name: d.original_name,
                doc_type: d.doc_type,
              }))}
              citedDocIds={citedDocIds}
            />
          }
        />
      }
    />
    {driveModalOpen && (
      <div
        className="ws-modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="Google Drive picker"
        onClick={(ev) => {
          if (ev.target === ev.currentTarget) setDriveModalOpen(false)
        }}
        onKeyDown={(ev) => {
          if (ev.key === 'Escape') setDriveModalOpen(false)
        }}
      >
        <div className="ws-modal">
          <div className="ws-modal__head">
            <span className="ws-modal__title">Google Drive</span>
            <button
              type="button"
              className="ws-modal__close"
              onClick={() => setDriveModalOpen(false)}
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <div className="ws-modal__body">
            <DrivePanel
              projectId={id ?? ''}
              onDocumentAdded={(doc) => {
                handleDocumentAdded(doc)
                setDriveModalOpen(false)
              }}
            />
          </div>
        </div>
      </div>
    )}
    </>
  )
}

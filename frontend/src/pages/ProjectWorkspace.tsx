/* ProjectWorkspace — project detail + streaming chat (B4) + documents/Drive (B5) */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import AppHeader from '../components/AppHeader'
import { type Project } from './ProjectCard'
import { apiGet, apiPost, apiPostForm, ApiError } from '../lib/api'
import { getToken } from '../lib/token'
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
}

interface DriveFile {
  id: string
  name: string
  mimeType?: string
  modifiedTime?: string
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

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function readinessDisplay(r: ProjectReadiness): { label: string; ready: boolean } {
  if (typeof r.label === 'string' && r.label) return { label: r.label, ready: r.ready }
  if (typeof r.status === 'string' && r.status) return { label: r.status, ready: r.ready }
  return { label: r.ready ? 'Ready' : 'Not ready', ready: r.ready }
}

function msgId(): string {
  return Math.random().toString(36).slice(2)
}

// ── ChatMessage bubble ─────────────────────────────────────────────────────

interface ChatMessageBubbleProps {
  message: ChatMessage
}

function ChatMessageBubble({ message }: ChatMessageBubbleProps) {
  const isUser = message.role === 'user'
  if (message.error) {
    return (
      <div className="chat-error-bubble" role="alert">
        {message.content || 'An error occurred. Please try again.'}
      </div>
    )
  }
  return (
    <div className={`chat-message chat-message--${message.role}`}>
      <div className="chat-message__avatar" aria-hidden="true">
        {isUser ? 'U' : 'TF'}
      </div>
      <div className="chat-message__bubble">
        {message.toolStatus && (
          <div className="chat-tool-status" aria-live="polite">
            {message.toolStatus}
          </div>
        )}
        <div className="chat-message__content">
          {message.content}
          {message.streaming && <span className="chat-cursor" aria-hidden="true" />}
        </div>
      </div>
    </div>
  )
}

// ── ChatComposer ───────────────────────────────────────────────────────────

interface ChatComposerProps {
  onSend: (text: string) => void
  disabled: boolean
  projectId: string
  onAttached?: (docName: string) => void
}

function ChatComposer({ onSend, disabled, projectId, onAttached }: ChatComposerProps) {
  const [text, setText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [attachStatus, setAttachStatus] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function submit() {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setText(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }

  async function uploadFile(file: File, role = 'other') {
    setUploading(true)
    setAttachStatus(`Uploading ${file.name}…`)
    try {
      const token = localStorage.getItem('thefork.jwt') || ''
      const fd = new FormData()
      fd.append('file', file)
      fd.append('role', role)
      const res = await fetch(`${API_BASE}/v1/projects/${projectId}/documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      })
      if (!res.ok) {
        const errBody = await res.text()
        setAttachStatus(`Upload failed (${res.status}): ${errBody.slice(0, 120)}`)
        return
      }
      const body = await res.json()
      const docName = body?.document?.original_name || file.name
      setAttachStatus(`Attached: ${docName}`)
      onAttached?.(docName)
      setText((prev) => (prev ? `${prev}\n` : '') + `[attached: ${docName}] `)
      setTimeout(() => setAttachStatus(null), 4000)
    } catch (err) {
      setAttachStatus(`Upload error: ${(err as Error).message}`)
    } finally {
      setUploading(false)
    }
  }

  async function startVoiceRecording() {
    if (recording) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      audioChunksRef.current = []
      mr.ondataavailable = (ev) => {
        if (ev.data.size > 0) audioChunksRef.current.push(ev.data)
      }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        const file = new File([blob], `voice-${Date.now()}.webm`, { type: 'audio/webm' })
        await uploadFile(file, 'other')
      }
      mediaRecorderRef.current = mr
      mr.start()
      setRecording(true)
      setAttachStatus('Recording — click 🎤 again to stop')
    } catch (err) {
      setAttachStatus(`Mic blocked: ${(err as Error).message}`)
    }
  }

  function stopVoiceRecording() {
    mediaRecorderRef.current?.stop()
    mediaRecorderRef.current = null
    setRecording(false)
  }

  return (
    <div className="chat-composer">
      {attachStatus && (
        <p className="chat-composer__attach-status" aria-live="polite">{attachStatus}</p>
      )}
      <div className="chat-composer__inner">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff,.dxf,.ifc,.xer,.mp3,.wav,.webm,.mp4"
          onChange={(e) => {
            const files = e.target.files
            if (files) Array.from(files).forEach((f) => uploadFile(f))
            e.target.value = ''
          }}
        />
        <input
          ref={cameraInputRef}
          type="file"
          accept="image/*"
          capture="environment"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) uploadFile(f)
            e.target.value = ''
          }}
        />
        <button
          type="button"
          className="chat-composer__attach"
          title="Attach file"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || uploading}
          aria-label="Attach file"
        >
          📎
        </button>
        <button
          type="button"
          className="chat-composer__attach"
          title="Take photo"
          onClick={() => cameraInputRef.current?.click()}
          disabled={disabled || uploading}
          aria-label="Take photo"
        >
          📷
        </button>
        <button
          type="button"
          className={`chat-composer__attach ${recording ? 'chat-composer__attach--recording' : ''}`}
          title={recording ? 'Stop recording' : 'Voice note'}
          onClick={() => (recording ? stopVoiceRecording() : startVoiceRecording())}
          disabled={disabled || uploading}
          aria-label={recording ? 'Stop recording' : 'Record voice'}
        >
          {recording ? '⏹' : '🎤'}
        </button>
        <textarea
          ref={textareaRef}
          className="chat-composer__textarea"
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="Ask, attach a doc, take a photo, or hold the mic…"
          disabled={disabled}
          rows={1}
          aria-label="Chat message"
        />
        <button
          type="button"
          className="chat-composer__send"
          onClick={submit}
          disabled={disabled || !text.trim()}
          aria-label="Send message"
        >
          ↑
        </button>
      </div>
      <p className="chat-composer__hint">
        Enter to send · Shift+Enter for newline · 📎 attach · 📷 photo · 🎤 voice
      </p>
    </div>
  )
}

// ── ChatThread ─────────────────────────────────────────────────────────────

interface ChatThreadProps {
  messages: ChatMessage[]
}

function ChatThread({ messages }: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="chat-empty">
        <div className="chat-empty__mark" aria-hidden="true">⌬</div>
        <p className="chat-empty__heading">Project Intelligence</p>
        <p className="chat-empty__hint">
          Ask about documents, drawings, schedules, or contract status. The
          assistant can search your project files, run calculations, and
          delegate to specialist agents.
        </p>
      </div>
    )
  }

  return (
    <div className="chat-thread" role="log" aria-live="polite" aria-label="Conversation">
      {messages.map((msg) => (
        <ChatMessageBubble key={msg.id} message={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

// ── DocumentsPanel ─────────────────────────────────────────────────────────

interface DocumentsPanelProps {
  projectId: string
  documents: DocumentRecord[]
  onDocumentAdded: (doc: DocumentRecord) => void
  onDocumentRemoved: (docId: string) => void
}

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
          {documents.map((doc) => (
            <li key={doc.id} className="doc-row">
              <div className="doc-row__main">
                <span className="doc-row__name" title={doc.original_name}>
                  {doc.original_name}
                </span>
                <span className="doc-row__meta">
                  {doc.doc_type && (
                    <span className="doc-tag">{doc.doc_type}</span>
                  )}
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
                  <span className="mono doc-row__date">{formatDate(doc.uploaded_at)}</span>
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
          ))}
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
      </form>
      {searchError && <p className="drive-search__error" role="alert">{searchError}</p>}
      {driveFiles.length > 0 && (
        <ul className="drive-file-list" aria-label="Google Drive files">
          {driveFiles.map((file) => (
            <li key={file.id} className="drive-file-row">
              <span className="drive-file-row__name" title={file.name}>{file.name}</span>
              <div className="drive-file-row__actions">
                {importErrors[file.id] && (
                  <span className="drive-file-row__error" role="alert">
                    {importErrors[file.id]}
                  </span>
                )}
                <button
                  type="button"
                  className="btn btn--ghost drive-file-row__add"
                  disabled={importingId === file.id}
                  onClick={() => void handleImport(file)}
                >
                  {importingId === file.id ? '…' : 'Add'}
                </button>
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

// ── WorkspaceRail ──────────────────────────────────────────────────────────

interface RailProps {
  project: ProjectDetail
  documents: DocumentRecord[]
  onDocumentAdded: (doc: DocumentRecord) => void
  onDocumentRemoved: (docId: string) => void
}

function WorkspaceRail({ project, documents, onDocumentAdded, onDocumentRemoved }: RailProps) {
  const readiness = project.readiness
    ? readinessDisplay(project.readiness as ProjectReadiness)
    : null

  return (
    <aside className="workspace-rail" aria-label="Project details">
      {/* Metadata */}
      <div className="rail-section">
        <div className="rail-section__title">Project</div>

        <div className="rail-meta-row">
          <span className="rail-meta-label">Name</span>
          <span className="rail-meta-value">{project.name}</span>
        </div>

        {project.client && (
          <div className="rail-meta-row">
            <span className="rail-meta-label">Client</span>
            <span className="rail-meta-value">{project.client}</span>
          </div>
        )}

        <div className="rail-meta-row">
          <span className="rail-meta-label">Status</span>
          <span className="rail-meta-value">{project.status}</span>
        </div>

        <div className="rail-meta-row">
          <span className="rail-meta-label">Created</span>
          <span className="rail-meta-value">{formatDate(project.created_at)}</span>
        </div>

        <div className="rail-meta-row">
          <span className="rail-meta-label">ID</span>
          <span className="rail-meta-value mono">{project.id}</span>
        </div>

        {readiness && (
          <div className="rail-meta-row">
            <span className="rail-meta-label">Readiness</span>
            <span
              className={`readiness-badge ${readiness.ready ? 'readiness-badge--ready' : 'readiness-badge--not-ready'}`}
            >
              {readiness.label}
            </span>
          </div>
        )}
      </div>

      {/* Documents — B5 */}
      <div className="rail-section">
        <div className="rail-section__title">
          Documents
          {documents.length > 0 && (
            <span className="rail-section__count">{documents.length}</span>
          )}
        </div>
        <DocumentsPanel
          projectId={project.id}
          documents={documents}
          onDocumentAdded={onDocumentAdded}
          onDocumentRemoved={onDocumentRemoved}
        />
      </div>

      {/* Google Drive — B5 */}
      <div className="rail-section">
        <div className="rail-section__title">Google Drive</div>
        <DrivePanel
          projectId={project.id}
          onDocumentAdded={onDocumentAdded}
        />
      </div>
    </aside>
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

    // Append user message + empty streaming assistant bubble
    const userMsgId = msgId()
    const assistantMsgId = msgId()

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', content: userText },
      { id: assistantMsgId, role: 'assistant', content: '', streaming: true },
    ])
    setStreaming(true)

    // Cancel any previous in-flight stream
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

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

      // Read the stream chunk by chunk
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break

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
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: finalContent, streaming: false, toolStatus: undefined }
                    : m
                )
              )
            } else if (evtType === 'error') {
              const errMsg =
                typeof evt['message'] === 'string'
                  ? evt['message']
                  : 'An error occurred during streaming.'
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: errMsg, streaming: false, error: true, toolStatus: undefined }
                    : m
                )
              )
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
      const errMsg = err instanceof Error ? err.message : 'Stream failed.'
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId
            ? { ...m, content: errMsg, streaming: false, error: true, toolStatus: undefined }
            : m
        )
      )
    } finally {
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

  return (
    <div className="workspace-shell">
      <AppHeader breadcrumb={breadcrumb} />

      <div className="workspace-body">
        {/* Primary conversation column */}
        <div className="workspace-chat">
          <ChatThread messages={messages} />
          <ChatComposer
            onSend={(text) => void handleSend(text)}
            disabled={streaming}
            projectId={id ?? ''}
          />
        </div>

        {/* Right rail — project metadata + documents + Drive (B5) */}
        <WorkspaceRail
          project={project}
          documents={documents}
          onDocumentAdded={handleDocumentAdded}
          onDocumentRemoved={handleDocumentRemoved}
        />
      </div>
    </div>
  )
}

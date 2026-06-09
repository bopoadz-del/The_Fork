/* ProjectWorkspace — project detail + streaming chat (B4) + documents/Drive (B5) */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
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
  /** Number of chunks the indexer stored for this doc. 0 = extraction failed. */
  chunk_count?: number
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
 * Combined readiness + LLM-availability state used by the rail badge and the
 * composer send-button. Three mutually-exclusive modes:
 *   setting-up    — project still indexing, no chat allowed
 *   ai-unavailable — project ready but last LLM call failed
 *   ready         — green light, send enabled
 */
type ReadinessMode = 'setting-up' | 'ai-unavailable' | 'ready'

function readinessMode(
  readiness: ProjectReadiness | null | undefined,
  llmAvailable: boolean,
): ReadinessMode {
  if (!readiness || !readiness.ready) return 'setting-up'
  if (!llmAvailable) return 'ai-unavailable'
  return 'ready'
}

function readinessModeLabel(mode: ReadinessMode): string {
  switch (mode) {
    case 'setting-up': return 'Setting up...'
    case 'ai-unavailable': return 'AI unavailable'
    case 'ready': return 'Ready'
  }
}

function readinessModeTooltip(mode: ReadinessMode): string {
  switch (mode) {
    case 'setting-up': return 'Project is indexing documents. Chat will be available shortly.'
    case 'ai-unavailable': return 'The assistant is temporarily unreachable. Sending will retry once it recovers.'
    case 'ready': return ''
  }
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

// ── ChatMessage bubble ─────────────────────────────────────────────────────

interface ChatMessageBubbleProps {
  message: ChatMessage
}

function ChatMessageBubble({ message }: ChatMessageBubbleProps) {
  const isUser = message.role === 'user'
  if (message.error) {
    return (
      <div className="chat-error-bubble" role="alert">
        <svg
          className="chat-error-bubble__icon"
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        <span className="chat-error-bubble__text">
          {message.content || 'Something went wrong. Please try again.'}
        </span>
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
          {isUser ? (
            <span className="chat-message__text">{message.content}</span>
          ) : message.streaming && !message.content ? (
            <span className="chat-typing" aria-label="Assistant is thinking">
              <span className="chat-typing__dot" />
              <span className="chat-typing__dot" />
              <span className="chat-typing__dot" />
            </span>
          ) : (
            <div className="chat-message__markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
              {message.streaming && <span className="chat-cursor" aria-hidden="true" />}
            </div>
          )}
        </div>
        {message.role === 'assistant' && message.sources && message.sources.length > 0 && (
          <details className="chat-message__sources">
            <summary className="chat-message__sources-summary">
              <span className="chat-message__sources-chevron" aria-hidden="true" />
              Sources ({message.sources.length})
            </summary>
            <ul className="chat-message__sources-list">
              {message.sources.map((s, i) => (
                <li key={i} className="chat-message__sources-row">
                  <span className="chat-message__sources-doc">
                    <svg
                      className="chat-message__sources-icon"
                      viewBox="0 0 24 24"
                      width="14"
                      height="14"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <polyline points="14 2 14 8 20 8" />
                    </svg>
                    <span className="chat-message__sources-name" title={s.doc_name || s.doc_id}>
                      {s.doc_name || s.doc_id}
                    </span>
                    {s.page_or_section && (
                      <span className="chat-message__sources-loc">{s.page_or_section}</span>
                    )}
                  </span>
                  <span className="chat-message__sources-tail">
                    <span className="chat-message__sources-score">{s.score.toFixed(2)}</span>
                    <span className={`chat-message__sources-badge chat-message__sources-badge--${s.confidence.toLowerCase()}`}>
                      {s.confidence}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  )
}

// ── ChatComposer ───────────────────────────────────────────────────────────

interface ChatComposerProps {
  onSend: (text: string) => void
  disabled: boolean
  /** Tooltip shown on the send button when disabled by external state (not just empty text) */
  disabledReason?: string
  projectId: string
  onAttached?: (docName: string) => void
  onClear?: () => void
  hasHistory?: boolean
}

function ChatComposer({ onSend, disabled, disabledReason, projectId, onAttached, onClear, hasHistory }: ChatComposerProps) {
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
      const token = getToken() || ''
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
      setAttachStatus('Recording — click Stop to finish')
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
          Attach
        </button>
        <button
          type="button"
          className="chat-composer__attach"
          title="Take photo"
          onClick={() => cameraInputRef.current?.click()}
          disabled={disabled || uploading}
          aria-label="Take photo"
        >
          Photo
        </button>
        <button
          type="button"
          className={`chat-composer__attach ${recording ? 'chat-composer__attach--recording' : ''}`}
          title={recording ? 'Stop recording' : 'Voice note'}
          onClick={() => (recording ? stopVoiceRecording() : startVoiceRecording())}
          disabled={disabled || uploading}
          aria-label={recording ? 'Stop recording' : 'Record voice'}
        >
          {recording ? 'Stop' : 'Voice'}
        </button>
        {onClear && hasHistory && (
          <button
            type="button"
            className="chat-composer__attach"
            title="Clear chat history (cannot be undone)"
            onClick={() => onClear()}
            disabled={disabled || uploading}
            aria-label="Clear chat history"
          >
            Clear
          </button>
        )}
        <textarea
          ref={textareaRef}
          className="chat-composer__textarea"
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your project documents..."
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
          title={disabled && disabledReason ? disabledReason : undefined}
        >
          ↑
        </button>
      </div>
      <p className="chat-composer__hint">
        Enter to send &middot; Shift+Enter for new line
      </p>
    </div>
  )
}

// ── ChatThread ─────────────────────────────────────────────────────────────

interface ChatThreadProps {
  messages: ChatMessage[]
  documentCount: number
  onSuggestion: (text: string) => void
  suggestionsDisabled: boolean
}

const EMPTY_SUGGESTIONS = [
  'What is the IT load specification?',
  'Summarise the key BOQ items',
  'What are the main project risks?',
]

function ChatThread({ messages, documentCount, onSuggestion, suggestionsDisabled }: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    const docLabel =
      documentCount === 0
        ? 'No documents indexed yet for this project'
        : documentCount === 1
          ? 'I have access to 1 document in this project'
          : `I have access to ${documentCount} documents in this project`
    return (
      <div className="chat-empty">
        <svg
          className="chat-empty__art"
          viewBox="0 0 96 96"
          width="96"
          height="96"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M14 28h28l6 8h34v44a4 4 0 0 1-4 4H18a4 4 0 0 1-4-4V28z" />
          <path d="M28 50h40M28 60h40M28 70h28" />
          <path d="M58 14l8 4 8-4v18l-8 4-8-4z" opacity="0.55" />
        </svg>
        <p className="chat-empty__heading">Ask anything about your project</p>
        <p className="chat-empty__hint">{docLabel}</p>
        <div className="chat-empty__chips" role="group" aria-label="Suggested questions">
          {EMPTY_SUGGESTIONS.map((suggestion) => (
            <button
              type="button"
              key={suggestion}
              className="chat-empty__chip"
              disabled={suggestionsDisabled}
              onClick={() => onSuggestion(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
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
          {documents.map((doc) => {
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
  readinessMode: ReadinessMode
  onDocumentAdded: (doc: DocumentRecord) => void
  onDocumentRemoved: (docId: string) => void
}

function WorkspaceRail({ project, documents, readinessMode: mode, onDocumentAdded, onDocumentRemoved }: RailProps) {
  const modeLabel = readinessModeLabel(mode)
  const tooltip = readinessModeTooltip(mode)

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

        <div className="rail-meta-row">
          <span className="rail-meta-label">Readiness</span>
          <span
            className={`readiness-badge readiness-badge--${mode}`}
            title={tooltip || undefined}
            aria-label={tooltip ? `${modeLabel}. ${tooltip}` : modeLabel}
          >
            <span className="readiness-badge__dot" aria-hidden="true" />
            {modeLabel}
          </span>
        </div>
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
  // LLM availability — flipped false when a stream errors, true again once a
  // stream completes cleanly. Drives the rail badge's "AI unavailable" state.
  const [llmAvailable, setLlmAvailable] = useState(true)

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
      }, 8000)
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
  const mode = readinessMode(project.readiness, llmAvailable)
  const composerBlockedReason = readinessModeTooltip(mode) || undefined

  return (
    <div className="workspace-shell">
      <AppHeader breadcrumb={breadcrumb} />

      <div className="workspace-body">
        {/* Primary conversation column */}
        <div className="workspace-chat">
          <ChatThread
            messages={messages}
            documentCount={documents.length}
            onSuggestion={(text) => void handleSend(text)}
            suggestionsDisabled={streaming || mode !== 'ready'}
          />
          <ChatComposer
            onSend={(text) => void handleSend(text)}
            disabled={streaming || mode !== 'ready'}
            disabledReason={composerBlockedReason}
            projectId={id ?? ''}
            hasHistory={messages.length > 0}
            onClear={async () => {
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
                    headers: {
                      Authorization: `Bearer ${token}`,
                      'Content-Type': 'application/json',
                    },
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
            }}
          />
        </div>

        {/* Right rail — project metadata + documents + Drive (B5) */}
        <WorkspaceRail
          project={project}
          documents={documents}
          readinessMode={mode}
          onDocumentAdded={handleDocumentAdded}
          onDocumentRemoved={handleDocumentRemoved}
        />
      </div>
    </div>
  )
}

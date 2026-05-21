/* ProjectWorkspace — project detail + streaming chat (B4) */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import AppHeader from '../components/AppHeader'
import { type Project } from './ProjectCard'
import { apiGet, ApiError } from '../lib/api'
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
  name: string
  doc_type?: string
  doc_role?: string
  size?: number
  created_at?: string
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
}

function ChatComposer({ onSend, disabled }: ChatComposerProps) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

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

  return (
    <div className="chat-composer">
      <div className="chat-composer__inner">
        <textarea
          ref={textareaRef}
          className="chat-composer__textarea"
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything about this project…"
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
        Enter to send · Shift+Enter for newline
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
          Ask anything about this project — documents, schedule, contract status,
          or anything else in the project context.
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

// ── WorkspaceRail ──────────────────────────────────────────────────────────

interface RailProps {
  project: ProjectDetail
}

function WorkspaceRail({ project }: RailProps) {
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

      {/* Documents placeholder — B5 will implement upload + list here */}
      <div className="rail-section">
        <div className="rail-section__title">Documents</div>
        {/* TODO B5: replace with document list + upload button */}
        <div className="rail-docs-placeholder">
          <span className="rail-docs-placeholder__icon">◫</span>
          <span>Document upload coming in B5</span>
        </div>
        {Array.isArray(project.documents) && project.documents.length > 0 && (
          <p style={{ marginTop: 'var(--space-2)', fontSize: '12px', color: 'var(--text-muted)' }}>
            {project.documents.length} document
            {project.documents.length !== 1 ? 's' : ''} attached
          </p>
        )}
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
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string>('default')

  // Mirror messages into a ref so handleSend can read current history without
  // being a stale closure or needing messages in its dependency array.
  const messagesRef = useRef<ChatMessage[]>([])
  useEffect(() => { messagesRef.current = messages }, [messages])

  // Abort controller — cancel in-flight stream on unmount or re-send
  const abortRef = useRef<AbortController | null>(null)

  // ── Load project ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!id) {
      setWsState({ tag: 'not-found' })
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const project = await apiGet<ProjectDetail>(`/v1/projects/${id}`)
        if (!cancelled) setWsState({ tag: 'ready', project })
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

    // Append user message
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

    try {
      const res = await fetch(`${API_BASE}/v1/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        },
        body: JSON.stringify({
          prompt: userText,
          model: 'deepseek-chat',
          session_id: sessionId,
          project_id: id ?? null,
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
              if (typeof evt['session_id'] === 'string') {
                setSessionId(evt['session_id'])
              }
            } else if (evtType === 'token') {
              const token = typeof evt['content'] === 'string' ? evt['content'] : ''
              accumulatedContent += token
              const snap = accumulatedContent
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: snap, streaming: true }
                    : m
                )
              )
            } else if (evtType === 'end') {
              const finalContent = accumulatedContent
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: finalContent, streaming: false }
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
                    ? { ...m, content: errMsg, streaming: false, error: true }
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
            ? { ...m, streaming: false }
            : m
        )
      )
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // Intentional cancel — mark assistant message as done, keep content
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId && m.streaming ? { ...m, streaming: false } : m
          )
        )
        return
      }
      const errMsg = err instanceof Error ? err.message : 'Stream failed.'
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId
            ? { ...m, content: errMsg, streaming: false, error: true }
            : m
        )
      )
    } finally {
      setStreaming(false)
    }
  }, [id, sessionId, streaming])

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
          />
        </div>

        {/* Right rail — project metadata + documents placeholder (B5 slot) */}
        <WorkspaceRail project={project} />
      </div>
    </div>
  )
}

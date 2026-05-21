import { useEffect, useRef, useState, type FormEvent } from 'react'
import { apiPost } from '../lib/api'
import type { Project } from './ProjectCard'

interface NewProjectModalProps {
  onClose: () => void
  onCreated: (project: Project) => void
}

interface CreateProjectResponse {
  id: string
  name: string
  client?: string
  status: string
  aconex_connected?: boolean
  user_id?: string
  created_at: string
  readiness?: unknown
  documents?: unknown
}

export default function NewProjectModal({ onClose, onCreated }: NewProjectModalProps) {
  const [name, setName] = useState('')
  const [client, setClient] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const overlayRef = useRef<HTMLDivElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  // Focus name field on mount
  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === overlayRef.current) onClose()
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return

    setSubmitting(true)
    setError(null)

    try {
      const body: { name: string; client?: string } = { name: name.trim() }
      if (client.trim()) body.client = client.trim()

      const project = await apiPost<CreateProjectResponse>('/v1/projects', body)
      onCreated(project as Project)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'An unexpected error occurred.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="modal-overlay"
      ref={overlayRef}
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div className="modal">
        <div className="modal__header">
          <h2 className="modal__title" id="modal-title">New project</h2>
          <button
            className="modal__close"
            type="button"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div className="form-field">
            <label className="form-label" htmlFor="proj-name">
              Project name
            </label>
            <input
              id="proj-name"
              ref={nameRef}
              className="form-input"
              type="text"
              placeholder="e.g. North Tower Fit-out"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoComplete="off"
              disabled={submitting}
            />
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="proj-client">
              Client
              <span className="form-label--optional">(optional)</span>
            </label>
            <input
              id="proj-client"
              className="form-input"
              type="text"
              placeholder="e.g. Acme Corp"
              value={client}
              onChange={(e) => setClient(e.target.value)}
              autoComplete="off"
              disabled={submitting}
            />
          </div>

          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}

          <div className="form-actions">
            <button
              type="button"
              className="btn btn--ghost"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={submitting || !name.trim()}
            >
              {submitting ? 'Creating…' : 'Create project'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

import { Link } from 'react-router-dom'
import { apiDelete } from '../lib/api'

// readiness shape may vary — render defensively
export interface Project {
  id: string
  name: string
  client?: string
  status: string
  aconex_connected?: boolean
  user_id?: string
  created_at: string
  readiness?: unknown
  documents?: unknown
  origin?: string
  document_count?: number
  is_master_corpus?: boolean
}

interface ProjectCardProps {
  project: Project
  onDelete?: (id: string) => void
}

function statusClass(status: string): string {
  const s = status.toLowerCase()
  if (s === 'active') return 'project-card__status--active'
  if (s === 'draft') return 'project-card__status--draft'
  return 'project-card__status--other'
}

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

function readinessHint(readiness: unknown): string | null {
  if (!readiness) return null
  if (typeof readiness !== 'object') return null
  const r = readiness as Record<string, unknown>
  // Try common keys: score, percent, label, status, ready
  if (typeof r.label === 'string' && r.label) return r.label
  if (typeof r.status === 'string' && r.status) return r.status
  if (typeof r.score === 'number') return `Score: ${r.score}`
  if (typeof r.percent === 'number') return `${r.percent}%`
  if (typeof r.ready === 'boolean') return r.ready ? 'Ready' : 'Not ready'
  // Readiness object exists but we can't distill a hint — just acknowledge it
  return 'Readiness available'
}

function isIncompleteShell(project: Project): boolean {
  return (
    project.origin === 'admin_drive_approved' &&
    !project.is_master_corpus &&
    (project.document_count ?? 0) <= 1
  )
}

export default function ProjectCard({ project, onDelete }: ProjectCardProps) {
  const hint = readinessHint(project.readiness)
  const master = project.is_master_corpus
  const incomplete = isIncompleteShell(project)

  async function handleDelete(e: React.MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    if (!window.confirm(`Delete project "${project.name}"? This cannot be undone.`)) {
      return
    }
    try {
      await apiDelete(`/v1/projects/${project.id}`)
      onDelete?.(project.id)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Delete failed'
      alert(message)
    }
  }

  return (
    <Link
      to={`/projects/${project.id}`}
      className="project-card-link"
      aria-label={`Open project ${project.name}`}
    >
      <div className={`project-card ${master ? 'project-card--master' : ''} ${incomplete ? 'project-card--incomplete' : ''}`}>
        <div className="project-card__top">
          <span className="project-card__id">{project.id}</span>
          <span className={`project-card__status ${statusClass(project.status)}`}>
            {project.status}
          </span>
        </div>

        <div className="project-card__name">
          {project.name}
          {master && (
            <span className="project-card__badge project-card__badge--master">
              Master Corpus
            </span>
          )}
          {incomplete && (
            <span className="project-card__badge project-card__badge--warning">
              Incomplete shell
            </span>
          )}
        </div>

        {project.client && (
          <div className="project-card__client">{project.client}</div>
        )}

        <div className="project-card__footer">
          <span className="project-card__date">
            {formatDate(project.created_at)}
          </span>
          {hint && (
            <span className="project-card__readiness">{hint}</span>
          )}
        </div>

        <button
          type="button"
          className="project-card__delete"
          onClick={handleDelete}
          aria-label={`Delete project ${project.name}`}
        >
          Delete
        </button>
      </div>
    </Link>
  )
}

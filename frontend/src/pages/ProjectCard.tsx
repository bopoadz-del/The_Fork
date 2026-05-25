import { Link } from 'react-router-dom'

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
}

interface ProjectCardProps {
  project: Project
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

export default function ProjectCard({ project }: ProjectCardProps) {
  const hint = readinessHint(project.readiness)

  return (
    <Link
      to={`/projects/${project.id}`}
      className="project-card-link"
      aria-label={`Open project ${project.name}`}
    >
      <div className="project-card">
        <div className="project-card__top">
          <span className="project-card__id">{project.id}</span>
          <span className={`project-card__status ${statusClass(project.status)}`}>
            {project.status}
          </span>
        </div>

        <div className="project-card__name">{project.name}</div>

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
      </div>
    </Link>
  )
}

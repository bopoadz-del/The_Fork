/* ProjectWorkspace page */
import { useParams } from 'react-router-dom'
import AppHeader from '../components/AppHeader'
import './pages.css'

export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>()

  return (
    <div className="page-shell">
      <AppHeader
        breadcrumb={
          <>
            <span className="nav-item">Projects</span>
            <span className="nav-sep">/</span>
            <span className="nav-item nav-item--active mono">{id ?? '—'}</span>
          </>
        }
      />

      <main className="page-main">
        <div className="content-header">
          <h1>Project Workspace</h1>
          <p className="page-description">
            Conversational intelligence and document context for this project.
          </p>
        </div>

        <div className="workspace-meta">
          <div className="meta-row">
            <span className="meta-label text-muted">Project ID</span>
            <span className="meta-value mono">{id ?? 'unknown'}</span>
          </div>
          <div className="meta-row">
            <span className="meta-label text-muted">Route</span>
            <span className="meta-value mono">/projects/{id}</span>
          </div>
          <div className="meta-row">
            <span className="meta-label text-muted">Status</span>
            <span className="meta-value">
              <span className="badge badge--scaffold">scaffold</span>
            </span>
          </div>
        </div>
      </main>
    </div>
  )
}

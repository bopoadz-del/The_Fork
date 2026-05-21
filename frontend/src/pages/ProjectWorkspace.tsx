/* ProjectWorkspace — placeholder page */
import { useParams } from 'react-router-dom'
import './pages.css'

export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>()

  return (
    <div className="page-shell">
      <header className="page-header">
        <div className="page-header__inner">
          <div className="page-header__brand">
            <span className="brand-mark">TF</span>
            <span className="brand-name">The Fork</span>
          </div>
          <nav className="page-header__nav">
            <span className="nav-item">Projects</span>
            <span className="nav-sep">/</span>
            <span className="nav-item nav-item--active mono">{id ?? '—'}</span>
          </nav>
        </div>
      </header>

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
            <span className="meta-value"><span className="badge badge--scaffold">scaffold</span></span>
          </div>
        </div>
      </main>
    </div>
  )
}

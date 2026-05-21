/* Projects page */
import AppHeader from '../components/AppHeader'
import './pages.css'

export default function Projects() {
  return (
    <div className="page-shell">
      <AppHeader
        breadcrumb={
          <span className="nav-item nav-item--active">Projects</span>
        }
      />

      <main className="page-main">
        <div className="content-header">
          <h1>Projects</h1>
          <p className="page-description">
            All active construction projects in your workspace.
          </p>
        </div>

        <div className="placeholder-grid">
          {['PRJ-001', 'PRJ-002', 'PRJ-003'].map((id) => (
            <div className="project-card" key={id}>
              <div className="project-card__id mono">{id}</div>
              <div className="project-card__name">Project Placeholder</div>
              <div className="project-card__meta text-muted">
                Route: /projects/{id.toLowerCase()}
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}

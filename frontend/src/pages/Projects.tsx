/* Projects — placeholder page */
import './pages.css'

export default function Projects() {
  return (
    <div className="page-shell">
      <header className="page-header">
        <div className="page-header__inner">
          <div className="page-header__brand">
            <span className="brand-mark">TF</span>
            <span className="brand-name">The Fork</span>
          </div>
          <nav className="page-header__nav">
            <span className="nav-item nav-item--active">Projects</span>
          </nav>
        </div>
      </header>

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

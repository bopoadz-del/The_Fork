/* Login — placeholder page */
import './pages.css'

export default function Login() {
  return (
    <div className="page-center">
      <div className="auth-card">
        <div className="auth-card__logo">
          <span className="auth-card__mark">TF</span>
        </div>
        <h1 className="auth-card__title">Sign in</h1>
        <p className="auth-card__subtitle">
          Construction Intelligence Platform
        </p>
        <div className="auth-card__meta mono">
          <span className="tag">v0.1 · scaffold</span>
          <span className="tag">route: /login</span>
        </div>
      </div>
    </div>
  )
}

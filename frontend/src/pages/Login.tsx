import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import './pages.css'
import './auth.css'

type Mode = 'signin' | 'register'

export default function Login() {
  const { user, loading, login, register } = useAuth()
  const navigate = useNavigate()

  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Redirect already-authenticated users away from /login
  useEffect(() => {
    if (!loading && user) {
      navigate('/', { replace: true })
    }
  }, [loading, user, navigate])

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    setEmail('')
    setPassword('')
    setDisplayName('')
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)

    try {
      if (mode === 'signin') {
        await login(email, password)
      } else {
        await register(email, password, displayName.trim() || undefined)
      }
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An unexpected error occurred.')
    } finally {
      setSubmitting(false)
    }
  }

  // While bootstrapping, render nothing (ProtectedRoute handles the spinner;
  // here we just avoid a flash of the login form for already-logged-in users).
  if (loading) return null

  return (
    <div className="page-center">
      <div className="auth-card">
        {/* Logo mark */}
        <div className="auth-card__logo">
          <span className="auth-card__mark">TF</span>
        </div>

        <h1 className="auth-card__title">
          {mode === 'signin' ? 'Sign in' : 'Create account'}
        </h1>
        <p className="auth-card__subtitle">Construction Intelligence Platform</p>

        {/* Form */}
        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          {mode === 'register' && (
            <div className="auth-field">
              <label className="auth-label" htmlFor="displayName">
                Display name <span className="auth-label__optional">(optional)</span>
              </label>
              <input
                id="displayName"
                className="auth-input"
                type="text"
                autoComplete="name"
                placeholder="e.g. Alex Chen"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={submitting}
              />
            </div>
          )}

          <div className="auth-field">
            <label className="auth-label" htmlFor="email">Email</label>
            <input
              id="email"
              className="auth-input"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
          </div>

          <div className="auth-field">
            <label className="auth-label" htmlFor="password">Password</label>
            <input
              id="password"
              className="auth-input"
              type="password"
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              placeholder={mode === 'register' ? 'Minimum 8 characters' : '••••••••'}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
          </div>

          {error && (
            <div className="auth-error" role="alert">
              {error}
            </div>
          )}

          <button
            className="auth-submit"
            type="submit"
            disabled={submitting || !email || !password}
          >
            {submitting
              ? (mode === 'signin' ? 'Signing in…' : 'Creating account…')
              : (mode === 'signin' ? 'Sign in' : 'Create account')}
          </button>
        </form>

        {/* Toggle */}
        <div className="auth-toggle">
          {mode === 'signin' ? (
            <>
              Don&apos;t have an account?{' '}
              <button
                className="auth-toggle__link"
                type="button"
                onClick={() => switchMode('register')}
              >
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{' '}
              <button
                className="auth-toggle__link"
                type="button"
                onClick={() => switchMode('signin')}
              >
                Sign in
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

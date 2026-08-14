import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { apiPost } from '../lib/api'
import BrandMark from '../components/BrandMark'
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
  // The verification link lands back here as /?verified=success|invalid.
  // Computed in the initializer rather than an effect: it is derived from the
  // URL that rendered this page, so deriving it during render avoids both a
  // flash of the un-noticed form and a setState-inside-effect.
  const [notice, setNotice] = useState<string | null>(() => {
    const verified = new URLSearchParams(window.location.search).get('verified')
    if (!verified) return null
    return verified === 'success'
      ? 'Email confirmed. You can sign in now.'
      : 'That confirmation link is invalid or has expired. Request a new one below.'
  })

  // Strip the flag so a refresh does not re-announce a stale outcome.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (!params.has('verified')) return
    params.delete('verified')
    const rest = params.toString()
    window.history.replaceState({}, '', window.location.pathname + (rest ? `?${rest}` : ''))
  }, [])

  // Redirect already-authenticated users away from /login
  useEffect(() => {
    if (!loading && user) {
      navigate('/', { replace: true })
    }
  }, [loading, user, navigate])

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    setNotice(null)
    setEmail('')
    setPassword('')
    setDisplayName('')
  }

  async function handleResend() {
    setError(null)
    setNotice(null)
    setSubmitting(true)
    try {
      await apiPost('/v1/users/resend-verification', { email })
      // The endpoint answers 202 whether or not the address exists, so this
      // wording must not imply the account was found -- that would rebuild
      // the account oracle the endpoint deliberately avoids.
      setNotice(`If ${email} needs confirming, a new link is on its way.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not request a new link.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setNotice(null)
    setSubmitting(true)

    try {
      if (mode === 'signin') {
        await login(email, password)
      } else {
        const result = await register(email, password, displayName.trim() || undefined)
        if (result.verificationRequired) {
          // The account exists but has no session yet. Navigating to the app
          // would bounce off ProtectedRoute; tell them to check their mail.
          setNotice(
            result.verificationEmailSent
              ? `Account created. We sent a confirmation link to ${email} - follow it to finish signing in.`
              : `Account created, but the confirmation email could not be sent. Use "Resend confirmation email" below.`,
          )
          setMode('signin')
          return
        }
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
        {/* Logo mark — shovel from the Quarry standalone design */}
        <div className="auth-card__logo">
          <span className="auth-card__mark">
            <BrandMark size={26} />
          </span>
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

          {notice && (
            <div className="auth-notice" role="status">
              {notice}
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

        {mode === 'signin' && (
          <div className="auth-toggle">
            <button
              className="auth-toggle__link"
              type="button"
              onClick={handleResend}
              disabled={submitting || !email}
            >
              Resend confirmation email
            </button>
          </div>
        )}

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

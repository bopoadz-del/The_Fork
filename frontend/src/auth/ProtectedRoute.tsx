import { Navigate } from 'react-router-dom'
import { useAuth } from './AuthContext'

interface ProtectedRouteProps {
  children: React.ReactNode
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg)',
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            color: 'var(--text-muted)',
            letterSpacing: '0.05em',
          }}
        >
          …
        </span>
      </div>
    )
  }

  if (!user) {
    // Carry the query string across the redirect. The email-verification link
    // lands on /?verified=success for a signed-OUT user, so bouncing to a bare
    // /login would swallow the one piece of feedback that confirms the click
    // worked. Any future landing flag inherits this for free.
    return (
      <Navigate
        to={{ pathname: '/login', search: window.location.search }}
        replace
      />
    )
  }

  return <>{children}</>
}

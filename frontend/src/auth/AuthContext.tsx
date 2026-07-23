import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, apiGet, apiPost } from '../lib/api'
import { clearToken, getToken, setToken } from '../lib/token'

// ─── Types ────────────────────────────────────────────────────────────────────
// /me returns user_id; /login returns user.id — normalise to a single shape.
export interface AuthUser {
  id: string
  email: string
  role: string
  display_name?: string
}

interface LoginResponse {
  token: string
  token_type: string
  user: { id: string; email: string; role: string }
}

interface RegisterResponse {
  id: string
  email: string
  role: string
  display_name?: string
  created_at: string
}

interface MeResponse {
  user_id: string
  email: string
  role: string
  display_name?: string
}

// ─── Context shape ────────────────────────────────────────────────────────────
interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName?: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

// ─── Provider ─────────────────────────────────────────────────────────────────
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  // Guard against StrictMode double-invocation
  const bootstrapped = useRef(false)

  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true

    const token = getToken()
    if (!token) {
      setLoading(false)
      return
    }

    apiGet<MeResponse>('/v1/users/me')
      .then((me) => {
        setUser({
          id: me.user_id,
          email: me.email,
          role: me.role,
          display_name: me.display_name,
        })
      })
      .catch((err: unknown) => {
        // Only discard the token on a real auth failure (401).
        // Network errors (TypeError, no status) leave the token intact so a
        // transient blip during bootstrap does not silently log the user out.
        if (err instanceof ApiError && err.status === 401) {
          clearToken()
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const login = async (email: string, password: string): Promise<void> => {
    const data = await apiPost<LoginResponse>('/v1/users/login', { email, password })
    setToken(data.token)
    setUser({
      id: data.user.id,
      email: data.user.email,
      role: data.user.role,
    })
  }

  const register = async (
    email: string,
    password: string,
    displayName?: string,
  ): Promise<void> => {
    await apiPost<RegisterResponse>('/v1/users/register', {
      email,
      password,
      ...(displayName ? { display_name: displayName } : {}),
    })
    // Auto-login after successful registration
    await login(email, password)
  }

  const logout = (): void => {
    clearToken()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// ─── Hook ─────────────────────────────────────────────────────────────────────
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}

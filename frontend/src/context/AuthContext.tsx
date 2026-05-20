/**
 * AuthContext — in-memory JWT token storage (D-22).
 *
 * token is held in React state only — no localStorage, no sessionStorage.
 * Refreshing the browser clears the token → automatic redirect to /login.
 */
import { createContext, useContext, useState, type ReactNode } from 'react'

interface AuthContextValue {
  token: string | null
  setToken: (t: string) => void
  clearToken: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null)

  const setToken = (t: string) => setTokenState(t)
  const clearToken = () => setTokenState(null)

  return (
    <AuthContext.Provider value={{ token, setToken, clearToken }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

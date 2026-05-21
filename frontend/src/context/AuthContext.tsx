/**
 * AuthContext — JWT token stored in sessionStorage (survives page reload,
 * cleared on tab close). Logout and JWT expiry clear sessionStorage.
 */
import { createContext, useContext, useState, type ReactNode } from 'react'

const SESSION_KEY = 'access_token'

interface AuthContextValue {
  token: string | null
  setToken: (t: string) => void
  clearToken: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(
    () => sessionStorage.getItem(SESSION_KEY)
  )

  const setToken = (t: string) => {
    sessionStorage.setItem(SESSION_KEY, t)
    setTokenState(t)
  }
  const clearToken = () => {
    sessionStorage.removeItem(SESSION_KEY)
    setTokenState(null)
  }

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

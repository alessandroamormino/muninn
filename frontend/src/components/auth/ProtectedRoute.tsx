/**
 * ProtectedRoute — redirects to /login when token is null (D-21).
 * Wraps all authenticated routes in App.tsx.
 */
import { Navigate } from 'react-router'
import { useAuth } from '../../context/AuthContext'
import type { ReactNode } from 'react'

interface ProtectedRouteProps {
  children: ReactNode
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { token } = useAuth()
  if (token === null) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

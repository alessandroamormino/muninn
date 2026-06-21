import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router'
import { toast } from 'sonner'
import { useNavigate } from 'react-router'
import Shell from './components/layout/Shell'
import SearchPage from './pages/SearchPage'
import EntitiesPage from './pages/EntitiesPage'
import LogsPage from './pages/LogsPage'
import GraphPage from './pages/GraphPage'
import LoginPage from './pages/LoginPage'
import ProtectedRoute from './components/auth/ProtectedRoute'
import { AuthProvider, useAuth } from './context/AuthContext'
import { Toaster } from './components/ui/sonner'

// Phase 27 — the monitoring Settings page is code-split into its own chunk. This is
// the ONE deliberate, scoped exception to this file's all-eager-import convention —
// no other route should be made lazy. (Originally added to isolate the recharts
// bundle; the sparkline was later replaced by a dependency-free PixelGauge, but the
// lazy boundary is kept so monitoring stays off the main bundle's critical path.)
const SettingsPage = lazy(() => import('./pages/SettingsPage'))

/**
 * Inner app — has access to AuthContext via useAuth().
 * Provides on401 callback to createApiClient consumers via context or prop drilling.
 * For now, on401 is exposed on window for ease of consumption from TanStack Query hooks.
 */
function AppRoutes() {
  const { clearToken } = useAuth()
  const navigate = useNavigate()

  // Expose on401 handler so TanStack Query hooks and fetchJson callers can call it.
  // This avoids prop-drilling through every page component.
  // Usage: import { getOn401 } from './App' or access window.__on401.
  // TODO(WR-04): replace window.__on401 with React Context to eliminate global side-effect
  ;(window as unknown as { __on401?: () => void }).__on401 = () => {
    clearToken()
    navigate('/login', { replace: true })
    toast.error('Sessione scaduta. Accedi di nuovo.')
  }

  return (
    <>
      <Routes>
        {/* Public: login page — no Shell, no ProtectedRoute */}
        <Route path="/login" element={<LoginPage />} />

        {/* Protected: all other routes require valid token */}
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <Shell>
                <Routes>
                  <Route path="/" element={<Navigate to="/search" replace />} />
                  <Route path="/search" element={<SearchPage />} />
                  <Route path="/entities" element={<EntitiesPage />} />
                  <Route
                    path="/settings"
                    element={
                      <Suspense fallback={null}>
                        <SettingsPage />
                      </Suspense>
                    }
                  />
                  <Route path="/logs" element={<LogsPage />} />
                  <Route path="/graph" element={<GraphPage />} />
                </Routes>
              </Shell>
            </ProtectedRoute>
          }
        />
      </Routes>
      <Toaster richColors position="top-right" />
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}

import { Routes, Route, Navigate } from 'react-router'
import Shell from './components/layout/Shell'
import SearchPage from './pages/SearchPage'
import SettingsPage from './pages/SettingsPage'
import LogsPage from './pages/LogsPage'
import GraphPage from './pages/GraphPage'
import { Toaster } from './components/ui/sonner'

export default function App() {
  return (
    <>
      <Shell>
        <Routes>
          <Route path="/" element={<Navigate to="/search" replace />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/graph" element={<GraphPage />} />
        </Routes>
      </Shell>
      <Toaster richColors position="top-right" />
    </>
  )
}

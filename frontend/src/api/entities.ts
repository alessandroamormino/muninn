import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

// Phase 26 — entity load/unload management.
// unload = snapshot Qdrant collection + drop_index (frees RAM, non-destructive).
// load   = restore from the registered snapshot (no re-embedding).
// Both run as backend BackgroundTasks; progress is polled via useUnloadProgress.

export function useUnloadEntity() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (name: string) =>
      fetchJson<{ status: string }>(
        `/api/collections/${encodeURIComponent(name)}/unload`,
        { method: 'POST' }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['collections'] })
      qc.invalidateQueries({ queryKey: ['unload-progress'] })
    },
  })
}

export function useLoadEntity() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (name: string) =>
      fetchJson<{ status: string }>(
        `/api/collections/${encodeURIComponent(name)}/load`,
        { method: 'POST' }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['collections'] })
      qc.invalidateQueries({ queryKey: ['unload-progress'] })
    },
  })
}

export interface UnloadProgress {
  entity?: string
  // 'snapshotting' | 'deleting' | 'restoring' | 'done' | 'failed'
  phase?: string
  error?: string
}

export function useUnloadProgress() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['unload-progress'],
    // Backend exposes the in-flight unload/load progress dict keyed by entity name;
    // any entity's load-status endpoint returns the same shared app.state.unload_progress.
    queryFn: () => fetchJson<UnloadProgress>('/api/collections/_/load-status'),
    refetchInterval: 1500,
  })
}

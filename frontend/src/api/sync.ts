import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export function useTriggerSync() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (collection: string) =>
      fetchJson<{ status: string; collection: string }>(
        `/api/sync/full/by-collection?collection=${encodeURIComponent(collection)}`,
        { method: 'POST' }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['logs'] })
    },
  })
}

export function useIncrementalSync() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (collection: string) =>
      fetchJson<{ status: string; collection: string }>(
        `/api/sync/by-collection?collection=${encodeURIComponent(collection)}`,
        { method: 'POST' }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['logs'] })
      qc.invalidateQueries({ queryKey: ['syncStatus'] })
    },
  })
}

export interface SyncStatusResponse {
  status: string
  last_run: {
    status: string
    took_ms: number
    started_at: string
    type: string
    quantization_warning?: string
    [key: string]: unknown
  } | null
}

export function useSyncStatus() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['syncStatus'],
    queryFn: () => fetchJson<SyncStatusResponse>('/api/sync/status'),
    staleTime: 10_000,
  })
}

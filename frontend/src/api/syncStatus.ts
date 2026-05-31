import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface SyncProgress {
  phase: 'fetching' | 'embedding' | 'upserting'
  total: number
  done: number
  percent: number
  elapsed_seconds: number
  eta_seconds: number | null
}

export interface SyncStatusResponse {
  status: 'running' | 'completed' | 'failed' | null
  collection?: string
  progress?: SyncProgress
  last_run?: {
    collection?: string
    status?: string
    [key: string]: unknown
  }
}

export function useSyncStatus() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  const query = useQuery({
    queryKey: ['sync-status'],
    queryFn: () => fetchJson<SyncStatusResponse>('/api/sync/status'),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 2_000 : 10_000,
    staleTime: 0,
  })

  return query
}

/** Returns the sync state for a specific collection name. */
export function useCollectionSyncState(collection: string): 'running' | 'completed' | 'failed' | null {
  const { data } = useSyncStatus()
  if (!data) return null
  if (data.status === 'running' && data.collection === collection) return 'running'
  if ((data.status === 'completed' || data.status === 'failed') && data.last_run?.collection === collection)
    return data.status
  return null
}

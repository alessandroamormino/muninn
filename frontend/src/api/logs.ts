import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface LogRun {
  id: number
  started_at: string
  finished_at: string
  type: string                // 'full' | 'incremental' | 'scheduled' | 'upload' | etc.
  status: 'completed' | 'failed' | 'skipped' | string
  took_ms: number
  model: string
  source_type: string
  collection: string
  inserted: number
  updated: number
  skipped_records: number
  errors: number
  error_message: string | null
  reason: string | null
}

export function useLogs(collection: string | null, limit = 50) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  const qs = new URLSearchParams()
  qs.set('limit', String(limit))
  if (collection) qs.set('collection', collection)
  return useQuery({
    queryKey: ['logs', collection, limit],
    queryFn: () => fetchJson<LogRun[]>(`/api/logs/sync?${qs.toString()}`),
    enabled: !!collection,
    staleTime: 0,  // always fresh on manual refresh
  })
}

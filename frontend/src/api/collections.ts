import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface CollectionItem {
  name: string
  source_type: string
  is_global?: boolean
  // Phase 26 — per-entity load state; optional for backward compat with cached responses.
  status?: 'active' | 'unloaded'
}

export function useCollections() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['collections'],
    queryFn: () => fetchJson<{ collections: CollectionItem[] }>('/api/collections'),
    staleTime: 10_000,
  })
}

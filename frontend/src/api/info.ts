import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface InfoResponse {
  embedding_model: string
  embedding_type: string
  collection: string
  weaviate_url: string
  sync_mode: string
  sync_schedule: string
  total_objects: number | null
  vector_store_engine: string
  search_mode: string
}

export function useInfo() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['info'],
    queryFn: () => fetchJson<InfoResponse>('/api/info'),
    staleTime: 60_000,
  })
}

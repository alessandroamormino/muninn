import { useQuery } from '@tanstack/react-query'
import { fetchJson } from './fetchJson'

export interface InfoResponse {
  embedding_model: string
  embedding_type: string
  collection: string
  weaviate_url: string
  sync_mode: string
  sync_schedule: string
  total_objects: number | null
}

export function useInfo() {
  return useQuery({
    queryKey: ['info'],
    queryFn: () => fetchJson<InfoResponse>('/api/info'),
    staleTime: 60_000,
  })
}

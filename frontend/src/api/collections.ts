import { useQuery } from '@tanstack/react-query'
import { fetchJson } from './fetchJson'

export function useCollections() {
  return useQuery({
    queryKey: ['collections'],
    queryFn: () => fetchJson<{ collections: string[] }>('/api/collections'),
    staleTime: 60_000,
  })
}

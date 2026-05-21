import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export function useCollections() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['collections'],
    queryFn: () => fetchJson<{ collections: string[] }>('/api/collections'),
    staleTime: 60_000,
  })
}

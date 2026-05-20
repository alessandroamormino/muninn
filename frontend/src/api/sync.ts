import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export function useTriggerSync() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as Record<string, unknown>)['__on401']?.()
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

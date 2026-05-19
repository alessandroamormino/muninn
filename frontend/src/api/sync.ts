import { useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchJson } from './fetchJson'

export function useTriggerSync() {
  const qc = useQueryClient()
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

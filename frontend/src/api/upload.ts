import { useMutation } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface SuggestedConfig {
  file_name: string
  collection: string
  id_field: string
  text_fields: string[]
  metadata_fields: string[]
  output_fields: string[]
  delimiter: string
}

export function useUpload() {
  const { token } = useAuth()
  const on401 = () => (window as Record<string, unknown>)['__on401']?.()

  return useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const res = await fetch('/api/upload', { method: 'POST', body: fd, headers })
      if (res.status === 401) {
        on401()
        throw new Error(`HTTP 401: ${await res.text().catch(() => '')}`)
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`)
      return res.json() as Promise<{
        suggested_config: SuggestedConfig
        reasoning?: object
        _warning?: string
      }>
    },
  })
}

export function useConfirmUpload() {
  const { token } = useAuth()
  const on401 = () => (window as Record<string, unknown>)['__on401']?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (cfg: SuggestedConfig) =>
      fetchJson<{ status: string; collection: string; config_path: string }>(
        '/api/upload/confirm',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cfg),
        }
      ),
  })
}

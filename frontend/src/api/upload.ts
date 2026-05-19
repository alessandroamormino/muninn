import { useMutation } from '@tanstack/react-query'
import { fetchJson } from './fetchJson'

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
  return useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch('/api/upload', { method: 'POST', body: fd })
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

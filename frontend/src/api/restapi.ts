import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface RestApiPayload {
  collection: string
  url: string
  id_field: string
  text_fields: string[]
  metadata_fields: string[]
  output_fields: string[]
  auth_type: 'none' | 'bearer' | 'api_key_header' | 'api_key_param' | 'basic'
  auth_env_var?: string | null
  auth_header_name?: string | null
  auth_param_name?: string | null
  pagination_type: 'none' | 'offset' | 'page' | 'cursor'
  pagination_next_key?: string | null
  json_key?: string | null
}

export function useCreateRestApiEntity() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (payload: RestApiPayload) =>
      fetchJson<{ status: string; collection: string; config_path: string }>(
        '/api/upload/restapi',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['collections'] })
    },
  })
}

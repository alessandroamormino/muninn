import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'
import type { InfoResponse } from './info'

export type { InfoResponse }

// ── useGetConfig ──────────────────────────────────────────────────────────────

export function useGetConfig(collection: string | null) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['config', collection],
    queryFn: () =>
      fetchJson<{ yaml: string }>(`/api/config/${encodeURIComponent(collection!)}`),
    enabled: !!collection,
    staleTime: 30_000,
  })
}

// ── useSaveConfig ─────────────────────────────────────────────────────────────

export function useSaveConfig(collection: string) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (yamlStr: string) =>
      fetchJson<{ ok: boolean }>(`/api/config/${encodeURIComponent(collection)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml: yamlStr }),
      }),
  })
}

// ── useEntityInfo ─────────────────────────────────────────────────────────────

export function useEntityInfo(collection: string | null) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['info', collection],
    queryFn: () =>
      fetchJson<InfoResponse>(`/api/info?collection=${encodeURIComponent(collection!)}`),
    enabled: !!collection,
    staleTime: 10_000,
  })
}

// ── useSuggestConfigFromFields ─────────────────────────────────────────────────

export interface SuggestFieldsResponse {
  suggested_config: {
    id_field: string
    text_fields: string[]
    metadata_fields: string[]
    output_fields: string[]
  }
  reasoning: Record<string, unknown>
}

export function useSuggestConfigFromFields() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (fields: string[]) =>
      fetchJson<SuggestFieldsResponse>('/api/setup/suggest-config-from-fields', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields }),
      }),
  })
}

// ── useCreateConfig ────────────────────────────────────────────────────────────
// Used by MySQLWizard (Plan 14.1-03) to create a new entity config

export interface CreateConfigPayload {
  collection: string
  source_type: 'mysql'
  port: number
  host_env_var: string
  db_env_var: string
  user_env_var: string
  password_env_var: string
  from_table: string
  fields: string[]
  id_field: string
  text_fields: string[]
  metadata_fields: string[]
  output_fields: string[]
  search_mode: string
}

export function useCreateConfig() {
  const qc = useQueryClient()
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useMutation({
    mutationFn: (body: CreateConfigPayload) =>
      fetchJson<{ collection: string }>('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['collections'] })
    },
  })
}

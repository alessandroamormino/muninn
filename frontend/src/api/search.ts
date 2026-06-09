import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface SearchResult {
  _score: number
  [key: string]: unknown
}

export interface SearchResponse {
  query: string
  took_ms: number
  results: SearchResult[]
}

export interface SearchParams {
  q: string
  collection: string | null
  filter?: string | null
  min_score?: number | null
  limit?: number
  search_mode?: string
  match_mode?: 'and' | 'or' | null
}

export function useSearch(params: SearchParams) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  const enabled = !!params.q && !!params.collection
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.collection) qs.set('collection', params.collection)
  if (params.filter) qs.set('filter', params.filter)
  if (params.min_score != null) qs.set('min_score', String(params.min_score))
  if (params.limit) qs.set('limit', String(params.limit))
  if (params.search_mode) qs.set('search_mode', params.search_mode)
  if (params.match_mode != null) qs.set('match_mode', params.match_mode)
  return useQuery({
    queryKey: ['search', params.q, params.collection, params.filter, params.min_score, params.limit, params.search_mode, params.match_mode],
    queryFn: () => fetchJson<SearchResponse>(`/api/search?${qs.toString()}`),
    enabled,
    staleTime: 10_000,
    retry: 0,
  })
}

export function useSuggest(q: string, collection: string | null) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['suggest', q, collection],
    queryFn: () =>
      fetchJson<string[]>(
        `/api/search/suggest?q=${encodeURIComponent(q)}&collection=${encodeURIComponent(collection!)}&limit=5`
      ),
    enabled: q.length >= 2 && !!collection,
    staleTime: 5_000,
    retry: 0,
  })
}

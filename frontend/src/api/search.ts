import { useQuery } from '@tanstack/react-query'
import { fetchJson } from './fetchJson'

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
}

export function useSearch(params: SearchParams) {
  const enabled = !!params.q && !!params.collection
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.collection) qs.set('collection', params.collection)
  if (params.filter) qs.set('filter', params.filter)
  if (params.min_score != null) qs.set('min_score', String(params.min_score))
  if (params.limit) qs.set('limit', String(params.limit))
  return useQuery({
    queryKey: ['search', params.q, params.collection, params.filter, params.min_score, params.limit],
    queryFn: () => fetchJson<SearchResponse>(`/api/search?${qs.toString()}`),
    enabled,
    staleTime: 10_000,
    retry: 0,
  })
}

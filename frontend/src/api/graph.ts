import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

export interface GraphNode {
  id: string
  x: number
  y: number
  cluster: number
  radius: number
  props: Record<string, unknown>
}
export interface GraphEdge {
  source: string
  target: string
}
export interface GraphCluster {
  id: number
  name: string
  size: number
}
export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  clusters: GraphCluster[]
  filter_fields: string[]
}

export function useGraph(collection: string | null) {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['graph', collection],
    queryFn: () => fetchJson<GraphResponse>(`/api/graph/${encodeURIComponent(collection!)}`),
    enabled: false,     // manual trigger only — user clicks Load Graph
    staleTime: 0,
    retry: 0,
    gcTime: 0,          // do not retain large graphs in memory
  })
}

import { useQuery } from '@tanstack/react-query'
import { fetchJson } from './fetchJson'

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
}

export function useGraph(collection: string | null) {
  return useQuery({
    queryKey: ['graph', collection],
    queryFn: () => fetchJson<GraphResponse>(`/api/graph/${encodeURIComponent(collection!)}`),
    enabled: false,     // manual trigger only — user clicks Load Graph
    staleTime: 0,
    retry: 0,
    gcTime: 0,          // do not retain large graphs in memory
  })
}

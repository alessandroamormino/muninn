import type { GraphCluster } from '@/api/graph'
import { Badge } from '@/components/ui/badge'

export const CLUSTER_PALETTE = [
  '#3b82f6', // blue
  '#10b981', // emerald
  '#f59e0b', // amber
  '#ef4444', // red
  '#8b5cf6', // violet
  '#06b6d4', // cyan
  '#f97316', // orange
  '#84cc16', // lime
  '#ec4899', // pink
  '#14b8a6', // teal
  '#a855f7', // purple
  '#fb923c', // orange-light
  '#22c55e', // green
  '#6366f1', // indigo
  '#e11d48', // rose
  '#0284c7', // sky
  '#d97706', // yellow
  '#16a34a', // green-dark
  '#7c3aed', // violet-dark
  '#c2410c', // orange-dark
]

export function colorForCluster(cluster: number): string {
  if (cluster < 0) return '#94a3b8' // slate-400 = noise
  return CLUSTER_PALETTE[cluster % CLUSTER_PALETTE.length]
}

interface Props {
  clusters: GraphCluster[]
  onClusterClick?: (clusterId: number) => void
  activeClusterId?: number | null
}

export default function ClusterLegend({ clusters, onClusterClick, activeClusterId }: Props) {
  if (clusters.length === 0) return null
  return (
    <div className="absolute top-2 bottom-2 left-2 z-10 bg-card/95 backdrop-blur rounded-md border p-3 max-w-xs flex flex-col">
      <div className="text-xs font-medium mb-1 text-muted-foreground flex-shrink-0">
        Clusters
        <span className="ml-1 text-[10px] opacity-60">(hover # for info)</span>
      </div>
      <ul className="flex-1 min-h-0 overflow-y-auto space-y-1 pr-1 mb-2">
        {clusters.map((c) => (
          <li
            key={c.id}
            className={`flex items-center gap-2 text-xs rounded px-1 -mx-1 py-0.5 transition-colors
              ${onClusterClick ? 'cursor-pointer' : ''}
              ${activeClusterId === c.id ? 'bg-accent font-medium' : onClusterClick ? 'hover:bg-accent/50' : ''}`}
            onClick={() => onClusterClick?.(c.id)}
          >
            <span
              className="inline-block w-3 h-3 rounded-full flex-shrink-0"
              style={{ background: colorForCluster(c.id) }}
              aria-hidden
            />
            <span className="flex-1 truncate" title={c.name}>{c.name}</span>
            <Badge
              variant="secondary"
              className="text-xs cursor-default"
              title={`${c.size} records in this semantic cluster`}
            >
              {c.size}
            </Badge>
          </li>
        ))}
      </ul>
      <div className="pt-2 border-t text-xs text-muted-foreground flex-shrink-0">
        Lines = semantic similarity
        <br/>(K-nearest neighbors in vector space)
      </div>
    </div>
  )
}

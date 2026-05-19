import { useState } from 'react'
import type { GraphNode } from '@/api/graph'
import { Badge } from '@/components/ui/badge'
import { colorForCluster } from './ClusterLegend'

interface Props {
  node: GraphNode | null
  onClose: () => void
  clusterName?: string
  clusterSize?: number
  neighbors?: GraphNode[]
  onHighlight?: (field: string, value: string) => void
  activeFilter?: { field: string; value: string } | null
}

export default function NodeSidebar({ node, onClose, clusterName, clusterSize, neighbors, onHighlight, activeFilter }: Props) {
  const [uuidExpanded, setUuidExpanded] = useState(false)

  if (!node) return null

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b flex-shrink-0">
        <span
          className="inline-block w-3 h-3 rounded-full flex-shrink-0"
          style={{ background: colorForCluster(node.cluster) }}
          aria-hidden
        />
        <span className="flex-1 truncate font-semibold text-sm">
          {clusterName ?? `Cluster ${node.cluster}`}
          {clusterSize != null && (
            <span className="ml-1 font-normal text-muted-foreground text-xs">
              — {clusterSize} nodes
            </span>
          )}
        </span>
        <button
          onClick={onClose}
          className="flex-shrink-0 text-muted-foreground hover:text-foreground transition-colors rounded p-0.5 text-base leading-none"
          aria-label="Close"
        >
          ✕
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {/* Semantic info chip */}
        <div className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          Connected via semantic similarity (K-nearest neighbors)
        </div>

        {/* Nearest neighbors */}
        {neighbors && neighbors.length > 0 && (
          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">
              Nearest neighbors ({neighbors.length})
            </div>
            <ul className="space-y-1">
              {neighbors.map(nb => (
                <li key={nb.id} className="flex items-center gap-2 text-sm">
                  <span
                    className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                    style={{ background: colorForCluster(nb.cluster) }}
                    aria-hidden
                  />
                  <span className="truncate">{nodeLabel(nb)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Field grid */}
        <dl className="grid grid-cols-[minmax(80px,120px)_1fr] gap-x-3 gap-y-2 text-sm">
          {Object.entries(node.props)
            .filter(([, v]) => {
              if (!v || typeof v !== 'string') return true
              return !/^[0-9a-f-]{36}$/i.test(v)
            })
            .map(([k, v]) => (
              <div className="contents" key={k}>
                <dt className="text-muted-foreground text-xs uppercase tracking-wider truncate" title={k}>{k}</dt>
                <dd className="break-words text-sm">{formatValue(v)}</dd>
              </div>
            ))}
        </dl>

        {/* Degree badge */}
        <div>
          <Badge variant="outline">degree-radius {node.radius}</Badge>
        </div>

        {/* Highlight by field chips */}
        {onHighlight && (
          <div>
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
              Highlight by field
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(node.props)
                .filter(([, v]) =>
                  typeof v === 'string' && v &&
                  (v as string).length < 50 &&
                  !/^[0-9a-f-]{36}$/i.test(v as string)
                )
                .slice(0, 8)
                .map(([field, value]) => {
                  const isActive = activeFilter?.field === field && activeFilter?.value === String(value)
                  return (
                    <button
                      key={field}
                      onClick={() => onHighlight(field, String(value))}
                      className={`text-xs px-2 py-1 rounded-full border transition-colors ${
                        isActive
                          ? 'bg-primary text-primary-foreground border-primary'
                          : 'bg-muted hover:bg-accent border-transparent'
                      }`}
                    >
                      {field}: {String(value)}
                    </button>
                  )
                })}
            </div>
          </div>
        )}

        {/* UUID (collapsed) */}
        <div className="pt-2 border-t">
          <button
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setUuidExpanded(v => !v)}
          >
            {uuidExpanded ? 'Hide ID' : 'Show ID'}
          </button>
          {uuidExpanded && (
            <div className="mt-1 font-mono text-xs text-muted-foreground break-all">{node.id}</div>
          )}
        </div>
      </div>
    </div>
  )
}

/** Returns the first non-UUID, non-numeric, non-empty string prop value as a display label */
function nodeLabel(node: GraphNode): string {
  for (const val of Object.values(node.props)) {
    if (!val || typeof val !== 'string') continue
    if (val.length < 2 || val.length > 100) continue
    if (/^[0-9a-f-]{36}$/i.test(val)) continue
    if (/^\d+$/.test(val)) continue
    return val
  }
  return node.id
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

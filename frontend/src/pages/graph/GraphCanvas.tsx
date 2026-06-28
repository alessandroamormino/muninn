import { useTranslation } from 'react-i18next'
import { useState, useMemo, useEffect, useRef } from 'react'
import type { GraphResponse, GraphNode } from '@/api/graph'
import { useVisNetwork } from './useVisNetwork'
import NodeSidebar from './NodeSidebar'
import ClusterLegend from './ClusterLegend'
import { Input } from '@/components/ui/input'

function computeClusterNames(
  nodes: Array<{ cluster: number; props: Record<string, unknown> }>
): Map<number, string> {
  const groups = new Map<number, typeof nodes>()
  nodes.forEach(n => {
    if (!groups.has(n.cluster)) groups.set(n.cluster, [])
    groups.get(n.cluster)!.push(n)
  })
  const names = new Map<number, string>()
  groups.forEach((clusterNodes, clusterId) => {
    if (clusterId < 0) { names.set(clusterId, 'Noise'); return }
    const counts: Record<string, Record<string, number>> = {}
    clusterNodes.forEach(n => {
      Object.entries(n.props).forEach(([key, val]) => {
        if (!val || typeof val !== 'string') return
        if (val.length < 2 || val.length > 50) return
        if (/^[0-9a-f-]{36}$/i.test(val)) return
        if (/^\d+$/.test(val)) return
        if (!counts[key]) counts[key] = {}
        counts[key][val] = (counts[key][val] ?? 0) + 1
      })
    })
    let bestName = ''
    let bestCount = 0
    Object.values(counts).forEach(valCounts => {
      Object.entries(valCounts).forEach(([val, cnt]) => {
        if (cnt / clusterNodes.length >= 0.4 && cnt > bestCount) {
          bestCount = cnt; bestName = val
        }
      })
    })
    names.set(clusterId, bestName || `Cluster ${clusterId}`)
  })
  return names
}

interface Props {
  data: GraphResponse
  resetZoomRef?: React.MutableRefObject<(() => void) | null>
}

export default function GraphCanvas({ data, resetZoomRef }: Props) {
  const { t } = useTranslation()
  const [searchTerm, setSearchTerm] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [activeClusterFilter, setActiveClusterFilter] = useState<number | null>(null)
  const [activeFieldFilter, setActiveFieldFilter] = useState<{ field: string; value: string } | null>(null)

  const highlightedIds = useMemo(() => {
    if (!searchTerm || searchTerm.trim().length < 2) return null
    const term = searchTerm.toLowerCase()
    const ids = new Set<string>()
    data.nodes.forEach(n => {
      const matches = Object.values(n.props).some(v =>
        typeof v === 'string' && v.toLowerCase().includes(term)
      )
      if (matches) ids.add(n.id)
    })
    return ids
  }, [searchTerm, data.nodes])

  const filterFn = useMemo<((node: GraphNode) => boolean) | null>(() => {
    if (activeClusterFilter !== null) {
      return (node) => node.cluster === activeClusterFilter
    }
    if (activeFieldFilter) {
      return (node) => {
        const val = node.props[activeFieldFilter.field]
        return typeof val === 'string' && val === activeFieldFilter.value
      }
    }
    return null
  }, [activeClusterFilter, activeFieldFilter])

  const handleHighlight = (field: string, value: string) => {
    setActiveClusterFilter(null)
    setActiveFieldFilter(prev =>
      prev?.field === field && prev?.value === value ? null : { field, value }
    )
  }

  const { resetZoom, zoomToCluster, zoomToNode } = useVisNetwork(
    containerRef,
    data,
    setSelected,
    highlightedIds,
    filterFn,
  )

  // Expose resetZoom to parent via ref
  useEffect(() => {
    if (resetZoomRef) resetZoomRef.current = resetZoom
  }, [resetZoom, resetZoomRef])

  const handleClusterClick = (clusterId: number) => {
    setActiveFieldFilter(null)
    setActiveClusterFilter(prev => prev === clusterId ? null : clusterId)
    zoomToCluster(clusterId, data.nodes)
  }

  useEffect(() => {
    if (!highlightedIds || highlightedIds.size === 0) return
    const firstId = [...highlightedIds][0]
    const firstNode = data.nodes.find(n => n.id === firstId)
    if (firstNode) {
      const t = setTimeout(() => zoomToNode(firstId), 100)
      return () => clearTimeout(t)
    }
  }, [highlightedIds]) // eslint-disable-line react-hooks/exhaustive-deps

  const adjacency = useMemo(() => {
    const adj = new Map<string, string[]>()
    data.edges.forEach(e => {
      if (!adj.has(e.source)) adj.set(e.source, [])
      if (!adj.has(e.target)) adj.set(e.target, [])
      adj.get(e.source)!.push(e.target)
      adj.get(e.target)!.push(e.source)
    })
    return adj
  }, [data.edges])

  const clusterNames = useMemo(() => computeClusterNames(data.nodes), [data.nodes])

  // Filterable fields come from config (graph.filter_fields) — no auto-detection
  const filterFields = useMemo(() => {
    if (!data.filter_fields?.length) return []
    return data.filter_fields
      .map(field => {
        const values = new Set<string>()
        data.nodes.forEach(n => {
          const val = n.props[field]
          if (typeof val === 'string' && val.length <= 60) values.add(val)
        })
        return { field, values: [...values].sort() }
      })
      .filter(({ values }) => values.length >= 2)
  }, [data.filter_fields, data.nodes])

  const selectedNeighbors = useMemo(() => {
    if (!selected) return []
    const neighborIds = adjacency.get(selected.id) ?? []
    return neighborIds
      .slice(0, 5)
      .map(id => data.nodes.find(n => n.id === id))
      .filter(Boolean) as GraphNode[]
  }, [selected, adjacency, data.nodes])

  const selectedClusterName = selected
    ? (clusterNames.get(selected.cluster) ?? `Cluster ${selected.cluster}`)
    : undefined
  const selectedClusterSize = selected
    ? data.nodes.filter(n => n.cluster === selected.cluster).length
    : undefined

  const enrichedClusters = useMemo(() =>
    data.clusters.map(c => ({
      ...c,
      name: clusterNames.get(c.id) ?? c.name,
    })),
    [data.clusters, clusterNames]
  )

  return (
    <div className="h-full w-full flex flex-col">

      {/* Filter bar — above the graph */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b bg-card flex-shrink-0 overflow-x-auto">
        <Input
          placeholder={t('graphDetail.searchRecords')}
          value={searchTerm}
          onChange={e => setSearchTerm(e.target.value)}
          className="h-6 text-xs w-40 flex-shrink-0"
        />
        {filterFields.length > 0 && (
          <>
          <span className="text-xs text-muted-foreground flex-shrink-0">{t('graphDetail.filtersLabel')}</span>
          {filterFields.map(({ field, values }) => (
            <select
              key={field}
              value={activeFieldFilter?.field === field ? activeFieldFilter.value : ''}
              onChange={e => {
                if (!e.target.value) { setActiveFieldFilter(null) }
                else { handleHighlight(field, e.target.value) }
              }}
              className="text-xs h-6 rounded border bg-background px-1 pr-5 cursor-pointer flex-shrink-0"
            >
              <option value="">{field[0].toUpperCase() + field.slice(1)}</option>
              {values.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          ))}
          {activeFieldFilter && (
            <button
              onClick={() => setActiveFieldFilter(null)}
              className="text-xs text-muted-foreground hover:text-foreground flex-shrink-0"
            >
              ✕ clear
            </button>
          )}
          </>
        )}
      </div>

      {/* min-h-0 prevents flex-1 from overflowing; absolute inset-0 on the
          vis-network container ensures vis-network reads a bounded clientHeight */}
      <div className="relative flex-1 min-h-0">

      {/* Cluster legend — top-left */}
      <ClusterLegend
        clusters={enrichedClusters}
        onClusterClick={handleClusterClick}
        activeClusterId={activeClusterFilter}
      />

      {/* Graph container — absolute inset-0 gives vis-network a stable bounded
          clientWidth/clientHeight regardless of flex parent chain */}
      <div
        ref={containerRef}
        className="absolute inset-0 bg-card"
        role="img"
        aria-label={`Knowledge graph with ${data.nodes.length} nodes and ${data.edges.length} edges`}
      />

      {/* Search match badge — bottom-left */}
      {highlightedIds && highlightedIds.size > 0 && (
        <div className="absolute bottom-2 left-2 z-10 bg-card/90 rounded px-2 py-1 text-xs border">
          {highlightedIds.size} matching record{highlightedIds.size !== 1 ? 's' : ''}
        </div>
      )}
      {highlightedIds && highlightedIds.size === 0 && (searchTerm?.trim().length ?? 0) >= 2 && (
        <div className="absolute bottom-2 left-2 z-10 bg-card/90 rounded px-2 py-1 text-xs text-muted-foreground border">
          No records match
        </div>
      )}

      {/* Active cluster filter badge — bottom-right */}
      {activeClusterFilter !== null && (
        <button
          onClick={() => setActiveClusterFilter(null)}
          className="absolute bottom-2 right-2 z-10 bg-card/90 border rounded px-2 py-1 text-xs hover:bg-accent transition-colors"
        >
          ✕ Cluster: {enrichedClusters.find(c => c.id === activeClusterFilter)?.name ?? activeClusterFilter}
        </button>
      )}

      {/* Right detail panel */}
      <div
        className={`absolute top-2 bottom-2 right-2 z-10 w-80 border rounded-md bg-card overflow-hidden
          transition-transform duration-300 ease-in-out
          ${selected ? 'translate-x-0 pointer-events-auto' : 'translate-x-[calc(100%+0.5rem)] pointer-events-none'}`}
      >
        <NodeSidebar
          node={selected}
          onClose={() => setSelected(null)}
          clusterName={selectedClusterName}
          clusterSize={selectedClusterSize}
          neighbors={selectedNeighbors}
          onHighlight={handleHighlight}
          activeFilter={activeFieldFilter}
        />
      </div>

      </div>{/* end relative flex-1 */}
    </div>
  )
}

import { useState, useMemo, useEffect, useRef } from 'react'
import type { GraphResponse, GraphNode } from '@/api/graph'
import { useVisNetwork } from './useVisNetwork'
import NodeSidebar from './NodeSidebar'
import ClusterLegend from './ClusterLegend'

function computeClusterNames(nodes: Array<{ cluster: number; props: Record<string, unknown> }>): Map<number, string> {
  const groups = new Map<number, Array<{ cluster: number; props: Record<string, unknown> }>>()
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
        if (/^[0-9a-f-]{36}$/i.test(val)) return // skip UUIDs
        if (/^\d+$/.test(val)) return // skip pure numbers
        if (!counts[key]) counts[key] = {}
        counts[key][val] = (counts[key][val] ?? 0) + 1
      })
    })

    let bestName = ''
    let bestCount = 0
    Object.values(counts).forEach(valCounts => {
      Object.entries(valCounts).forEach(([val, cnt]) => {
        const coverage = cnt / clusterNodes.length
        if (coverage >= 0.4 && cnt > bestCount) {
          bestCount = cnt
          bestName = val
        }
      })
    })

    names.set(clusterId, bestName || `Cluster ${clusterId}`)
  })

  return names
}

interface Props {
  data: GraphResponse
  searchTerm?: string
  resetZoomRef?: React.MutableRefObject<(() => void) | null>
}

export default function GraphCanvas({ data, searchTerm, resetZoomRef }: Props) {
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

  const { resetZoom, zoomToCluster, zoomToNode, clusterNamesRef } = useVisNetwork(
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

  const clusterNames = clusterNamesRef.current

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
    <div className="relative h-full w-full">

      {/* Cluster legend — top-left */}
      <ClusterLegend
        clusters={enrichedClusters}
        onClusterClick={handleClusterClick}
        activeClusterId={activeClusterFilter}
      />

      {/* Graph container — vis-network manages cursor and rendering internally */}
      <div
        ref={containerRef}
        className="block w-full h-full bg-card"
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

      {/* Active filter clear button — bottom-right */}
      {(activeClusterFilter !== null || activeFieldFilter) && (
        <button
          onClick={() => { setActiveClusterFilter(null); setActiveFieldFilter(null) }}
          className="absolute bottom-2 right-2 z-10 bg-card/90 border rounded px-2 py-1 text-xs hover:bg-accent transition-colors"
        >
          ✕ {activeClusterFilter !== null
            ? `Cluster: ${enrichedClusters.find(c => c.id === activeClusterFilter)?.name ?? activeClusterFilter}`
            : `${activeFieldFilter!.field}: ${activeFieldFilter!.value}`}
        </button>
      )}

      {/* Right detail panel — absolute like ClusterLegend, slides in via CSS transform.
          pointer-events-none when hidden so canvas clicks pass through. */}
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

    </div>
  )
}

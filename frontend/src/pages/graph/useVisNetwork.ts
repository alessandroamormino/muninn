import { useEffect, useRef } from 'react'
import type { MutableRefObject } from 'react'
import { Network } from 'vis-network'
import { DataSet } from 'vis-data'
import type { Options, Node as VisNode, Edge as VisEdge } from 'vis-network'
import type { GraphResponse, GraphNode } from '@/api/graph'
import { colorForCluster } from './ClusterLegend'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface UseVisNetworkReturn {
  resetZoom: () => void
  zoomToCluster: (clusterId: number, allNodes: GraphNode[]) => void
  zoomToNode: (nodeId: string) => void
  clusterNamesRef: MutableRefObject<Map<number, string>>
}

// ─── Options ──────────────────────────────────────────────────────────────────

const VIS_OPTIONS: Options = {
  autoResize: false,
  height: '100%',
  width: '100%',
  nodes: {
    shape: 'dot',
    borderWidth: 1,
    borderWidthSelected: 2,
    color: { border: 'rgba(0,0,0,0.2)' },
    chosen: false,
  },
  edges: {
    color: { color: '#e2e8f0', opacity: 0.8 },
    width: 0.5,
    smooth: false,
    chosen: false,
    hoverWidth: 0,
    selectionWidth: 0,
  },
  physics: {
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {
      gravitationalConstant: -50,
      centralGravity: 0.01,
      springLength: 100,
      springConstant: 0.08,
      damping: 0.4,
      avoidOverlap: 0.2,
    },
    stabilization: {
      enabled: true,
      iterations: 500,
      updateInterval: 50,
      fit: true,
    },
  },
  interaction: {
    hover: true,
    tooltipDelay: 200,
    dragNodes: true,
    dragView: true,
    zoomView: true,
    hoverConnectedEdges: false,
    selectConnectedEdges: false,
  },
}

// ─── Tooltip builder ──────────────────────────────────────────────────────────

/**
 * Builds an HTML tooltip string for a node's props.
 * XSS mitigation: String(v) coercion on all values; UUID values filtered out.
 * vis-network renders node.title as innerHTML — do NOT pass script tags or
 * raw user HTML. All values are coerced with String() before inclusion.
 */
function buildTooltipHtml(props: Record<string, unknown>): string {
  return Object.entries(props)
    .filter(([, v]) => v !== null && v !== undefined && typeof v === 'string' && !/^[0-9a-f-]{36}$/i.test(v as string))
    .slice(0, 6)
    .map(([k, v]) => `<b>${k}:</b> ${String(v)}`)
    .join('<br/>')
}

// ─── Highlight helper ─────────────────────────────────────────────────────────

function applyHighlight(
  nodesDataSet: DataSet<VisNode>,
  data: GraphResponse,
  highlightedIds: Set<string> | null | undefined,
  filterFn: ((node: GraphNode) => boolean) | null | undefined,
): void {
  const hasHighlight = (highlightedIds && highlightedIds.size > 0) || !!filterFn
  if (!hasHighlight) {
    nodesDataSet.update(
      data.nodes.map(n => ({
        id: n.id,
        color: {
          background: colorForCluster(n.cluster),
          border: 'rgba(0,0,0,0.15)',
          highlight: { background: colorForCluster(n.cluster), border: '#fff' },
          hover: { background: colorForCluster(n.cluster), border: '#fff' },
        },
        opacity: 1,
      } as VisNode))
    )
    return
  }
  nodesDataSet.update(
    data.nodes.map(n => {
      const inIds = highlightedIds?.has(n.id) ?? false
      const inFilter = filterFn ? filterFn(n) : false
      const isMatch = inIds || inFilter
      return {
        id: n.id,
        color: {
          background: isMatch ? colorForCluster(n.cluster) : 'rgba(148,163,184,0.2)',
          border: isMatch ? 'rgba(0,0,0,0.15)' : 'rgba(148,163,184,0.1)',
          highlight: { background: colorForCluster(n.cluster), border: '#fff' },
          hover: { background: colorForCluster(n.cluster), border: '#fff' },
        },
        opacity: isMatch ? 1 : 0.15,
      } as VisNode
    })
  )
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useVisNetwork(
  containerRef: React.RefObject<HTMLDivElement | null>,
  data: GraphResponse | null,
  onNodeClick: (node: GraphNode) => void,
  highlightedIds?: Set<string> | null,
  filterFn?: ((node: GraphNode) => boolean) | null,
): UseVisNetworkReturn {
  const networkRef = useRef<Network | null>(null)
  const nodesDataSetRef = useRef<DataSet<VisNode> | null>(null)
  const edgesDataSetRef = useRef<DataSet<VisEdge> | null>(null)
  const clusterNamesRef = useRef<Map<number, string>>(new Map())

  // Main effect: create/destroy the Network instance when data changes
  useEffect(() => {
    if (!containerRef.current || !data) return

    // Build cluster name map from backend-provided cluster names
    const namesMap = new Map<number, string>()
    data.clusters.forEach(c => namesMap.set(c.id, c.name))
    clusterNamesRef.current = namesMap

    // Map backend nodes → vis-network VisNode format
    const visNodes: VisNode[] = data.nodes.map(n => ({
      id: n.id,
      // Scale UMAP coordinates (~-10..+10) to vis-network canvas space as starting hint
      // vis-network centers and spreads them during physics stabilization
      x: n.x * 40,
      y: n.y * 40,
      // size = radius of dot shape (maps from node.radius 6..20)
      size: n.radius,
      color: {
        background: colorForCluster(n.cluster),
        border: 'rgba(0,0,0,0.15)',
        highlight: { background: colorForCluster(n.cluster), border: '#fff' },
        hover: { background: colorForCluster(n.cluster), border: '#fff' },
      },
      // title renders as innerHTML in vis-network tooltip div
      // buildTooltipHtml applies String() coercion + UUID filter (XSS mitigation)
      title: buildTooltipHtml(n.props),
      label: undefined,
    } as VisNode))

    // Map backend edges → vis-network Edge format
    const visEdges: VisEdge[] = data.edges.map((e, i) => ({
      id: i,
      from: e.source,
      to: e.target,
    } as VisEdge))

    const nodesDataSet = new DataSet(visNodes)
    const edgesDataSet = new DataSet(visEdges)
    nodesDataSetRef.current = nodesDataSet
    edgesDataSetRef.current = edgesDataSet

    // Use explicit pixel dimensions so vis-network never reads a wrong clientHeight
    // during a React re-render that happens to fall mid-stabilization.
    // autoResize:false + fixed px → canvas size is locked at creation time.
    const cw = containerRef.current.clientWidth || containerRef.current.offsetWidth
    const ch = containerRef.current.clientHeight || containerRef.current.offsetHeight
    const initOptions: Options = {
      ...VIS_OPTIONS,
      width: cw > 0 ? `${cw}px` : '100%',
      height: ch > 0 ? `${ch}px` : '100%',
    }

    const network = new Network(
      containerRef.current,
      { nodes: nodesDataSet, edges: edgesDataSet },
      initOptions,
    )
    networkRef.current = network

    // Stop the simulation loop without triggering a full setOptions redraw cycle.
    // setOptions({physics:{enabled:false}}) internally calls initPhysics + canvas
    // setSize which races with React re-renders and causes "Canvas exceeds max size".
    network.on('stabilizationIterationsDone', () => {
      network.stopSimulation()
    })

    // After drag: stop simulation so nodes don't drift.
    // Do NOT set fixed:{x,y} — that would prevent the user from re-dragging the node.
    // stopSimulation() is sufficient: nodes stay where dropped since physics is off.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    network.on('dragEnd', (params: any) => {
      if (!params.nodes.length) return  // viewport pan, not a node drag
      network.stopSimulation()
    })

    // Click on node → open NodeSidebar via onNodeClick callback
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    network.on('click', (params: any) => {
      if (!params.nodes.length) return  // background click — ignore
      const nodeId = params.nodes[0] as string
      const original = data.nodes.find(n => n.id === nodeId)
      if (original) onNodeClick(original)
    })

    return () => {
      network.destroy()
      networkRef.current = null
      nodesDataSetRef.current = null
      edgesDataSetRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])  // re-initialize only when data reference changes (collection switch)

  // Separate effect: apply highlight/dim when filter state changes
  // Does NOT recreate the network — updates node colors in the existing DataSet
  useEffect(() => {
    if (!nodesDataSetRef.current || !data) return
    applyHighlight(nodesDataSetRef.current, data, highlightedIds, filterFn)
  }, [highlightedIds, filterFn, data])

  // ─── Returned control API ────────────────────────────────────────────────────

  const resetZoom = () =>
    networkRef.current?.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } })

  const zoomToCluster = (clusterId: number, allNodes: GraphNode[]) => {
    const ids = allNodes.filter(n => n.cluster === clusterId).map(n => n.id)
    networkRef.current?.fit({ nodes: ids, animation: { duration: 600, easingFunction: 'easeInOutQuad' } })
  }

  const zoomToNode = (nodeId: string) => {
    networkRef.current?.focus(nodeId, { scale: 3, animation: { duration: 600, easingFunction: 'easeInOutQuad' } })
  }

  return { resetZoom, zoomToCluster, zoomToNode, clusterNamesRef }
}

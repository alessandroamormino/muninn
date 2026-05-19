import { useEffect, useRef } from 'react'
import * as d3 from 'd3'
import type { GraphResponse, GraphNode } from '@/api/graph'
import { colorForCluster } from './ClusterLegend'

interface SimNode extends d3.SimulationNodeDatum {
  id: string
  cluster: number
  radius: number
  props: Record<string, unknown>
}
interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  source: string | SimNode
  target: string | SimNode
}

interface ClusterCentroid {
  x: number
  y: number
  count: number
}

interface Args {
  canvas: HTMLCanvasElement | null
  data: GraphResponse | null
  onNodeClick: (node: GraphNode) => void
  highlightedIds?: Set<string> | null
  filterFn?: ((node: GraphNode) => boolean) | null
  selectedNodeId?: string | null
}

const LOD_THRESHOLD = 0.6  // below: cluster bubbles, above: individual nodes
const BUBBLE_SCREEN_R = 40 // minimum bubble radius in screen pixels

export function computeClusterNames(nodes: Array<{ cluster: number; props: Record<string, unknown> }>): Map<number, string> {
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

export function useGraphRender({ canvas, data, onNodeClick, highlightedIds, filterFn, selectedNodeId }: Args) {
  const simRef = useRef<d3.Simulation<SimNode, SimLink> | null>(null)
  const transformRef = useRef(d3.zoomIdentity)
  const nodesRef = useRef<SimNode[]>([])
  const clusterCentroidsRef = useRef<Map<number, ClusterCentroid>>(new Map())
  const clusterAdjRef = useRef<Map<string, number>>(new Map())
  const clusterNamesRef = useRef<Map<number, string>>(new Map())
  const edgePathRef = useRef<Path2D | null>(null)
  const zoomRef = useRef<d3.ZoomBehavior<HTMLCanvasElement, unknown> | null>(null)
  const selRef = useRef<d3.Selection<HTMLCanvasElement, unknown, null, undefined> | null>(null)

  // Refs for highlight/filter/selection — updated without re-running simulation
  const highlightedIdsRef = useRef<Set<string> | null | undefined>(highlightedIds)
  const filterFnRef = useRef<((node: GraphNode) => boolean) | null | undefined>(filterFn)
  const selectedNodeIdRef = useRef<string | null | undefined>(selectedNodeId)
  const dirtyRef = useRef(false)
  const isTransitioningRef = useRef(false)

  // Resize-aware redraw — debounced so sidebar CSS transition (300ms) doesn't
  // blank the canvas on every animation frame (canvas.width= clears content)
  useEffect(() => {
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = window.devicePixelRatio || 1
    let timer = 0
    const applyResize = () => {
      const rect = canvas.getBoundingClientRect()
      const newW = Math.round(rect.width * dpr)
      const newH = Math.round(rect.height * dpr)
      if (canvas.width === newW && canvas.height === newH) return
      canvas.width = newW
      canvas.height = newH
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      dirtyRef.current = true
    }
    const resize = () => {
      clearTimeout(timer)
      timer = window.setTimeout(applyResize, 50)
    }
    applyResize() // immediate on first mount
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)
    return () => { ro.disconnect(); clearTimeout(timer) }
  }, [canvas])

  // Separate effect: update highlight/filter/selection refs without touching simulation
  useEffect(() => {
    highlightedIdsRef.current = highlightedIds
    filterFnRef.current = filterFn
    dirtyRef.current = true
  }, [highlightedIds, filterFn])

  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId
    dirtyRef.current = true
  }, [selectedNodeId])

  // Re-create simulation only when data REFERENCE changes (not on every tick)
  useEffect(() => {
    if (!canvas || !data) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    simRef.current?.stop()
    const width = canvas.clientWidth
    const height = canvas.clientHeight

    const simNodes: SimNode[] = data.nodes.map((n) => ({
      id: n.id,
      cluster: n.cluster,
      radius: n.radius,
      props: n.props,
      x: n.x * 40 + width / 2,   // scale UMAP coords to canvas space as a starting layout
      y: n.y * 40 + height / 2,
    }))
    const simLinks: SimLink[] = data.edges.map((e) => ({ source: e.source, target: e.target }))
    nodesRef.current = simNodes
    clusterNamesRef.current = computeClusterNames(simNodes)

    const updateCentroids = () => {
      const centroids = new Map<number, ClusterCentroid>()
      simNodes.forEach(n => {
        if (!centroids.has(n.cluster)) centroids.set(n.cluster, { x: 0, y: 0, count: 0 })
        const c = centroids.get(n.cluster)!
        c.x += n.x ?? 0
        c.y += n.y ?? 0
        c.count++
      })
      centroids.forEach(c => { c.x /= c.count; c.y /= c.count })
      clusterCentroidsRef.current = centroids
    }

    const updateAdjacency = () => {
      const adj = new Map<string, number>()
      simLinks.forEach(l => {
        const s = l.source as SimNode
        const t = l.target as SimNode
        if (s.cluster === t.cluster) return
        const key = [Math.min(s.cluster, t.cluster), Math.max(s.cluster, t.cluster)].join('-')
        adj.set(key, (adj.get(key) ?? 0) + 1)
      })
      clusterAdjRef.current = adj
    }

    const sim = d3.forceSimulation<SimNode>(simNodes)
      .force('link', d3.forceLink<SimNode, SimLink>(simLinks)
        .id((d) => d.id)
        .strength(0.05)
        .distance(40))
      .force('charge', d3.forceManyBody<SimNode>().strength(-30))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide<SimNode>().radius((d) => d.radius + 1))
      .alphaDecay(0.08)
      .velocityDecay(0.6)

    const redraw = () => {
      const t = transformRef.current
      const k = t.k

      // Viewport culling bounds in data coords
      const vl = -t.x / k
      const vt2 = -t.y / k
      const vr = (canvas.clientWidth - t.x) / k
      const vb = (canvas.clientHeight - t.y) / k
      const margin = 30

      ctx.save()
      ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight)
      ctx.translate(t.x, t.y)
      ctx.scale(k, k)

      const showBubbles = k < LOD_THRESHOLD
      const showNodes = k >= LOD_THRESHOLD

      // Draw cluster bubbles at low zoom
      if (showBubbles) {
        ctx.globalAlpha = 1

        // Compute which clusters have matching nodes (when filter is active)
        const hasAnyFilter = (filterFnRef.current !== null && filterFnRef.current !== undefined) ||
          (highlightedIdsRef.current != null && highlightedIdsRef.current.size > 0)

        const matchingClusters = new Set<number>()
        if (hasAnyFilter) {
          nodesRef.current.forEach(n => {
            const isMatch = (highlightedIdsRef.current?.has(n.id) ?? false) ||
              (filterFnRef.current ? filterFnRef.current({ id: n.id, x: n.x ?? 0, y: n.y ?? 0, cluster: n.cluster, radius: n.radius, props: n.props } as GraphNode) : false)
            if (isMatch) matchingClusters.add(n.cluster)
          })
        }

        // Draw inter-cluster edges first (behind bubbles)
        clusterAdjRef.current.forEach((count, key) => {
          const [idA, idB] = key.split('-').map(Number)
          const centA = clusterCentroidsRef.current.get(idA)
          const centB = clusterCentroidsRef.current.get(idB)
          if (!centA || !centB) return
          const edgeMatch = !hasAnyFilter || matchingClusters.has(idA) || matchingClusters.has(idB)
          ctx.globalAlpha = edgeMatch ? 0.4 : 0.05
          const lineW = Math.min(8, Math.sqrt(count) * 1.5) / k
          ctx.beginPath()
          ctx.strokeStyle = '#64748b'
          ctx.lineWidth = lineW
          ctx.moveTo(centA.x, centA.y)
          ctx.lineTo(centB.x, centB.y)
          ctx.stroke()
        })
        ctx.globalAlpha = 1

        // Draw cluster bubbles (on top of inter-cluster edges)
        clusterCentroidsRef.current.forEach((centroid, clusterId) => {
          if (clusterId < 0) return // skip noise
          // Constant screen-size bubbles: always at least BUBBLE_SCREEN_R px on screen
          const r = Math.max(BUBBLE_SCREEN_R, Math.sqrt(centroid.count) * 2) / k
          const bubbleMatch = !hasAnyFilter || matchingClusters.has(clusterId)
          ctx.globalAlpha = bubbleMatch ? 1 : 0.1
          ctx.beginPath()
          ctx.arc(centroid.x, centroid.y, r, 0, 2 * Math.PI)
          ctx.fillStyle = colorForCluster(clusterId)
          ctx.fill()
          ctx.strokeStyle = 'rgba(255,255,255,0.6)'
          ctx.lineWidth = 2 / k
          ctx.stroke()

          const name = clusterNamesRef.current?.get(clusterId) ?? `Cluster ${clusterId}`
          const nameFontPx = Math.min(14, Math.max(9, BUBBLE_SCREEN_R * 0.35))
          ctx.globalAlpha = bubbleMatch ? 1 : 0.1
          ctx.fillStyle = 'white'
          ctx.font = `bold ${nameFontPx / k}px sans-serif`
          ctx.textAlign = 'center'
          ctx.textBaseline = 'middle'
          ctx.fillText(name, centroid.x, centroid.y)

          const countFontPx = Math.min(11, Math.max(8, BUBBLE_SCREEN_R * 0.25))
          ctx.font = `${countFontPx / k}px sans-serif`
          ctx.globalAlpha = bubbleMatch ? 0.8 : 0.08
          ctx.fillText(`${centroid.count}`, centroid.x, centroid.y + Math.max(12, 16 / k))
          ctx.globalAlpha = 1
        })
      }

      // Draw individual nodes + edges at high zoom
      if (showNodes) {
        const nodeAlpha = 1
        ctx.globalAlpha = 1

        if (isTransitioningRef.current) {
          // FAST PATH during zoom transition: skip edges, draw plain circles only
          simNodes.forEach(n => {
            if (n.x == null || n.y == null) return
            if (n.x < vl - margin || n.x > vr + margin || n.y < vt2 - margin || n.y > vb + margin) return
            const hasHighlight = (highlightedIdsRef.current && highlightedIdsRef.current.size > 0) || !!filterFnRef.current
            const isMatch = !hasHighlight || (highlightedIdsRef.current?.has(n.id) ?? false) ||
              (filterFnRef.current ? filterFnRef.current({ id: n.id, x: n.x, y: n.y, cluster: n.cluster, radius: n.radius, props: n.props } as GraphNode) : false)
            ctx.globalAlpha = hasHighlight ? (isMatch ? 1 : 0.1) : 1
            ctx.beginPath()
            ctx.arc(n.x, n.y, n.radius, 0, 2 * Math.PI)
            ctx.fillStyle = colorForCluster(n.cluster)
            ctx.fill()
          })
          ctx.globalAlpha = 1
        } else {
          // FULL QUALITY: edges + nodes with rings + selected node ring

          // edges: use pre-compiled Path2D if available (post-simulation), else manual with culling
          ctx.strokeStyle = '#e2e8f0'
          ctx.lineWidth = 0.5
          ctx.beginPath()
          if (edgePathRef.current) {
            ctx.stroke(edgePathRef.current)
          } else {
            simLinks.forEach((l) => {
              const s = l.source as SimNode
              const tgt = l.target as SimNode
              if (s.x == null || s.y == null || tgt.x == null || tgt.y == null) return
              const sVis = s.x > vl - margin && s.x < vr + margin && s.y > vt2 - margin && s.y < vb + margin
              const tVis = tgt.x > vl - margin && tgt.x < vr + margin && tgt.y > vt2 - margin && tgt.y < vb + margin
              if (!sVis && !tVis) return
              ctx.moveTo(s.x, s.y)
              ctx.lineTo(tgt.x, tgt.y)
            })
            ctx.stroke()
          }

          // Determine if any highlight is active
          const currentHighlightedIds = highlightedIdsRef.current
          const currentFilterFn = filterFnRef.current
          const hasHighlight = (currentHighlightedIds && currentHighlightedIds.size > 0) || !!currentFilterFn

          // nodes (with viewport culling and highlight logic)
          simNodes.forEach((n) => {
            if (n.x == null || n.y == null) return
            if (n.x < vl - margin || n.x > vr + margin || n.y < vt2 - margin || n.y > vb + margin) return

            // Highlight logic
            let alpha = nodeAlpha
            let isHighlighted = false
            if (hasHighlight) {
              const inIds = currentHighlightedIds?.has(n.id) ?? false
              const inFilter = currentFilterFn
                ? currentFilterFn({ id: n.id, x: n.x, y: n.y, cluster: n.cluster, radius: n.radius, props: n.props })
                : false
              isHighlighted = inIds || inFilter
              alpha = isHighlighted ? nodeAlpha : nodeAlpha * 0.12
            }

            ctx.globalAlpha = alpha
            ctx.beginPath()
            ctx.arc(n.x, n.y, n.radius, 0, 2 * Math.PI)
            ctx.fillStyle = colorForCluster(n.cluster)
            ctx.fill()

            // Ring for highlighted nodes (when highlight is active)
            if (hasHighlight && isHighlighted) {
              ctx.strokeStyle = 'white'
              ctx.lineWidth = 2
              ctx.stroke()
            } else {
              ctx.strokeStyle = 'rgba(15, 23, 42, 0.2)'
              ctx.lineWidth = 0.5
              ctx.stroke()
            }
          })
          ctx.globalAlpha = nodeAlpha  // reset

          // Draw selected node ring on top
          const selId = selectedNodeIdRef.current
          if (selId && nodeAlpha > 0) {
            const sel = nodesRef.current.find(n => n.id === selId)
            if (sel && sel.x != null && sel.y != null) {
              ctx.globalAlpha = nodeAlpha
              ctx.beginPath()
              ctx.arc(sel.x, sel.y, sel.radius + 4, 0, 2 * Math.PI)
              ctx.strokeStyle = 'white'
              ctx.lineWidth = 3
              ctx.stroke()
              ctx.beginPath()
              ctx.arc(sel.x, sel.y, sel.radius + 4, 0, 2 * Math.PI)
              ctx.strokeStyle = colorForCluster(sel.cluster)
              ctx.lineWidth = 1.5
              ctx.stroke()
            }
          }

          ctx.globalAlpha = 1
        }
      }

      ctx.restore()
    }

    // RAF-based render loop — avoid double-draw from overlapping tick + zoom events
    dirtyRef.current = false
    let rafId: number

    sim.on('tick', () => {
      updateCentroids()
      dirtyRef.current = true
    })

    sim.on('end', () => {
      // Bake edge geometry into Path2D for fast GPU-compiled drawing
      const path = new Path2D()
      simLinks.forEach(l => {
        const s = l.source as SimNode
        const tgt = l.target as SimNode
        if (s.x != null && s.y != null && tgt.x != null && tgt.y != null) {
          path.moveTo(s.x, s.y)
          path.lineTo(tgt.x, tgt.y)
        }
      })
      edgePathRef.current = path
      // Compute cluster adjacency once after simulation converges
      updateAdjacency()
      dirtyRef.current = true
    })

    const animate = () => {
      if (dirtyRef.current) { redraw(); dirtyRef.current = false }
      rafId = requestAnimationFrame(animate)
    }
    rafId = requestAnimationFrame(animate)

    simRef.current = sim

    // Zoom + pan
    const sel = d3.select<HTMLCanvasElement, unknown>(canvas)
    const zoom = d3.zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.1, 8])
      .on('start', () => {
        // Stop simulation when user interacts — eliminates simulation+zoom overlap lag
        if (simRef.current) simRef.current.stop()
      })
      .on('zoom', (event) => {
        transformRef.current = event.transform
        dirtyRef.current = true
      })
    sel.call(zoom)

    zoom
      .on('start.quality', () => { isTransitioningRef.current = true })
      .on('end.quality', () => {
        isTransitioningRef.current = false
        dirtyRef.current = true  // force full-quality redraw after transition ends
      })

    zoomRef.current = zoom
    selRef.current = sel

    // Expose resetZoom via a custom event for the toolbar button
    const handleReset = () => {
      sel.transition().duration(300).call(zoom.transform, d3.zoomIdentity)
    }
    canvas.addEventListener('graph:reset-zoom', handleReset)

    // Click-to-select: cluster bubble at low zoom, node at high zoom
    const handleClick = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const t = transformRef.current
      const mx = (event.clientX - rect.left - t.x) / t.k
      const my = (event.clientY - rect.top - t.y) / t.k
      const k = t.k

      if (k < LOD_THRESHOLD) {
        // Try to hit a cluster bubble — zoom to its bounding box
        let hitCluster: number | null = null
        clusterCentroidsRef.current.forEach((centroid, clusterId) => {
          if (clusterId < 0) return
          const r = Math.max(BUBBLE_SCREEN_R, Math.sqrt(centroid.count) * 2) / k
          const dist = Math.sqrt((mx - centroid.x) ** 2 + (my - centroid.y) ** 2)
          if (dist < r) hitCluster = clusterId
        })
        if (hitCluster !== null) {
          zoomToCluster(hitCluster)
          return // don't open sidebar
        }
      }

      // Hit test individual nodes via quadtree
      const tree = d3.quadtree<SimNode>()
        .x((d) => d.x ?? 0)
        .y((d) => d.y ?? 0)
        .addAll(simNodes)
      const found = tree.find(mx, my, 20)
      if (found) {
        onNodeClick({
          id: found.id,
          x: found.x ?? 0,
          y: found.y ?? 0,
          cluster: found.cluster,
          radius: found.radius,
          props: found.props,
        })
      }
    }
    canvas.addEventListener('click', handleClick)

    return () => {
      sim.stop()
      cancelAnimationFrame(rafId)
      canvas.removeEventListener('click', handleClick)
      canvas.removeEventListener('graph:reset-zoom', handleReset)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvas, data, onNodeClick])

  function zoomToCluster(clusterId: number) {
    simRef.current?.stop()  // stop simulation before zoom transition
    if (!canvas || !zoomRef.current || !selRef.current) return
    const clusterNodes = nodesRef.current.filter(n => n.cluster === clusterId)
    if (clusterNodes.length === 0) return

    const xs = clusterNodes.map(n => n.x ?? 0)
    const ys = clusterNodes.map(n => n.y ?? 0)
    const minX = Math.min(...xs)
    const maxX = Math.max(...xs)
    const minY = Math.min(...ys)
    const maxY = Math.max(...ys)

    const padding = 80
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    const scaleX = w / (maxX - minX + padding * 2)
    const scaleY = h / (maxY - minY + padding * 2)
    const targetK = Math.min(scaleX, scaleY, 4)
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2

    selRef.current.transition().duration(600).ease(d3.easeCubicOut).call(
      zoomRef.current.transform,
      d3.zoomIdentity
        .translate(w / 2 - cx * targetK, h / 2 - cy * targetK)
        .scale(targetK)
    )
  }

  function zoomToNode(nodeId: string, dataX: number, dataY: number) {
    simRef.current?.stop()
    if (!canvas || !zoomRef.current || !selRef.current) return
    // Find actual current position from simNodes
    const node = nodesRef.current.find(n => n.id === nodeId)
    const x = node?.x ?? dataX * 40 + canvas.clientWidth / 2
    const y = node?.y ?? dataY * 40 + canvas.clientHeight / 2
    const targetK = 3
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    selRef.current.transition().duration(600).ease(d3.easeCubicOut).call(
      zoomRef.current.transform,
      d3.zoomIdentity.translate(w / 2 - x * targetK, h / 2 - y * targetK).scale(targetK)
    )
  }

  return {
    resetZoom: () => canvas?.dispatchEvent(new CustomEvent('graph:reset-zoom')),
    clusterNamesRef,
    zoomToCluster,
    zoomToNode,
  }
}

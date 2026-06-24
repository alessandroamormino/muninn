import { useState, useRef } from 'react'
import EntityDropdown from '@/components/EntityDropdown'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import GraphCanvas from './graph/GraphCanvas'
import { useGraph } from '@/api/graph'
import { useEntityInfo } from '@/api/config'

export default function GraphPage() {
  const [collection, setCollection] = useState<string | null>(null)
  const { data: graphInfo } = useEntityInfo(collection)
  const isFtsMode = collection != null && graphInfo?.search_mode === 'fts'
  const graph = useGraph(isFtsMode ? null : collection)
  const resetZoomRef = useRef<(() => void) | null>(null)

  const isTooFew = (() => {
    const e = graph.error as Error | undefined
    return !!e && /too few records/i.test(e.message)
  })()

  const errMsg = (() => {
    const e = graph.error as Error | undefined
    if (!e) return null
    if (isTooFew) return null  // shown as empty state, not error
    return 'Failed to generate the graph. The collection may be empty or the server encountered an error.'
  })()

  return (
    <div className="h-[calc(100vh-5.5rem)] flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Knowledge Graph</h1>
        <div className="flex-1" />
        {graph.data && (
          <>
            <Badge variant="outline">{graph.data.nodes.length} nodes</Badge>
            <Button size="sm" variant="outline" onClick={() => resetZoomRef.current?.()}>
              Reset view
            </Button>
          </>
        )}
        <EntityDropdown activeOnly value={collection} onChange={setCollection} />
        <Button
          onClick={() => graph.refetch()}
          disabled={!collection || graph.isFetching || isFtsMode}
        >
          {graph.isFetching ? 'Computing...' : 'Load Graph'}
        </Button>
      </div>

      <div className="flex-1 border rounded-md overflow-hidden relative bg-muted/30">
        {!collection && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
            Select an entity, then click Load Graph.
          </div>
        )}

        {collection && isFtsMode && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-6">
            <h3 className="text-base font-semibold mb-1">Knowledge Graph non disponibile</h3>
            <p className="text-sm text-muted-foreground max-w-sm">
              Il Knowledge Graph richiede vettori semantici per calcolare la proiezione UMAP.
              Con <code className="font-mono text-xs bg-muted px-1 rounded">search_mode: fts</code> non vengono
              calcolati vettori. Cambia modalità a <strong>hybrid</strong> o <strong>vector</strong> per abilitare il grafo.
            </p>
          </div>
        )}

        {collection && graph.isFetching && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
            <div className="h-8 w-8 border-2 border-primary border-t-transparent rounded-full animate-spin" aria-hidden />
            <p className="text-sm text-muted-foreground">
              Computing UMAP projection... (this may take a few seconds)
            </p>
          </div>
        )}

        {collection && isTooFew && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-6">
            <h3 className="text-base font-semibold mb-1">No data to visualize</h3>
            <p className="text-sm text-muted-foreground">
              Run a full sync for this collection first, then load the graph.
            </p>
          </div>
        )}

        {collection && errMsg && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="border border-destructive/30 bg-destructive/5 text-destructive p-4 rounded-md text-sm max-w-md">
              {errMsg}
            </div>
          </div>
        )}

        {graph.data && !graph.isFetching && !errMsg && (
          <GraphCanvas data={graph.data} resetZoomRef={resetZoomRef} />
        )}
      </div>
    </div>
  )
}

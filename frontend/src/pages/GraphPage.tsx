import { useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import EntityDropdown from '@/components/EntityDropdown'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import GraphCanvas from './graph/GraphCanvas'
import { useGraph } from '@/api/graph'
import { useEntityInfo } from '@/api/config'

export default function GraphPage() {
  const { t } = useTranslation()
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
    return t('graph.errGenerate')
  })()

  return (
    <div className="h-[calc(100vh-5.5rem)] flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">{t('graph.title')}</h1>
        <div className="flex-1" />
        {graph.data && (
          <>
            <Badge variant="outline">{t('graph.nodes', { n: graph.data.nodes.length })}</Badge>
            <Button size="sm" variant="outline" onClick={() => resetZoomRef.current?.()}>
              {t('graph.resetView')}
            </Button>
          </>
        )}
        <EntityDropdown activeOnly value={collection} onChange={setCollection} />
        <Button
          onClick={() => graph.refetch()}
          disabled={!collection || graph.isFetching || isFtsMode}
        >
          {graph.isFetching ? t('graph.computing') : t('graph.load')}
        </Button>
      </div>

      <div className="flex-1 border rounded-md overflow-hidden relative bg-muted/30">
        {!collection && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
            {t('graph.selectPrompt')}
          </div>
        )}

        {collection && isFtsMode && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-6">
            <h3 className="text-base font-semibold mb-1">{t('graph.ftsTitle')}</h3>
            <p className="text-sm text-muted-foreground max-w-sm">
              {t('graph.ftsHint')}
            </p>
          </div>
        )}

        {collection && graph.isFetching && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
            <div className="h-8 w-8 border-2 border-primary border-t-transparent rounded-full animate-spin" aria-hidden />
            <p className="text-sm text-muted-foreground">
              {t('graph.computingUmap')}
            </p>
          </div>
        )}

        {collection && isTooFew && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-6">
            <h3 className="text-base font-semibold mb-1">{t('graph.tooFewTitle')}</h3>
            <p className="text-sm text-muted-foreground">
              {t('graph.tooFewHint')}
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

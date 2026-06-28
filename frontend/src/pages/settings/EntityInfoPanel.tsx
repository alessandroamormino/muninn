import { toast } from 'sonner'
import { useTranslation } from 'react-i18next'
import { useEntityInfo } from '@/api/config'
import { usePurgeEntityCache } from '@/api/entities'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { EngineBadge } from '@/components/EngineBadge'

export default function EntityInfoPanel({ collection }: { collection: string }) {
  const { t } = useTranslation()
  const { data: info, isLoading, isError } = useEntityInfo(collection)
  const purgeCache = usePurgeEntityCache()

  if (isLoading) {
    return (
      <div className="mt-4 grid grid-cols-3 gap-4">
        <Skeleton className="h-16 rounded-md" />
        <Skeleton className="h-16 rounded-md" />
        <Skeleton className="h-16 rounded-md" />
      </div>
    )
  }

  if (isError) {
    return (
      <p className="mt-4 text-destructive text-sm">
        {t('entityInfo.loadError')}
      </p>
    )
  }

  return (
    <div className="mt-4">
      <dl className="grid grid-cols-3 gap-4">
        <div>
          <dt className="text-muted-foreground text-xs">{t('entityInfo.totalObjects')}</dt>
          <dd className="text-sm font-semibold">{info?.total_objects ?? '—'}</dd>
        </div>
        {info?.search_mode !== 'fts' && info?.search_mode !== 'bm25' && (
          <div>
            <dt className="text-muted-foreground text-xs">{t('entityInfo.model')}</dt>
            <dd className="text-sm font-semibold">{info?.embedding_model ?? '—'}</dd>
          </div>
        )}
        <div>
          <dt className="text-muted-foreground text-xs">{t('entityInfo.sourceType')}</dt>
          <dd className="text-sm font-semibold">{info?.sync_mode ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">{t('entityInfo.engine')}</dt>
          <dd className="text-sm font-semibold"><EngineBadge engine={info?.vector_store_engine ?? 'weaviate'} /></dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">{t('entityInfo.searchMode')}</dt>
          <dd className="text-sm font-semibold">{info?.search_mode ?? '—'}</dd>
        </div>
      </dl>

      <div className="mt-4 pt-3 border-t flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {t('entityInfo.purgeHint')}
        </p>
        <Button
          size="sm"
          variant="outline"
          disabled={purgeCache.isPending}
          onClick={() =>
            purgeCache.mutate(collection, {
              onSuccess: () => toast.success(t('entityInfo.purgeSuccess', { collection })),
              onError: (e: Error) =>
                toast.error(/403/.test(e.message) ? t('entityInfo.purgeForbidden') : e.message),
            })
          }
        >
          {purgeCache.isPending ? t('entityInfo.purging') : t('entityInfo.purge')}
        </Button>
      </div>
    </div>
  )
}

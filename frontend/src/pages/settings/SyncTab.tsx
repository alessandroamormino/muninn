import { Button } from '@/components/ui/button'
import { useTranslation } from 'react-i18next'
import { useTriggerSync, useIncrementalSync } from '@/api/sync'
import { useSyncStatus } from '@/api/syncStatus'
import { toast } from 'sonner'

function formatEta(seconds: number | null): string {
  if (seconds === null) return '…'
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s}s`
}

export default function SyncTab({ collection }: { collection: string }) {
  const { t } = useTranslation()
  const phaseLabel = (phase: string): string => {
    if (phase === 'fetching') return t('sync.phaseFetching')
    if (phase === 'embedding') return t('sync.phaseEmbedding')
    if (phase === 'upserting') return t('sync.phaseUpserting')
    return phase
  }
  const fullMutation = useTriggerSync()
  const incrementalMutation = useIncrementalSync()
  const { data: syncStatus } = useSyncStatus()

  const isEitherPending = fullMutation.isPending || incrementalMutation.isPending

  const isRunningHere = syncStatus?.status === 'running' && syncStatus?.collection === collection
  const progress = isRunningHere ? syncStatus?.progress : null

  return (
    <div className="mt-4">
      <p className="text-sm text-muted-foreground">
        {t('sync.desc', { collection })}
      </p>

      <div className="flex gap-2 mt-6">
        <Button
          disabled={isEitherPending || isRunningHere}
          onClick={() => {
            fullMutation.mutate(collection, {
              onSuccess: () => toast.success(t('sync.startedFull')),
              onError: (e: Error) => toast.error(e.message),
            })
          }}
        >
          {fullMutation.isPending ? t('sync.starting') : t('sync.runFull')}
        </Button>
        <Button
          variant="outline"
          disabled={isEitherPending || isRunningHere}
          onClick={() => {
            incrementalMutation.mutate(collection, {
              onSuccess: () => toast.success(t('sync.startedIncremental')),
              onError: (e: Error) => toast.error(e.message),
            })
          }}
        >
          {incrementalMutation.isPending ? t('sync.starting') : t('sync.runIncremental')}
        </Button>
      </div>

      {isRunningHere && (
        <div className="mt-6 rounded-lg border bg-muted/40 p-4 space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2 font-medium">
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-sky-500" />
              </span>
              {progress ? phaseLabel(progress.phase) : t('sync.starting')}
            </span>
            <span className="text-muted-foreground tabular-nums">
              {progress ? `${progress.percent.toFixed(1)}%` : ''}
            </span>
          </div>

          {progress && (
            <>
              <div className="w-full bg-muted rounded-full h-1.5 overflow-hidden">
                {progress.total === 0 ? (
                  <div className="h-1.5 rounded-full bg-sky-500/60 animate-pulse w-full" />
                ) : (
                  <div
                    className="bg-sky-500 h-1.5 rounded-full transition-all duration-500"
                    style={{ width: `${progress.percent}%` }}
                  />
                )}
              </div>
              {progress.total > 0 && (
                <div className="flex justify-between text-xs text-muted-foreground tabular-nums">
                  <span>{progress.done.toLocaleString()} / {progress.total.toLocaleString()} {t('sync.records')}</span>
                  <span>ETA {formatEta(progress.eta_seconds)}</span>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {!!syncStatus?.last_run?.quantization_warning && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm mt-4">
          {t('sync.quantWarning')}
        </div>
      )}
    </div>
  )
}

import { Button } from '@/components/ui/button'
import { useTriggerSync, useIncrementalSync, useSyncStatus } from '@/api/sync'
import { toast } from 'sonner'

export default function SyncTab({ collection }: { collection: string }) {
  const fullMutation = useTriggerSync()
  const incrementalMutation = useIncrementalSync()
  const { data: syncStatus } = useSyncStatus()

  const isEitherPending = fullMutation.isPending || incrementalMutation.isPending

  return (
    <div className="mt-4 space-y-3">
      <p className="text-sm text-muted-foreground">
        Trigger a sync for <code className="font-mono">{collection}</code>.
        Full sync re-fetches all records and rebuilds the index.
        Incremental sync processes only new or changed records.
      </p>

      <div className="flex gap-2">
        <Button
          disabled={isEitherPending}
          onClick={() => {
            fullMutation.mutate(collection, {
              onSuccess: () => toast.success('Sync started in background.'),
              onError: (e: Error) => toast.error(e.message),
            })
          }}
        >
          {fullMutation.isPending ? 'Starting...' : 'Run full sync'}
        </Button>
        <Button
          variant="outline"
          disabled={isEitherPending}
          onClick={() => {
            incrementalMutation.mutate(collection, {
              onSuccess: () => toast.success('Incremental sync started in background.'),
              onError: (e: Error) => toast.error(e.message),
            })
          }}
        >
          {incrementalMutation.isPending ? 'Starting...' : 'Run incremental sync'}
        </Button>
      </div>

      {syncStatus?.last_run?.quantization_warning && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
          Attenzione: collection con &gt;50K record senza quantizzazione. Considera{' '}
          <code>weaviate.quantization: sq</code> o <code>pq</code> in config.yaml.
        </div>
      )}
    </div>
  )
}

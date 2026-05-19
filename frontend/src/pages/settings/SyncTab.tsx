import { Button } from '@/components/ui/button'
import { useTriggerSync } from '@/api/sync'
import { toast } from 'sonner'

export default function SyncTab({ collection }: { collection: string }) {
  const mutation = useTriggerSync()
  return (
    <div className="mt-4 space-y-3">
      <p className="text-sm text-muted-foreground">
        Trigger a full sync for <code className="font-mono">{collection}</code>.
        This reads <code className="font-mono">configuration/{collection}/config.yaml</code>,
        re-fetches all records, and rebuilds the index.
      </p>
      <Button
        disabled={mutation.isPending}
        onClick={() => {
          mutation.mutate(collection, {
            onSuccess: () => toast.success('Sync started in background.'),
            onError: (e: Error) => toast.error(e.message),
          })
        }}
      >
        {mutation.isPending ? 'Starting...' : 'Run sync now'}
      </Button>
    </div>
  )
}

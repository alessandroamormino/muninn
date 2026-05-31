import { useEntityInfo } from '@/api/config'
import { Skeleton } from '@/components/ui/skeleton'

export default function EntityInfoPanel({ collection }: { collection: string }) {
  const { data: info, isLoading, isError } = useEntityInfo(collection)

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
        Impossibile caricare la configurazione. Riprova.
      </p>
    )
  }

  return (
    <div className="mt-4">
      <dl className="grid grid-cols-3 gap-4">
        <div>
          <dt className="text-muted-foreground text-xs">Total objects</dt>
          <dd className="text-sm font-semibold">{info?.total_objects ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">Model</dt>
          <dd className="text-sm font-semibold">{info?.embedding_model ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">Source type</dt>
          <dd className="text-sm font-semibold">{info?.sync_mode ?? '—'}</dd>
        </div>
      </dl>
    </div>
  )
}

import { useLogs } from '@/api/logs'
import LogsTable from '@/pages/logs/LogsTable'
import { Link } from 'react-router-dom'

export default function LogsTab({ collection }: { collection: string }) {
  const { data: logs, isLoading } = useLogs(collection, 10)

  if (isLoading) {
    return <div className="mt-4 text-muted-foreground text-sm">Loading logs…</div>
  }

  return (
    <div className="mt-4 space-y-3">
      {(logs ?? []).length === 0 ? (
        <p className="text-muted-foreground text-sm">Nessuna sync eseguita per questa entity.</p>
      ) : (
        <LogsTable rows={logs ?? []} />
      )}
      <Link
        to={`/logs?collection=${encodeURIComponent(collection)}`}
        className="text-sm text-primary hover:underline"
      >
        Vedi tutti i log →
      </Link>
    </div>
  )
}

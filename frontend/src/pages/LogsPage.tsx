import { useState } from 'react'
import EntityDropdown from '@/components/EntityDropdown'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import LogsTable from './logs/LogsTable'
import { useLogs } from '@/api/logs'

export default function LogsPage() {
  const [collection, setCollection] = useState<string | null>(null)
  const logs = useLogs(collection)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">Logs</h1>
        <div className="flex items-center gap-2">
          <EntityDropdown value={collection} onChange={setCollection} />
          <Button
            variant="outline"
            size="sm"
            onClick={() => logs.refetch()}
            disabled={!collection || logs.isFetching}
          >
            {logs.isFetching ? 'Refreshing...' : 'Refresh'}
          </Button>
        </div>
      </div>

      {!collection && (
        <div className="text-sm text-muted-foreground py-8 text-center">
          Select an entity to view its sync history.
        </div>
      )}

      {collection && logs.isPending && (
        <div className="space-y-2">
          {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      )}

      {collection && logs.isError && (
        <div className="border border-destructive/30 bg-destructive/5 text-destructive p-4 rounded-md text-sm">
          Could not load logs. Check that the orchestrator container is running.
        </div>
      )}

      {collection && logs.data && logs.data.length === 0 && (
        <div className="text-center py-12">
          <h3 className="text-base font-semibold mb-1">No sync history</h3>
          <p className="text-sm text-muted-foreground">
            Run your first sync from the Settings page.
          </p>
        </div>
      )}

      {collection && logs.data && logs.data.length > 0 && (
        <div className="border rounded-md">
          <LogsTable rows={logs.data} />
        </div>
      )}
    </div>
  )
}

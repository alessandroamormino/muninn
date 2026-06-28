import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import EntityDropdown from '@/components/EntityDropdown'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import LogsTable from './logs/LogsTable'
import { useLogs } from '@/api/logs'

export default function LogsPage() {
  const { t } = useTranslation()
  const [collection, setCollection] = useState<string | null>(null)
  const logs = useLogs(collection)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">{t('logs.title')}</h1>
        <div className="flex items-center gap-2">
          <EntityDropdown value={collection} onChange={setCollection} />
          <Button
            variant="outline"
            size="sm"
            onClick={() => logs.refetch()}
            disabled={!collection || logs.isFetching}
          >
            {logs.isFetching ? t('logs.refreshing') : t('logs.refresh')}
          </Button>
        </div>
      </div>

      {!collection && (
        <div className="text-sm text-muted-foreground py-8 text-center">
          {t('logs.selectEntity')}
        </div>
      )}

      {collection && logs.isPending && (
        <div className="space-y-2">
          {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      )}

      {collection && logs.isError && (
        <div className="border border-destructive/30 bg-destructive/5 text-destructive p-4 rounded-md text-sm">
          {t('logs.errLoad')}
        </div>
      )}

      {collection && logs.data && logs.data.length === 0 && (
        <div className="text-center py-12">
          <h3 className="text-base font-semibold mb-1">{t('logs.noHistoryTitle')}</h3>
          <p className="text-sm text-muted-foreground">
            {t('logs.noHistoryHint')}
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

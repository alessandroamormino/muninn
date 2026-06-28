import { useTranslation } from 'react-i18next'
import { useLogs } from '@/api/logs'
import LogsTable from '@/pages/logs/LogsTable'
import { Link } from 'react-router'

export default function LogsTab({ collection }: { collection: string }) {
  const { t } = useTranslation()
  const { data: logs, isLoading } = useLogs(collection, 10)

  if (isLoading) {
    return <div className="mt-4 text-muted-foreground text-sm">{t('logsTab.loading')}</div>
  }

  return (
    <div className="mt-4 space-y-3">
      {(logs ?? []).length === 0 ? (
        <p className="text-muted-foreground text-sm">{t('logsTab.none')}</p>
      ) : (
        <LogsTable rows={logs ?? []} />
      )}
      <Link
        to={`/logs?collection=${encodeURIComponent(collection)}`}
        className="text-sm text-primary hover:underline"
      >
        {t('logsTab.seeAll')}
      </Link>
    </div>
  )
}

import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'

interface Props {
  status: string
}

const STATUS_STYLE: Record<string, { labelKey: string; className: string }> = {
  completed: { labelKey: 'logTable.stCompleted', className: 'bg-green-600/10 text-green-700 border-green-600/30' },
  failed: { labelKey: 'logTable.stFailed', className: 'bg-red-600/10 text-red-700 border-red-600/30' },
  skipped: { labelKey: 'logTable.stSkipped', className: 'bg-amber-500/10 text-amber-700 border-amber-500/30' },
  running: { labelKey: 'logTable.stRunning', className: 'bg-amber-500/10 text-amber-700 border-amber-500/30' },
}

export default function StatusBadge({ status }: Props) {
  const { t } = useTranslation()
  const s = STATUS_STYLE[status]
  return (
    <Badge variant="outline" className={s?.className ?? ''}>
      {s ? t(s.labelKey) : status}
    </Badge>
  )
}

import { Badge } from '@/components/ui/badge'

interface Props {
  status: string
}

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  completed: { label: 'Completed', className: 'bg-green-600/10 text-green-700 border-green-600/30' },
  failed: { label: 'Failed', className: 'bg-red-600/10 text-red-700 border-red-600/30' },
  skipped: { label: 'Skipped', className: 'bg-amber-500/10 text-amber-700 border-amber-500/30' },
  running: { label: 'Running', className: 'bg-amber-500/10 text-amber-700 border-amber-500/30' },
}

export default function StatusBadge({ status }: Props) {
  const s = STATUS_STYLE[status] ?? { label: status, className: '' }
  return (
    <Badge variant="outline" className={s.className}>
      {s.label}
    </Badge>
  )
}

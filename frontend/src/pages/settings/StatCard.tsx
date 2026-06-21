import { Card } from '@/components/ui/card'
import { Sparkline } from './Sparkline'

// Phase 27 — Resource Monitoring Dashboard.
// Dense dashboard tile: label above a big tabular-nums value, optional inline units,
// optional sparkline below. Fixed outer footprint (never data-dependent) per
// 27-UI-SPEC.md's Live-Update Affordance — no layout shift on every 2s poll tick.

interface Props {
  label: string
  value: string
  unit?: string
  sparklineData?: number[]
  className?: string
}

export function StatCard({ label, value, unit, sparklineData, className }: Props) {
  return (
    <Card className={`p-4 h-[120px] flex flex-col justify-between ${className ?? ''}`}>
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>
      <div>
        <span className="text-3xl font-semibold tabular-nums">{value}</span>
        {unit && <span className="text-base text-muted-foreground ml-1">{unit}</span>}
      </div>
      {sparklineData && (
        <div className="text-primary">
          <Sparkline data={sparklineData} />
        </div>
      )}
    </Card>
  )
}

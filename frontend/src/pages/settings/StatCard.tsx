import { Card } from '@/components/ui/card'
import { PixelGauge } from './PixelGauge'

// Phase 27 — Resource Monitoring Dashboard.
// Dense dashboard tile: label above a big tabular-nums value, plus a pixel-fill
// gauge (used vs capacity). Fixed outer footprint (never data-dependent) per
// 27-UI-SPEC.md's Live-Update Affordance — no layout shift on every 2s poll tick.
//
// The gauge is ALWAYS inside the card. `variant` controls layout:
//   'lg' — wide Stack Totals tile: a big gauge sits to the RIGHT of the number.
//   'sm' — narrow container tile: a full-width gauge strip sits UNDER the number.
// Both keep generous internal padding (p-5) and vertically centre their content.

interface Props {
  label: string
  value: string
  unit?: string
  /** Used/capacity ratio (0..1). When set, renders a dot-matrix PixelGauge. */
  fraction?: number
  variant?: 'sm' | 'lg'
  className?: string
}

export function StatCard({ label, value, unit, fraction, variant = 'sm', className }: Props) {
  const isLg = variant === 'lg'

  const Value = (
    <div className="leading-none">
      <span className={`${isLg ? 'text-4xl' : 'text-3xl'} font-semibold tabular-nums`}>
        {value}
      </span>
      {unit && <span className="text-base text-muted-foreground ml-1">{unit}</span>}
    </div>
  )

  return (
    <Card className={`p-5 h-[140px] overflow-hidden flex flex-col ${className ?? ''}`}>
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>

      {isLg ? (
        <div className="flex-1 flex items-center gap-6">
          {Value}
          {fraction !== undefined && (
            <PixelGauge
              fraction={fraction}
              cols={26}
              rows={6}
              cell={10}
              className="ml-auto shrink-0"
            />
          )}
        </div>
      ) : (
        <div className="flex-1 flex flex-col justify-center gap-3">
          {Value}
          {fraction !== undefined && <PixelGauge fraction={fraction} cols={26} rows={5} cell={6} />}
        </div>
      )}
    </Card>
  )
}

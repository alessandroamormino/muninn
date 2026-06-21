import { Card } from '@/components/ui/card'
import { PixelGauge } from './PixelGauge'

// Phase 27 — Resource Monitoring Dashboard.
// Dense dashboard tile: label on top, then the number with a pixel-fill gauge
// (used vs capacity) to its RIGHT. The gauge fills all the width left over beside
// the number (flex-1) with a small gap, so its squares stretch to occupy the card
// rather than floating at the edge. Fixed outer footprint per 27-UI-SPEC.md's
// Live-Update Affordance — no layout shift on every 2s poll tick.
//
// `variant` tunes sizing only (layout is the same number-left / gauge-right for all):
//   'lg' — wide Stack Totals tile: bigger number + taller gauge. May show `secondary`
//          (e.g. "/ 10.7 GiB") so the capacity reads as "used / total" in the card.
//   'sm' — narrow container tile.

interface Props {
  label: string
  value: string
  unit?: string
  /** Small muted suffix after the value+unit, e.g. "/ 10.7 GiB". */
  secondary?: string
  /** Used/capacity ratio (0..1). When set, renders a dot-matrix PixelGauge. */
  fraction?: number
  variant?: 'sm' | 'lg'
  className?: string
}

export function StatCard({
  label,
  value,
  unit,
  secondary,
  fraction,
  variant = 'sm',
  className,
}: Props) {
  const isLg = variant === 'lg'

  return (
    <Card
      className={`p-5 ${isLg ? 'h-[140px]' : 'h-[120px]'} overflow-hidden flex flex-col ${
        className ?? ''
      }`}
    >
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>

      <div className="flex-1 flex items-center gap-4 min-w-0">
        <div className="shrink-0 leading-none whitespace-nowrap">
          <span className={`${isLg ? 'text-4xl' : 'text-3xl'} font-semibold tabular-nums`}>
            {value}
          </span>
          {unit && <span className="text-base text-muted-foreground ml-1">{unit}</span>}
          {secondary && (
            <span className="text-base text-muted-foreground ml-1 tabular-nums">{secondary}</span>
          )}
        </div>
        {fraction !== undefined && (
          <PixelGauge
            fraction={fraction}
            cols={isLg ? 40 : 22}
            rows={isLg ? 6 : 5}
            className={`flex-1 min-w-0 ${isLg ? 'h-14' : 'h-8'}`}
          />
        )}
      </div>
    </Card>
  )
}

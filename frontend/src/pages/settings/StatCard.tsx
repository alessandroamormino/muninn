import { Card } from '@/components/ui/card'
import { PixelGauge } from './PixelGauge'

// Phase 27 — Resource Monitoring Dashboard.
// Dense dashboard tile: label above a big tabular-nums value, optional inline units,
// optional pixel-fill gauge (used vs capacity). Fixed outer footprint (never
// data-dependent) per 27-UI-SPEC.md's Live-Update Affordance — no layout shift on
// every 2s poll tick.
//
// The gauge is ALWAYS inside the card. `gauge` controls placement:
//   'below' (default) — full-width strip under the number; good for narrow tiles.
//   'right'           — fixed-size block to the right of the number; good for the
//                       wide Stack Totals tiles where a full-width strip stretches.
// Both pass an explicitly bounded height to PixelGauge so it can never overflow.

interface Props {
  label: string
  value: string
  unit?: string
  /** Used/capacity ratio (0..1). When set, renders a dot-matrix PixelGauge. */
  fraction?: number
  gauge?: 'below' | 'right'
  className?: string
}

export function StatCard({ label, value, unit, fraction, gauge = 'below', className }: Props) {
  const Value = (
    <div className="leading-none">
      <span className="text-3xl font-semibold tabular-nums">{value}</span>
      {unit && <span className="text-base text-muted-foreground ml-1">{unit}</span>}
    </div>
  )

  return (
    <Card className={`p-4 h-[120px] flex flex-col justify-between overflow-hidden ${className ?? ''}`}>
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>

      {gauge === 'right' ? (
        <div className="flex items-end justify-between gap-4">
          {Value}
          {fraction !== undefined && (
            <PixelGauge fraction={fraction} className="h-12 w-40 shrink-0" />
          )}
        </div>
      ) : (
        <>
          {Value}
          {fraction !== undefined && <PixelGauge fraction={fraction} className="h-7 w-full" />}
        </>
      )}
    </Card>
  )
}

// Phase 27 follow-up — dot-matrix "fill" gauge (Nothing-phone aesthetic).
// A grid of pixel cells that light up to represent `fraction` (used / capacity),
// e.g. RAM used vs limit or CPU% vs 100. Replaces the time-series Sparkline for
// metrics that have a meaningful maximum — an instantaneous level reads more
// directly than a scrolling line. Pure CSS grid: no chart library.
//
// Fill order is left-to-right by column, bottom-up within each column, so the lit
// front rises like a filling tank. Colour stays monochrome (foreground dots on a
// faint track) until the level enters warn/danger bands, where it picks up amber /
// the destructive red as a Nothing-style accent under pressure.
//
// SIZING: the cell grid divides the container in BOTH axes (grid-template-rows +
// grid-template-columns), so the gauge fills whatever box `className` gives it and
// never overflows. The caller MUST set a bounded height (and usually width) via
// `className` — e.g. `h-7 w-full` below a value, or `h-12 w-40` beside it. Do NOT
// use aspect-square cells: on a wide card that scales each cell to the column width
// and blows the gauge far past the card.

interface Props {
  /** 0..1; values outside the range are clamped. NaN/Infinity treated as 0. */
  fraction: number
  cols?: number
  rows?: number
  /** Tailwind sizing for the gauge box — MUST include a bounded height. */
  className?: string
}

export function PixelGauge({ fraction, cols = 20, rows = 5, className }: Props) {
  const frac = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0))
  const total = cols * rows
  const lit = Math.round(frac * total)

  const litClass =
    frac >= 0.85 ? 'bg-destructive' : frac >= 0.7 ? 'bg-amber-500' : 'bg-foreground'

  const cells = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      // Column-major, bottom-up: the partial column at the front fills from the
      // bottom edge, like a liquid level.
      const order = c * rows + (rows - 1 - r)
      const on = order < lit
      cells.push(
        <span
          key={`${r}-${c}`}
          className={`rounded-[1px] transition-colors duration-300 ${
            on ? litClass : 'bg-foreground/10'
          }`}
        />,
      )
    }
  }

  return (
    <div
      className={`grid gap-[2px] ${className ?? ''}`}
      style={{
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
      }}
      role="meter"
      aria-valuenow={Math.round(frac * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      {cells}
    </div>
  )
}

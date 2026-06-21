// Phase 27 follow-up — dot-matrix "fill" gauge (Nothing-phone aesthetic).
// A grid of pixel cells that light up to represent `fraction` (used / capacity),
// e.g. RAM used vs limit or CPU% vs 100. Replaces the time-series Sparkline for
// metrics that have a meaningful maximum — an instantaneous level reads more
// directly than a scrolling line. Pure CSS grid: no chart library.
//
// Fill order is left-to-right by column, bottom-up within each column, so the lit
// front rises like a filling tank. Colour stays monochrome (foreground dots on a
// faint /20 track that is visible even at ~0% fill) until the level enters
// warn/danger bands, where it picks up amber / the destructive red as a
// Nothing-style accent under pressure.
//
// SIZING: cells are a FIXED square `cell` px — the grid sizes itself to its content
// (cols×cell), never stretching to the card width. This keeps every pixel the same
// crisp size regardless of how wide the card is, and the gauge can never overflow
// (it is always smaller than the card box that contains it).

interface Props {
  /** 0..1; values outside the range are clamped. NaN/Infinity treated as 0. */
  fraction: number
  cols?: number
  rows?: number
  /** Square cell edge in px. */
  cell?: number
  /** Gap between cells in px. */
  gap?: number
  className?: string
}

export function PixelGauge({
  fraction,
  cols = 26,
  rows = 5,
  cell = 6,
  gap = 2,
  className,
}: Props) {
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
            on ? litClass : 'bg-foreground/20'
          }`}
        />,
      )
    }
  }

  return (
    <div
      className={`grid ${className ?? ''}`}
      style={{
        gridTemplateColumns: `repeat(${cols}, ${cell}px)`,
        gridAutoRows: `${cell}px`,
        gap: `${gap}px`,
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

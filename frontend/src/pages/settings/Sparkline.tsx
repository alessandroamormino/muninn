import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts'

// Phase 27 — Resource Monitoring Dashboard.
// Direct Recharts composition (shadcn/ui does not wrap Recharts; no shadcn Chart
// block exists in this repo). Zero chrome (no axes/grid/legend/tooltip) — a
// dashboard accent, not a "Grafana-style" chart (D-01).
export function Sparkline({ data }: { data: number[] }) {
  const points = data.map((v, i) => ({ i, v }))
  return (
    <ResponsiveContainer width="100%" height={40}>
      <LineChart data={points}>
        <YAxis hide domain={['auto', 'auto']} />
        <Line
          type="monotone"
          dataKey="v"
          stroke="currentColor"
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useMetrics, type ContainerMetric } from '@/api/metrics'
import { StatCard } from './settings/StatCard'
import { StatusDot } from './settings/StatusDot'
import { formatBytes, formatCpu, formatUptime } from '@/lib/formatMetrics'

// Phase 27 — Resource Monitoring Dashboard (the NEW Settings page — monitoring only).
// The old entity-management page now lives at EntitiesPage.tsx / route /entities.
// Whole-page degraded state on isError (D-08/SC-27-6) — the single GET /metrics call
// returns all containers or none, never a partial-failure matrix.
//
// Phase 27 follow-up: CPU/RAM tiles show an instantaneous dot-matrix PixelGauge
// (used vs capacity) instead of a scrolling sparkline — no client-side ring buffer.

/** RAM used/limit as a 0..1 fraction; 0 when the limit is unknown. */
function memFraction(used: number, limit: number): number {
  return limit > 0 ? used / limit : 0
}

/** CPU% as a 0..1 fraction of a single core (the gauge clamps multi-core spikes). */
function cpuFraction(pct: number): number {
  return pct / 100
}

function ContainerCard({ name, metric }: { name: string; metric: ContainerMetric }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="font-semibold text-sm">{name}</span>
        <StatusDot status={metric.status} health={metric.health} />
      </div>
      <div className="grid grid-cols-2 gap-3 mb-3">
        <StatCard
          label="CPU%"
          value={formatCpu(metric.cpu_pct).replace('%', '')}
          unit="%"
          fraction={cpuFraction(metric.cpu_pct)}
        />
        <StatCard
          label="RAM"
          value={formatBytes(metric.mem_used).split(' ')[0]}
          unit={formatBytes(metric.mem_used).split(' ')[1]}
          fraction={memFraction(metric.mem_used, metric.mem_limit)}
        />
      </div>
      <p className="text-xs text-muted-foreground mb-3 tabular-nums">
        RAM {formatBytes(metric.mem_used)} of {formatBytes(metric.mem_limit)}
      </p>
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted-foreground">Net I/O</dt>
          <dd className="tabular-nums">
            ↓ {formatBytes(metric.net_rx)}  ↑ {formatBytes(metric.net_tx)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Block I/O</dt>
          <dd className="tabular-nums">
            R {formatBytes(metric.blk_read)}  W {formatBytes(metric.blk_write)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Uptime</dt>
          <dd className="tabular-nums">{formatUptime(metric.uptime_s)}</dd>
        </div>
      </dl>
    </Card>
  )
}

export default function SettingsPage() {
  const { data, isError, isLoading } = useMetrics()

  return (
    <Card className="p-6">
      <div className="flex items-center gap-2 mb-6">
        <h2 className="text-xl font-semibold">Resource Monitoring</h2>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="text-muted-foreground text-sm cursor-help">ⓘ</span>
          </TooltipTrigger>
          <TooltipContent side="right" className="max-w-xs">
            Su macOS questi numeri riflettono l'allocazione della VM di Docker Desktop, non
            l'hardware fisico del Mac.
          </TooltipContent>
        </Tooltip>
      </div>

      {isError && (
        <div className="flex flex-col items-center justify-center text-center px-6 py-16">
          <h3 className="text-base font-semibold mb-1">Monitoring non disponibile</h3>
          <p className="text-sm text-muted-foreground max-w-sm">
            Impossibile leggere le statistiche Docker. Verifica che il socket sia montato
            e che l'orchestrator abbia i permessi necessari.
          </p>
        </div>
      )}

      {!isError && isLoading && (
        <div className="grid grid-cols-2 gap-4">
          <Skeleton className="h-[140px] rounded-md" />
          <Skeleton className="h-[140px] rounded-md" />
        </div>
      )}

      {!isError && data && (
        <div className="flex flex-col gap-8">
          <section>
            <h3 className="text-sm font-semibold text-muted-foreground mb-2">Stack Totals</h3>
            <div className="grid grid-cols-2 gap-4">
              <StatCard
                label="CPU% (total)"
                value={formatCpu(data.totals.cpu_pct).replace('%', '')}
                unit="%"
                fraction={cpuFraction(data.totals.cpu_pct)}
                variant="lg"
              />
              <StatCard
                label="RAM (total)"
                value={formatBytes(data.totals.mem_used).split(' ')[0]}
                unit={formatBytes(data.totals.mem_used).split(' ')[1]}
                fraction={memFraction(data.totals.mem_used, data.totals.mem_limit)}
                variant="lg"
              />
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              of {formatBytes(data.totals.mem_limit)} host RAM
            </p>
          </section>

          <section>
            <h3 className="text-sm font-semibold text-muted-foreground mb-2">Containers</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {data.containers.map((c) => (
                <ContainerCard key={c.name} name={c.name} metric={c} />
              ))}
            </div>
          </section>
        </div>
      )}
    </Card>
  )
}

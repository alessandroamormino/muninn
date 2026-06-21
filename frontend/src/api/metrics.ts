import { useQuery } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

// Phase 27 — Resource Monitoring Dashboard.
// useMetrics() polls GET /api/metrics every 2s (D-02, fixed interval, no backoff —
// admin-only, low-cardinality dashboard). retry: 1 surfaces "unavailable" quickly
// rather than retry-storming (D-08).

export interface ContainerMetric {
  name: string
  cpu_pct: number
  mem_used: number
  mem_limit: number
  net_rx: number
  net_tx: number
  blk_read: number
  blk_write: number
  uptime_s: number | null
  status: string
  health: string | null
}

export interface MetricsResponse {
  containers: ContainerMetric[]
  totals: { cpu_pct: number; mem_used: number; mem_limit: number }
}

export function useMetrics() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  const fetchJson = createApiClient(token, on401 as () => void)

  return useQuery({
    queryKey: ['metrics'],
    queryFn: () => fetchJson<MetricsResponse>('/api/metrics'),
    refetchInterval: 2_000,
    retry: 1,
    staleTime: 0,
  })
}

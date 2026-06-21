// Phase 27 — Resource Monitoring Dashboard.
// Centralized formatters per 27-UI-SPEC.md "Units & Formatting" — small focused
// functions, mirrors this codebase's existing convention (SyncTab.tsx's local
// formatEta/phaseLabel helpers) rather than a heavyweight i18n/format library.

const MIB = 1024 * 1024
const GIB = 1024 * MIB

/**
 * Binary-unit byte formatter (MiB/GiB, never decimal MB/GB — Docker's own API
 * uses binary byte counts). Threshold exactly 1024 MiB: below it, MiB with 0
 * decimals; at/above it, GiB with 1 decimal.
 */
export function formatBytes(bytes: number): string {
  const mib = bytes / MIB
  if (mib < 1024) {
    return `${Math.round(mib)} MiB`
  }
  const gib = bytes / GIB
  return `${gib.toFixed(1)} GiB`
}

/** CPU% — one decimal place, '%' suffix. */
export function formatCpu(pct: number): string {
  return `${pct.toFixed(1)}%`
}

/**
 * Humanized uptime — largest 2 units only, no seconds once >= 1 hour:
 * <60s -> "42s"; <60min -> "5m 12s"; <24h -> "3h 12m"; >=24h -> "2d 4h".
 * Returns "—" when uptime is unknown (container not running / no StartedAt).
 */
export function formatUptime(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'

  const s = Math.floor(seconds)
  if (s < 60) return `${s}s`

  const minutes = Math.floor(s / 60)
  const remSeconds = s % 60
  if (minutes < 60) return `${minutes}m ${remSeconds}s`

  const hours = Math.floor(minutes / 60)
  const remMinutes = minutes % 60
  if (hours < 24) return `${hours}h ${remMinutes}m`

  const days = Math.floor(hours / 24)
  const remHours = hours % 24
  return `${days}d ${remHours}h`
}

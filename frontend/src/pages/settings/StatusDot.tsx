import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

// Phase 27 — Resource Monitoring Dashboard.
// Maps Docker (status, health) to a colored dot per 27-UI-SPEC.md's Health/Status
// Badge table. health: null (no healthcheck defined) renders identical to running —
// absence of a healthcheck is normal, not an alarming "Unknown" state (RESEARCH A1).

interface Props {
  status: string
  health: string | null
}

export function StatusDot({ status, health }: Props) {
  // running + unhealthy — steady-state warning, solid amber (no animation)
  if (status === 'running' && health === 'unhealthy') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="h-2 w-2 rounded-full bg-amber-500 shrink-0 inline-flex" />
        </TooltipTrigger>
        <TooltipContent side="top">Unhealthy</TooltipContent>
      </Tooltip>
    )
  }

  // running + starting healthcheck — sky dot with ping halo (in-flight)
  if (status === 'running' && health === 'starting') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75 [animation-duration:1.8s]" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-500" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">Starting…</TooltipContent>
      </Tooltip>
    )
  }

  // restarting — amber with gentle pulse (recurring-but-not-job-bound, not ping)
  if (status === 'restarting') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="h-2 w-2 rounded-full bg-amber-500 shrink-0 inline-flex animate-pulse" />
        </TooltipTrigger>
        <TooltipContent side="top">Restarting</TooltipContent>
      </Tooltip>
    )
  }

  // exited / dead — solid red
  if (status === 'exited' || status === 'dead') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="h-2 w-2 rounded-full bg-red-500 shrink-0 inline-flex" />
        </TooltipTrigger>
        <TooltipContent side="top">{status === 'exited' ? 'Exited' : 'Dead'}</TooltipContent>
      </Tooltip>
    )
  }

  // running + healthy, or running + no healthcheck (health: null) — default solid emerald
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0 inline-flex" />
      </TooltipTrigger>
      <TooltipContent side="top">Running</TooltipContent>
    </Tooltip>
  )
}

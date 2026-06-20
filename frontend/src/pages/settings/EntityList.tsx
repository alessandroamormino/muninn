import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { CollectionItem } from '@/api/collections'
import { useSyncStatus } from '@/api/syncStatus'
import { useUnloadEntity, useLoadEntity, useUnloadProgress } from '@/api/entities'

const SOURCE_BADGE: Record<string, { label: string; className: string }> = {
  csv:      { label: 'CSV',      className: 'bg-blue-100 text-blue-700' },
  rest_api: { label: 'REST',     className: 'bg-purple-100 text-purple-700' },
  mysql:    { label: 'MySQL',    className: 'bg-orange-100 text-orange-700' },
  json:     { label: 'JSON',     className: 'bg-green-100 text-green-700' },
}

function SourceBadge({ type }: { type: string }) {
  const badge = SOURCE_BADGE[type] ?? { label: type, className: 'bg-gray-100 text-gray-600' }
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${badge.className}`}>
      {badge.label}
    </span>
  )
}

function SyncDot({ collection }: { collection: string }) {
  const { data } = useSyncStatus()
  if (!data) return null

  const isRunning = data.status === 'running' && data.collection === collection
  const lastForThis = data.last_run?.collection === collection
  const isDone = lastForThis && data.status === 'completed'
  const isFailed = lastForThis && data.status === 'failed'

  if (isRunning) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75 [animation-duration:1.8s]" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-500" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="right">Sync in corso…</TooltipContent>
      </Tooltip>
    )
  }
  if (isDone) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0 inline-flex" />
        </TooltipTrigger>
        <TooltipContent side="right">Ultima sync completata</TooltipContent>
      </Tooltip>
    )
  }
  if (isFailed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="h-2 w-2 rounded-full bg-red-500 shrink-0 inline-flex" />
        </TooltipTrigger>
        <TooltipContent side="right">Ultima sync fallita</TooltipContent>
      </Tooltip>
    )
  }
  return null
}

// Phase 26 — per-entity load/unload toggle. Hooks are called at the top of THIS
// component's render (one instance per row), never inside a .map() callback or an
// event handler (React Rules of Hooks, D-15). The onCheckedChange handler only calls
// the stable `mutate` callbacks returned by the hooks.
function EntityRowToggle({ name, status }: { name: string; status?: 'active' | 'unloaded' }) {
  const unload = useUnloadEntity()
  const load = useLoadEntity()
  const { data: progress } = useUnloadProgress()

  const isForThis = progress?.entity === name
  const inFlight = isForThis && ['snapshotting', 'deleting', 'restoring'].includes(progress?.phase ?? '')
  const failed = isForThis && progress?.phase === 'failed'
  const busy = inFlight || unload.isPending || load.isPending
  const checked = (status ?? 'active') === 'active'

  return (
    <div className="flex items-center gap-1.5 shrink-0">
      {status === 'unloaded' && !inFlight && (
        <span className="text-[9px] font-semibold px-1 py-0.5 rounded bg-amber-100 text-amber-700 leading-none">
          unloaded
        </span>
      )}
      {inFlight && (
        <span className="text-[10px] text-muted-foreground">{progress?.phase}…</span>
      )}
      {failed && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="text-[10px] text-red-500 cursor-help">failed</span>
          </TooltipTrigger>
          <TooltipContent side="left">{progress?.error ?? 'Operation failed'}</TooltipContent>
        </Tooltip>
      )}
      <Switch
        checked={checked}
        disabled={busy}
        onCheckedChange={(next) => (next ? load.mutate(name) : unload.mutate(name))}
        aria-label={checked ? `Unload ${name}` : `Load ${name}`}
      />
    </div>
  )
}

interface Props {
  collections: CollectionItem[]
  selected: string | null
  onSelect: (c: string) => void
  onCreateCsv: () => void
  onCreateRestApi: () => void
  onCreateMySQL: () => void
}

export default function EntityList({ collections, selected, onSelect, onCreateCsv, onCreateRestApi, onCreateMySQL }: Props) {
  return (
    <div className="flex flex-col h-full">
      {collections.length === 0 ? (
        <div className="text-sm text-muted-foreground py-4 flex-1">
          <div className="font-medium mb-1">No entities configured</div>
          <div>Add your first entity by uploading a CSV or connecting a data source.</div>
        </div>
      ) : (
        <ul className="flex-1 space-y-1 overflow-y-auto">
          {collections.map((c) => (
            <li key={c.name} className="flex items-center gap-1">
              <button
                onClick={() => onSelect(c.name)}
                className={`flex-1 min-w-0 text-left px-3 py-2.5 rounded-md text-sm flex items-center justify-between gap-2 overflow-visible ${
                  selected === c.name ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                }`}
              >
                <span className="flex items-center gap-1.5 min-w-0">
                  <SyncDot collection={c.name} />
                  <span className="truncate">{c.name}</span>
                  {c.is_global && (
                    <span className="text-[9px] font-semibold px-1 py-0.5 rounded bg-amber-100 text-amber-700 leading-none shrink-0">
                      default
                    </span>
                  )}
                </span>
                <SourceBadge type={c.source_type} />
              </button>
              <EntityRowToggle name={c.name} status={c.status} />
            </li>
          ))}
        </ul>
      )}
      <div className="space-y-2 pt-3 border-t">
        <Button size="sm" className="w-full" onClick={onCreateCsv}>Upload CSV</Button>
        <Button size="sm" variant="outline" className="w-full" onClick={onCreateRestApi}>Add REST API</Button>
        <Button size="sm" variant="outline" className="w-full" onClick={onCreateMySQL}>Add MySQL</Button>
      </div>
    </div>
  )
}

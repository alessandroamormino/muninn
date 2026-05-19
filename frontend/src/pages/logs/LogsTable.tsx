import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import StatusBadge from './StatusBadge'
import type { LogRun } from '@/api/logs'

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export default function LogsTable({ rows }: { rows: LogRun[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Duration</TableHead>
          <TableHead className="text-right">Inserted</TableHead>
          <TableHead className="text-right">Updated</TableHead>
          <TableHead className="text-right">Skipped</TableHead>
          <TableHead className="text-right">Errors</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id}>
            <TableCell className="font-mono text-xs">{formatTime(r.started_at)}</TableCell>
            <TableCell className="text-xs">{r.type}</TableCell>
            <TableCell className="text-right font-mono text-xs">{formatDuration(r.took_ms)}</TableCell>
            <TableCell className="text-right font-mono text-xs">{r.inserted}</TableCell>
            <TableCell className="text-right font-mono text-xs">{r.updated}</TableCell>
            <TableCell className="text-right font-mono text-xs">{r.skipped_records}</TableCell>
            <TableCell className="text-right font-mono text-xs">{r.errors}</TableCell>
            <TableCell>
              <StatusBadge status={r.status} />
              {r.error_message && (
                <div className="text-xs text-destructive mt-1 max-w-md truncate" title={r.error_message}>
                  {r.error_message}
                </div>
              )}
              {r.reason && (
                <div className="text-xs text-muted-foreground mt-1">
                  {r.reason}
                </div>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

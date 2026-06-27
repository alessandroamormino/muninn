import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  useTriggerBackup, useBackups, useRestoreBackup, useDeleteBackup, useBackupProgress,
  type BackupEntry,
} from '@/api/backup'
import { toast } from 'sonner'

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

type Confirm =
  | { kind: 'restore'; bundle: BackupEntry }
  | { kind: 'delete'; bundle: BackupEntry }
  | null

export default function BackupTab({ collection }: { collection: string }) {
  const trigger = useTriggerBackup()
  const restore = useRestoreBackup()
  const del = useDeleteBackup()
  const { data: catalog } = useBackups()
  const { data: progress } = useBackupProgress()
  const [confirm, setConfirm] = useState<Confirm>(null)
  const qc = useQueryClient()

  const rows = Object.values(catalog ?? {})
    .filter((b) => b.collection === collection)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))

  const isForThis = progress?.collection === collection
  const phase = isForThis ? progress?.phase : undefined
  const inFlight = ['snapshotting', 'uploading', 'restoring'].includes(phase ?? '')
  const busy = inFlight || trigger.isPending || restore.isPending || del.isPending

  // Only surface a terminal banner for an op we actually watched run this
  // session — a stale `done` left on the server must NOT reappear on reload.
  const sawInFlight = useRef(false)
  useEffect(() => {
    if (inFlight) sawInFlight.current = true
  }, [inFlight])

  // When the watched op reaches a terminal phase, refresh the catalog so the
  // new bundle (or its removal) shows without a manual page reload.
  useEffect(() => {
    if (sawInFlight.current && (phase === 'done' || phase === 'failed')) {
      qc.invalidateQueries({ queryKey: ['backups'] })
    }
  }, [phase, qc])

  const showDone = sawInFlight.current && (phase === 'done' || phase === 'failed')

  return (
    <div className="mt-4 space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">
          Off-host backup of <code className="font-mono">{collection}</code> (Qdrant snapshot
          + state set) to the configured S3 bucket. Restore overwrites the live collection.
        </p>
        <div className="mt-4">
          <Button
            disabled={busy}
            onClick={() =>
              trigger.mutate(collection, {
                onSuccess: () => toast.success('Backup started in background.'),
                onError: (e: Error) => toast.error(e.message),
              })
            }
          >
            {trigger.isPending ? 'Starting…' : 'Create backup'}
          </Button>
        </div>
      </div>

      {(inFlight || showDone) && (
        <div className="rounded-lg border bg-muted/40 p-3 text-sm flex items-center gap-2">
          {inFlight && (
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-sky-500" />
            </span>
          )}
          {showDone && phase === 'done' && (
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
          )}
          {showDone && phase === 'failed' && (
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
          )}
          <span className="font-medium">{phase}</span>
          {progress?.error && <span className="text-red-500">— {progress.error}</span>}
        </div>
      )}

      <div>
        <h3 className="text-sm font-semibold mb-2">Backups</h3>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No backups yet for this collection.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Created</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Snapshot</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((b) => (
                <TableRow key={b.bundle_id}>
                  <TableCell className="tabular-nums">
                    {new Date(b.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell className="tabular-nums">{formatBytes(b.size_bytes)}</TableCell>
                  <TableCell className="font-mono text-xs truncate max-w-[14rem]">
                    {b.snapshot_name}
                  </TableCell>
                  <TableCell className="text-right space-x-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => setConfirm({ kind: 'restore', bundle: b })}
                    >
                      Restore
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy}
                      onClick={() => setConfirm({ kind: 'delete', bundle: b })}
                    >
                      Delete
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* D-08 / T-28-04-02: restore and delete are gated behind an explicit confirm dialog. */}
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          {confirm?.kind === 'restore' && (
            <>
              <DialogHeader>
                <DialogTitle>Restore this backup?</DialogTitle>
                <DialogDescription>
                  This <strong>overwrites the live collection</strong>{' '}
                  <code className="font-mono">{collection}</code> with the snapshot from{' '}
                  {new Date(confirm.bundle.created_at).toLocaleString()}. This cannot be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirm(null)}>Cancel</Button>
                <Button
                  onClick={() => {
                    const bundle = confirm.bundle
                    setConfirm(null)
                    restore.mutate(
                      { name: collection, bundleId: bundle.bundle_id },
                      {
                        onSuccess: () => toast.success('Restore started in background.'),
                        onError: (e: Error) => toast.error(e.message),
                      },
                    )
                  }}
                >
                  Restore (overwrite)
                </Button>
              </DialogFooter>
            </>
          )}
          {confirm?.kind === 'delete' && (
            <>
              <DialogHeader>
                <DialogTitle>Delete this backup?</DialogTitle>
                <DialogDescription>
                  Permanently removes the bundle from the bucket and the catalog. This cannot be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirm(null)}>Cancel</Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    const bundle = confirm.bundle
                    setConfirm(null)
                    del.mutate(bundle.bundle_id, {
                      onSuccess: () => toast.success('Backup deleted.'),
                      onError: (e: Error) => toast.error(e.message),
                    })
                  }}
                >
                  Delete
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

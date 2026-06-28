import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
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
          {t('backup.desc', { collection })}
        </p>
        <div className="mt-4">
          <Button
            disabled={busy}
            onClick={() =>
              trigger.mutate(collection, {
                onSuccess: () => toast.success(t('backup.started')),
                onError: (e: Error) => toast.error(e.message),
              })
            }
          >
            {trigger.isPending ? t('backup.starting') : t('backup.create')}
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
        <h3 className="text-sm font-semibold mb-2">{t('backup.title')}</h3>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('backup.none')}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('backup.colCreated')}</TableHead>
                <TableHead>{t('backup.colSize')}</TableHead>
                <TableHead>{t('backup.colSnapshot')}</TableHead>
                <TableHead className="text-right">{t('backup.colActions')}</TableHead>
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
                      {t('backup.restore')}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy}
                      onClick={() => setConfirm({ kind: 'delete', bundle: b })}
                    >
                      {t('common.delete')}
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
                <DialogTitle>{t('backup.restoreTitle')}</DialogTitle>
                <DialogDescription>
                  {t('backup.restoreDesc', { collection, date: new Date(confirm.bundle.created_at).toLocaleString() })}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirm(null)}>{t('common.cancel')}</Button>
                <Button
                  onClick={() => {
                    const bundle = confirm.bundle
                    setConfirm(null)
                    restore.mutate(
                      { name: collection, bundleId: bundle.bundle_id },
                      {
                        onSuccess: () => toast.success(t('backup.restoreStarted')),
                        onError: (e: Error) => toast.error(e.message),
                      },
                    )
                  }}
                >
                  {t('backup.restoreConfirm')}
                </Button>
              </DialogFooter>
            </>
          )}
          {confirm?.kind === 'delete' && (
            <>
              <DialogHeader>
                <DialogTitle>{t('backup.deleteTitle')}</DialogTitle>
                <DialogDescription>
                  {t('backup.deleteDesc')}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirm(null)}>{t('common.cancel')}</Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    const bundle = confirm.bundle
                    setConfirm(null)
                    del.mutate(bundle.bundle_id, {
                      onSuccess: () => toast.success(t('backup.deleted')),
                      onError: (e: Error) => toast.error(e.message),
                    })
                  }}
                >
                  {t('common.delete')}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

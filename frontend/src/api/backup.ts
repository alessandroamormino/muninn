import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createApiClient } from './fetchJson'
import { useAuth } from '../context/AuthContext'

// Phase 28 — off-host backup / restore.
// backup  = Qdrant snapshot + state set -> S3-compatible bucket (BAK-01).
// restore = download snapshot -> restore_collection (no re-embedding, BAK-02, D-08 destructive).
// Both run as backend BackgroundTasks; progress is polled via useBackupProgress.

export interface BackupEntry {
  bundle_id: string
  collection: string
  snapshot_name: string
  created_at: string
  size_bytes: number
}

// GET /backup returns the catalog dict keyed by bundle_id.
export type BackupCatalog = Record<string, BackupEntry>

export interface BackupProgress {
  collection?: string
  // 'snapshotting' | 'uploading' | 'restoring' | 'done' | 'failed'
  phase?: string
  bundle_id?: string
  error?: string
}

function useApi() {
  const { token } = useAuth()
  const on401 = () => (window as unknown as { __on401?: () => void }).__on401?.()
  return createApiClient(token, on401 as () => void)
}

export function useTriggerBackup() {
  const qc = useQueryClient()
  const fetchJson = useApi()
  return useMutation({
    mutationFn: (name: string) =>
      fetchJson<{ status: string }>(
        `/api/backup/${encodeURIComponent(name)}`,
        { method: 'POST' }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backups'] })
      qc.invalidateQueries({ queryKey: ['backup-progress'] })
    },
  })
}

export function useBackups() {
  const fetchJson = useApi()
  return useQuery({
    queryKey: ['backups'],
    queryFn: () => fetchJson<BackupCatalog>('/api/backup'),
  })
}

export function useRestoreBackup() {
  const qc = useQueryClient()
  const fetchJson = useApi()
  return useMutation({
    // D-08: destructive — confirm=true is mandatory (backend returns 400 without it).
    mutationFn: ({ name, bundleId }: { name: string; bundleId: string }) =>
      fetchJson<{ status: string }>(
        `/api/backup/${encodeURIComponent(name)}/restore?bundle_id=${encodeURIComponent(bundleId)}&confirm=true`,
        { method: 'POST' }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backup-progress'] }),
  })
}

export function useDeleteBackup() {
  const qc = useQueryClient()
  const fetchJson = useApi()
  return useMutation({
    mutationFn: (bundleId: string) =>
      fetchJson<{ status: string }>(
        `/api/backup/${encodeURIComponent(bundleId)}`,
        { method: 'DELETE' }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backups'] }),
  })
}

export function useBackupProgress() {
  const fetchJson = useApi()
  return useQuery({
    queryKey: ['backup-progress'],
    queryFn: () => fetchJson<BackupProgress>('/api/backup/status'),
    refetchInterval: 1500,
  })
}

import { useRef, useCallback } from 'react'

// Phase 27 — Resource Monitoring Dashboard.
// Client-side ~60s ring buffer for sparkline history (D-01/D-02). useRef, NEVER
// useState — the useQuery re-render on each 2s poll already triggers the repaint;
// pushing into useState here would cause a redundant re-render storm. Mirrors this
// codebase's established convention in graph/useVisNetwork.ts (STATE.md decision [11-05]).

const BUFFER_SIZE = 30 // ~60s at 2s polling interval

export function useMetricsBuffer() {
  const buffersRef = useRef<Map<string, number[]>>(new Map())

  const push = useCallback((key: string, value: number) => {
    const arr = buffersRef.current.get(key) ?? []
    arr.push(value)
    if (arr.length > BUFFER_SIZE) arr.shift()
    buffersRef.current.set(key, arr)
  }, [])

  const get = useCallback((key: string) => buffersRef.current.get(key) ?? [], [])

  return { push, get }
}

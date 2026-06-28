import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { SearchResult } from '@/api/search'

const TITLE_FIELD_HINTS = ['description', 'descrizione', 'nome', 'name', 'title', 'titolo', 'label']
const STATUS_FIELD_HINTS = ['status', 'stato']

function pickTitle(props: Record<string, unknown>): [string, string] {
  const keys = Object.keys(props)
  const hint = keys.find((k) => TITLE_FIELD_HINTS.includes(k.toLowerCase()))
  const key = hint ?? keys[0] ?? ''
  const val = key ? String(props[key] ?? '—') : '—'
  return [key, val || '—']
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export default function ResultCard({ result }: { result: SearchResult }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const { _score, ...props } = result

  const [titleKey, titleVal] = pickTitle(props)

  const statusKey = Object.keys(props).find((k) =>
    STATUS_FIELD_HINTS.includes(k.toLowerCase())
  )
  const statusVal = statusKey ? String(props[statusKey] ?? '').toLowerCase() : null

  const secondaryEntries = Object.entries(props).filter(
    ([k]) => k !== titleKey && k.toLowerCase() !== 'tags'
  )

  return (
    <>
      <Card
        className="p-4 cursor-pointer hover:bg-accent/30 transition-colors select-none"
        onClick={() => setOpen(true)}
      >
        <div className="flex items-start justify-between gap-4 mb-2">
          <span className="font-semibold text-base leading-snug">{titleVal}</span>
          <Badge variant="secondary" className="font-mono text-xs flex-shrink-0">
            {Number(_score).toFixed(3)}
          </Badge>
        </div>

        <div className="flex items-center gap-2 text-sm text-muted-foreground flex-wrap">
          {statusKey && (
            <span
              className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${
                statusVal === 'attivo' || statusVal === 'active' ? 'bg-green-500' : 'bg-red-500'
              }`}
              title={String(props[statusKey])}
            />
          )}
          {secondaryEntries.slice(0, 3).map(([k, v], i) => (
            <span key={k} className="flex items-center gap-2">
              {i > 0 && <span className="opacity-40">·</span>}
              <span className="text-foreground/80">{formatValue(v)}</span>
            </span>
          ))}
        </div>
      </Card>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <div
            className="relative bg-card rounded-2xl border shadow-2xl w-full max-w-lg max-h-[80vh] overflow-y-auto p-6 mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-5 pr-6">
              {statusKey && (
                <span
                  className={`mt-1 inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                    statusVal === 'attivo' || statusVal === 'active' ? 'bg-green-500' : 'bg-red-500'
                  }`}
                />
              )}
              <div className="flex-1 min-w-0">
                <h2 className="text-lg font-semibold leading-snug">{titleVal}</h2>
              </div>
              <Badge variant="secondary" className="font-mono text-xs flex-shrink-0">
                {Number(_score).toFixed(3)}
              </Badge>
            </div>

            <dl className="space-y-3">
              {Object.entries(props)
                .filter(([k]) => k !== titleKey)
                .map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-muted-foreground text-[10px] uppercase tracking-wider mb-0.5">
                      {k.replace(/_/g, ' ')}
                    </dt>
                    <dd className="text-sm break-words">{formatValue(v)}</dd>
                  </div>
                ))}
            </dl>

            <button
              onClick={() => setOpen(false)}
              className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors text-lg leading-none"
              aria-label={t('common.close')}
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </>
  )
}

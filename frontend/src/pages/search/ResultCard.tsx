import { useState } from 'react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { SearchResult } from '@/api/search'

export default function ResultCard({ result }: { result: SearchResult }) {
  const [open, setOpen] = useState(false)
  const { _score, ...props } = result

  // Case-insensitive field lookup (Weaviate lowercases the first letter)
  const get = (field: string): string => {
    const entry = Object.entries(props).find(([k]) => k.toLowerCase() === field.toLowerCase())
    const v = entry?.[1]
    if (v === null || v === undefined || v === '') return '—'
    return String(v)
  }

  const nome = get('descrizione')
  const azienda = get('azienda')
  const sede = get('sede')
  const stato = get('stato')
  const jobTitle = get('job_title')
  const isAttivo = stato.toLowerCase() === 'attivo'

  return (
    <>
      <Card
        className="p-4 cursor-pointer hover:bg-accent/30 transition-colors select-none"
        onClick={() => setOpen(true)}
      >
        {/* Title row */}
        <div className="flex items-start justify-between gap-4 mb-2">
          <span className="font-semibold text-base leading-snug">{nome}</span>
          <Badge variant="secondary" className="font-mono text-xs flex-shrink-0">
            {Number(_score).toFixed(3)}
          </Badge>
        </div>

        {/* Compact info row */}
        <div className="flex items-center gap-2 text-sm text-muted-foreground flex-wrap">
          {/* Status dot — green = Attivo, red = anything else */}
          <span
            className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${
              isAttivo ? 'bg-green-500' : 'bg-red-500'
            }`}
            title={stato}
            aria-label={`Stato: ${stato}`}
          />
          {azienda !== '—' && <span className="font-medium text-foreground/80">{azienda}</span>}
          {sede !== '—' && <><span className="opacity-40">·</span><span>{sede}</span></>}
          {jobTitle !== '—' && <><span className="opacity-40">·</span><span>{jobTitle}</span></>}
        </div>
      </Card>

      {/* Detail modal */}
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <div
            className="relative bg-card rounded-2xl border shadow-2xl w-full max-w-lg max-h-[80vh] overflow-y-auto p-6 mx-4"
            onClick={e => e.stopPropagation()}
          >
            {/* Modal header */}
            <div className="flex items-start gap-3 mb-5 pr-6">
              <span
                className={`mt-1 inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                  isAttivo ? 'bg-green-500' : 'bg-red-500'
                }`}
                title={stato}
              />
              <div className="flex-1 min-w-0">
                <h2 className="text-lg font-semibold leading-snug">{nome}</h2>
                {azienda !== '—' && (
                  <p className="text-sm text-muted-foreground mt-0.5">{azienda}{sede !== '—' ? ` · ${sede}` : ''}</p>
                )}
              </div>
              <Badge variant="secondary" className="font-mono text-xs flex-shrink-0">
                {Number(_score).toFixed(3)}
              </Badge>
            </div>

            {/* All fields */}
            <dl className="grid grid-cols-[minmax(100px,140px)_1fr] gap-x-4 gap-y-2 text-sm">
              {Object.entries(props)
                .filter(([k]) => {
                  const kl = k.toLowerCase()
                  // skip fields already shown in header
                  return kl !== 'descrizione' && kl !== 'azienda' && kl !== 'sede'
                })
                .map(([k, v]) => (
                  <div className="contents" key={k}>
                    <dt className="text-muted-foreground text-xs uppercase tracking-wider truncate self-start pt-0.5" title={k}>
                      {k}
                    </dt>
                    <dd className="break-words">{formatValue(v)}</dd>
                  </div>
                ))}
            </dl>

            {/* Close button */}
            <button
              onClick={() => setOpen(false)}
              className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors text-lg leading-none"
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </>
  )
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  const s = String(v)
  return s === '' ? '—' : s
}

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

interface Props {
  filter: string
  minScore: number | null
  fields: string[]          // metadata_fields della entity = campi filtrabili
  onChange: (next: { filter: string; minScore: number | null }) => void
}

type Row = { field: string; value: string }

// Parse "Campo:Valore,Campo2:Valore2" → rows. Split-on-first-colon mirrors search.py.
function parse(filter: string): Row[] {
  if (!filter) return []
  return filter.split(',').map((part) => {
    const i = part.indexOf(':')
    return i === -1
      ? { field: part.trim(), value: '' }
      : { field: part.slice(0, i).trim(), value: part.slice(i + 1).trim() }
  })
}

function serialize(rows: Row[]): string {
  return rows
    .filter((r) => r.field && r.value)
    .map((r) => `${r.field}:${r.value}`)
    .join(',')
}

export default function FilterBar({ filter, minScore, fields, onChange }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<Row[]>(() => parse(filter))

  const commit = (next: Row[]) => {
    setRows(next)
    onChange({ filter: serialize(next), minScore })
  }

  const active = filter || minScore != null
  const hasFields = fields.length > 0

  return (
    <div className="border rounded-md">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-3 py-2 text-sm font-medium hover:bg-muted"
        aria-expanded={open}
      >
        {t('search.filters')} {active ? <span className="text-xs text-muted-foreground">{t('search.filtersActive')}</span> : null}
      </button>
      {open && (
        <div className="p-3 space-y-3 border-t">
          {!hasFields ? (
            <p className="text-xs text-muted-foreground">
              {t('search.noFilterFields')}
            </p>
          ) : (
            <div className="space-y-2">
              {rows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Select
                    value={row.field || undefined}
                    onValueChange={(field) =>
                      commit(rows.map((r, j) => (j === i ? { ...r, field } : r)))
                    }
                  >
                    <SelectTrigger className="w-[40%] text-xs">
                      <SelectValue placeholder={t('search.field')} />
                    </SelectTrigger>
                    <SelectContent>
                      {fields.map((f) => (
                        <SelectItem key={f} value={f} className="text-xs">{f}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    value={row.value}
                    onChange={(e) =>
                      commit(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
                    }
                    placeholder={t('search.value')}
                    className="flex-1 text-xs"
                  />
                  <Button
                    size="sm" variant="ghost"
                    onClick={() => commit(rows.filter((_, j) => j !== i))}
                    aria-label={t('search.removeFilter')}
                  >
                    ✕
                  </Button>
                </div>
              ))}
              <div className="flex items-center justify-between">
                <Button
                  size="sm" variant="outline"
                  onClick={() => commit([...rows, { field: '', value: '' }])}
                >
                  {t('search.addFilter')}
                </Button>
                <span className="text-xs text-muted-foreground">{t('search.andLogic')}</span>
              </div>
            </div>
          )}

          <div className="border-t pt-3">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">{t('search.minScore')}</span>
              <Input
                type="number"
                min={0} max={1} step={0.1}
                value={minScore ?? ''}
                placeholder={t('search.minScoreOff')}
                onChange={(e) => {
                  const v = e.target.value
                  onChange({ filter: serialize(rows), minScore: v === '' ? null : parseFloat(v) })
                }}
                className="w-24 text-xs"
              />
            </label>
            <p className="text-xs text-muted-foreground mt-1">{t('search.minScoreHint')}</p>
          </div>
        </div>
      )}
    </div>
  )
}

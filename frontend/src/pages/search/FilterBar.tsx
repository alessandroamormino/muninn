import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface Props {
  filter: string
  minScore: number | null
  onChange: (next: { filter: string; minScore: number | null }) => void
}

export default function FilterBar({ filter, minScore, onChange }: Props) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border rounded-md">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left px-3 py-2 text-sm font-medium hover:bg-muted"
        aria-expanded={open}
      >
        Filters {filter || minScore != null ? <span className="text-xs text-muted-foreground">(active)</span> : null}
      </button>
      {open && (
        <div className="p-3 space-y-3 border-t">
          <label className="block text-sm">
            <span className="text-muted-foreground">Filter (Campo:Valore[,Campo2:Valore2])</span>
            <Input
              value={filter}
              onChange={(e) => onChange({ filter: e.target.value, minScore })}
              placeholder="Azienda:BianchiTech"
              className="mt-1 font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground mt-1">Only metadata_fields are filterable. Multi-filter uses AND logic.</p>
          </label>
          <label className="block text-sm">
            <span className="text-muted-foreground">Minimum score: {minScore != null ? minScore.toFixed(2) : 'off'}</span>
            <div className="flex items-center gap-2 mt-1">
              <input
                type="range" min={0} max={1} step={0.05}
                value={minScore ?? 0}
                onChange={(e) => onChange({ filter, minScore: parseFloat(e.target.value) })}
                className="flex-1"
              />
              <Button size="sm" variant="ghost" onClick={() => onChange({ filter, minScore: null })}>Clear</Button>
            </div>
          </label>
        </div>
      )}
    </div>
  )
}

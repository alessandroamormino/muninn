import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const QDRANT_MODES = [
  { value: 'hybrid', label: 'Hybrid (BM25 + semantico)' },
  { value: 'vector', label: 'Vector (solo semantico)' },
  { value: 'bm25', label: 'BM25 (solo keyword)' },
  { value: 'fts', label: 'FTS (full-text con stemming)' },
]

interface Props {
  value: string
  onChange: (mode: string) => void
  disabled?: boolean
}

export default function SearchModeSelector({ value, onChange, disabled }: Props) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground whitespace-nowrap">Modalità:</span>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger className="w-[220px] h-8 text-sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {QDRANT_MODES.map((m) => (
            <SelectItem key={m.value} value={m.value}>
              {m.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

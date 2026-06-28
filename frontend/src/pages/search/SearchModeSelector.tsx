import { useTranslation } from 'react-i18next'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const ALL_MODES = [
  { value: 'hybrid', label: 'Hybrid (BM25 + semantico)' },
  { value: 'vector', label: 'Vettoriale (kNN semantico)' },
  { value: 'bm25', label: 'BM25 (keyword, ranking IDF)' },
  { value: 'fts', label: 'FTS (full-text + frase)' },
]

// Modes available per configured search_mode.
// fts/bm25/vector → single mode, selector hidden by caller.
// hybrid → all modes available (both vectors and sparse index exist).
const AVAILABLE: Record<string, string[]> = {
  hybrid: ['hybrid', 'vector', 'bm25', 'fts'],
  vector: ['vector'],
  bm25:   ['bm25'],
  fts:    ['fts'],
}

interface Props {
  value: string
  onChange: (mode: string) => void
  disabled?: boolean
  configuredMode: string
}

export default function SearchModeSelector({ value, onChange, disabled, configuredMode }: Props) {
  const { t } = useTranslation()
  const available = AVAILABLE[configuredMode] ?? ['hybrid', 'vector', 'bm25', 'fts']

  // Only show selector when there's actually a choice to make
  if (available.length <= 1) return null

  const modes = ALL_MODES.filter((m) => available.includes(m.value))

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground whitespace-nowrap">{t('search.mode')}:</span>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger className="w-[220px] h-8 text-sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {modes.map((m) => (
            <SelectItem key={m.value} value={m.value}>
              {m.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

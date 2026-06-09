import { Button } from '@/components/ui/button'

interface Props {
  value: 'and' | 'or'
  onChange: (mode: 'and' | 'or') => void
  disabled?: boolean
}

export default function MatchModeToggle({ value, onChange, disabled }: Props) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">Mode:</span>
      <Button
        type="button"
        size="sm"
        variant={value === 'and' ? 'default' : 'outline'}
        disabled={disabled}
        onClick={() => onChange('and')}
        title="All search terms must appear in the result"
      >
        AND
      </Button>
      <Button
        type="button"
        size="sm"
        variant={value === 'or' ? 'default' : 'outline'}
        disabled={disabled}
        onClick={() => onChange('or')}
        title="Any search term may appear in the result"
      >
        OR
      </Button>
    </div>
  )
}

import { useState, useEffect } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { useSuggest } from '@/api/search'

const DEBOUNCE_MS = 200

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

interface Props {
  placeholder: string
  onSubmit: (q: string) => void
  disabled?: boolean
  collection?: string | null
}

export default function SearchBar({ placeholder, onSubmit, disabled, collection }: Props) {
  const [draft, setDraft] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const [open, setOpen] = useState(true)

  const debouncedDraft = useDebounce(draft, DEBOUNCE_MS)
  const { data: suggestions } = useSuggest(debouncedDraft, collection ?? null)

  const showSuggest = open && (suggestions?.length ?? 0) > 0 && draft.length >= 2 && !!collection

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!suggestions || suggestions.length === 0) {
      if (e.key === 'Escape') {
        setOpen(false)
        setSelectedIndex(-1)
      }
      if (e.key === 'Tab') {
        setOpen(false)
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex((i) => (i + 1) % suggestions.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex((i) => (i <= 0 ? suggestions.length - 1 : i - 1))
    } else if (e.key === 'Enter' && selectedIndex >= 0 && suggestions[selectedIndex]) {
      e.preventDefault()
      const picked = suggestions[selectedIndex]
      setDraft(picked)
      onSubmit(picked)
      setOpen(false)
      setSelectedIndex(-1)
    } else if (e.key === 'Escape') {
      setOpen(false)
      setSelectedIndex(-1)
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  const handle = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = draft.trim()
    if (trimmed) {
      onSubmit(trimmed)
      setOpen(false)
      setSelectedIndex(-1)
    }
  }

  return (
    <form onSubmit={handle} className="flex gap-2">
      <div className="relative flex-1">
        <Input
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setSelectedIndex(-1); setOpen(true) }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          className="text-base"
          aria-autocomplete="list"
          aria-controls="suggest-listbox"
          aria-activedescendant={selectedIndex >= 0 ? `suggest-opt-${selectedIndex}` : undefined}
        />
        {showSuggest && (
          <ul
            id="suggest-listbox"
            role="listbox"
            className="absolute top-full left-0 right-0 z-50 bg-background border rounded-md shadow-md mt-1 max-h-60 overflow-auto"
          >
            {suggestions!.map((s, i) => (
              <li
                key={s}
                id={`suggest-opt-${i}`}
                role="option"
                aria-selected={i === selectedIndex}
                className={`px-3 py-2 cursor-pointer text-sm ${i === selectedIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
                onMouseDown={(e) => {
                  e.preventDefault()
                  setDraft(s)
                  onSubmit(s)
                  setOpen(false)
                  setSelectedIndex(-1)
                }}
              >
                {s}
              </li>
            ))}
          </ul>
        )}
      </div>
      <Button type="submit" disabled={disabled || !draft.trim()}>Search</Button>
    </form>
  )
}

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface Props {
  placeholder: string
  onSubmit: (q: string) => void
  disabled?: boolean
}

export default function SearchBar({ placeholder, onSubmit, disabled }: Props) {
  const [draft, setDraft] = useState('')
  const handle = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = draft.trim()
    if (trimmed) onSubmit(trimmed)
  }
  return (
    <form onSubmit={handle} className="flex gap-2">
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="text-base"
      />
      <Button type="submit" disabled={disabled || !draft.trim()}>Search</Button>
    </form>
  )
}

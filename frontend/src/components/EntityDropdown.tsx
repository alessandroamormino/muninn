import { useCollections } from '@/api/collections'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface Props {
  value: string | null
  onChange: (v: string) => void
  placeholder?: string
}

export default function EntityDropdown({ value, onChange, placeholder = 'Select collection...' }: Props) {
  const { data, isLoading, isError } = useCollections()
  const collections = data?.collections ?? []
  return (
    <Select value={value ?? undefined} onValueChange={onChange} disabled={isLoading || isError}>
      <SelectTrigger className="w-[280px]">
        <SelectValue placeholder={
          isLoading ? 'Loading...' :
          isError ? 'Could not load entities' :
          collections.length === 0 ? 'No entities configured' :
          placeholder
        } />
      </SelectTrigger>
      <SelectContent>
        {collections.map((c) => (
          <SelectItem key={c} value={c}>{c}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

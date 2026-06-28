import { useTranslation } from 'react-i18next'
import { useCollections } from '@/api/collections'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface Props {
  value: string | null
  onChange: (v: string) => void
  placeholder?: string
  // When true, hide entities whose load state is 'unloaded' (e.g. on Search/Graph,
  // where an unloaded collection would only return 409). Status undefined = treated
  // as active for backward compat with cached responses.
  activeOnly?: boolean
}

export default function EntityDropdown({ value, onChange, placeholder, activeOnly = false }: Props) {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useCollections()
  const all = data?.collections ?? []
  const collections = activeOnly ? all.filter((c) => c.status !== 'unloaded') : all
  return (
    <Select value={value ?? undefined} onValueChange={onChange} disabled={isLoading || isError}>
      <SelectTrigger className="w-[280px]">
        <SelectValue placeholder={
          isLoading ? t('common.loading') :
          isError ? t('entityDropdown.loadError') :
          collections.length === 0 ? t('entityDropdown.noEntities') :
          (placeholder ?? t('entityDropdown.select'))
        } />
      </SelectTrigger>
      <SelectContent>
        {collections.map((c) => (
          <SelectItem key={c.name} value={c.name}>
            {c.name}{c.is_global ? ` (${t('common.default')})` : ''}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

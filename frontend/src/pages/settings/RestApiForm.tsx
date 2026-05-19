import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCreateRestApiEntity } from '@/api/restapi'
import type { RestApiPayload } from '@/api/restapi'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

const AUTH_TYPES: RestApiPayload['auth_type'][] = [
  'none',
  'bearer',
  'api_key_header',
  'api_key_param',
  'basic',
]
const PAG_TYPES: RestApiPayload['pagination_type'][] = ['none', 'offset', 'page', 'cursor']

export default function RestApiForm({ onDone }: { onDone: (c: string) => void }) {
  const [form, setForm] = useState<RestApiPayload>({
    collection: '',
    url: '',
    id_field: 'id',
    text_fields: [],
    metadata_fields: [],
    output_fields: [],
    auth_type: 'none',
    auth_env_var: '',
    pagination_type: 'none',
  })
  const create = useCreateRestApiEntity()
  const qc = useQueryClient()

  const submit = async () => {
    try {
      const r = await create.mutateAsync(form)
      toast.success('Configuration saved.')
      qc.invalidateQueries({ queryKey: ['collections'] })
      onDone(r.collection)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold">Add REST API source</h3>
      <Field
        label="Collection (PascalCase)"
        value={form.collection}
        onChange={(v) => setForm({ ...form, collection: v })}
        placeholder="MyEntity"
      />
      <Field
        label="URL"
        value={form.url}
        onChange={(v) => setForm({ ...form, url: v })}
        placeholder="https://api.example.com/items"
      />
      <Field
        label="ID field"
        value={form.id_field}
        onChange={(v) => setForm({ ...form, id_field: v })}
      />
      <Field
        label="JSON results key (optional)"
        value={form.json_key ?? ''}
        onChange={(v) => setForm({ ...form, json_key: v || null })}
        placeholder="results"
      />
      <ListField
        label="Text fields (vectorized)"
        value={form.text_fields}
        onChange={(v) => setForm({ ...form, text_fields: v })}
      />
      <ListField
        label="Metadata fields"
        value={form.metadata_fields}
        onChange={(v) => setForm({ ...form, metadata_fields: v })}
      />
      <ListField
        label="Output fields"
        value={form.output_fields}
        onChange={(v) => setForm({ ...form, output_fields: v })}
      />

      <SelectField
        label="Auth type"
        value={form.auth_type}
        options={AUTH_TYPES}
        onChange={(v) => setForm({ ...form, auth_type: v as RestApiPayload['auth_type'] })}
      />
      {form.auth_type !== 'none' && (
        <>
          <Field
            label="Env var name (UPPERCASE)"
            value={form.auth_env_var ?? ''}
            onChange={(v) => setForm({ ...form, auth_env_var: v.toUpperCase() })}
            placeholder="TMDB_BEARER_TOKEN"
          />
          <p className="text-xs text-muted-foreground bg-muted p-2 rounded font-mono">
            Set this variable in the .env file before starting the container. The GUI never reads
            or stores secrets — only the variable NAME is persisted as{' '}
            <code>
              ${'{'}
              {form.auth_env_var || 'YOUR_VAR'}
              {'}'}
            </code>{' '}
            in config.yaml.
          </p>
          {form.auth_type === 'api_key_header' && (
            <Field
              label="Header name"
              value={form.auth_header_name ?? ''}
              onChange={(v) => setForm({ ...form, auth_header_name: v })}
              placeholder="X-Api-Key"
            />
          )}
          {form.auth_type === 'api_key_param' && (
            <Field
              label="Query param name"
              value={form.auth_param_name ?? ''}
              onChange={(v) => setForm({ ...form, auth_param_name: v })}
              placeholder="api_key"
            />
          )}
        </>
      )}

      <SelectField
        label="Pagination type"
        value={form.pagination_type}
        options={PAG_TYPES}
        onChange={(v) => setForm({ ...form, pagination_type: v as RestApiPayload['pagination_type'] })}
      />
      {form.pagination_type === 'cursor' && (
        <Field
          label="Next-page key (cursor)"
          value={form.pagination_next_key ?? ''}
          onChange={(v) => setForm({ ...form, pagination_next_key: v })}
          placeholder="next"
        />
      )}

      <Button onClick={submit} disabled={!form.collection || !form.url || create.isPending}>
        {create.isPending ? 'Saving...' : 'Save configuration'}
      </Button>
      {create.isSuccess && (
        <p className="text-sm text-muted-foreground">
          Configuration saved. Click <strong>Run Sync</strong> in the Sync tab to index your data.
        </p>
      )}
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1"
      />
    </label>
  )
}

function ListField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string[]
  onChange: (v: string[]) => void
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted-foreground">{label} (comma-separated)</span>
      <Input
        value={value.join(', ')}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean)
          )
        }
        className="mt-1 font-mono text-xs"
      />
    </label>
  )
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: readonly string[]
  onChange: (v: string) => void
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="mt-1">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  )
}

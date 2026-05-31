import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCreateConfig } from '@/api/config'
import type { CreateConfigPayload } from '@/api/config'
import { toast } from 'sonner'
import SuggestConfigButton from './SuggestConfigButton'

// ── Regex constants ───────────────────────────────────────────────────────────

const ENTITY_NAME_RE = /^[A-Za-z][A-Za-z0-9]*$/
const UPPERCASE_RE = /^[A-Z][A-Z0-9_]*$/

// ── Step type ─────────────────────────────────────────────────────────────────

type Step = 1 | 2 | 3

// ── Form state interface ──────────────────────────────────────────────────────

interface MySQLWizardForm {
  collection: string
  port: number
  hostEnvVar: string
  dbEnvVar: string
  userEnvVar: string
  passwordEnvVar: string
  fromTable: string
  idField: string
  fields: string[]
  textFields: string[]
  metadataFields: string[]
  outputFields: string[]
}

const INITIAL_FORM: MySQLWizardForm = {
  collection: '',
  port: 3306,
  hostEnvVar: '',
  dbEnvVar: '',
  userEnvVar: '',
  passwordEnvVar: '',
  fromTable: '',
  idField: 'id',
  fields: [],
  textFields: [],
  metadataFields: [],
  outputFields: [],
}

// ── Inner helper components ───────────────────────────────────────────────────

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

// ── MySQLWizard component ─────────────────────────────────────────────────────

interface Props {
  onDone: (collection: string) => void
  onCancel: () => void
}

export default function MySQLWizard({ onDone, onCancel }: Props) {
  const [step, setStep] = useState<Step>(1)
  const [form, setForm] = useState<MySQLWizardForm>(INITIAL_FORM)
  const create = useCreateConfig()

  // ── Validators ──────────────────────────────────────────────────────────────

  const step1Valid = ENTITY_NAME_RE.test(form.collection)
  const step2Valid =
    UPPERCASE_RE.test(form.hostEnvVar) &&
    UPPERCASE_RE.test(form.dbEnvVar) &&
    UPPERCASE_RE.test(form.userEnvVar) &&
    UPPERCASE_RE.test(form.passwordEnvVar) &&
    form.port > 0
  const step3Valid =
    form.fromTable.trim() !== '' &&
    form.fields.length > 0 &&
    form.idField.trim() !== '' &&
    form.fields.includes(form.idField)

  // ── handleCreate ────────────────────────────────────────────────────────────

  const handleCreate = async () => {
    const payload: CreateConfigPayload = {
      collection: form.collection,
      source_type: 'mysql',
      port: form.port,
      host_env_var: form.hostEnvVar,
      db_env_var: form.dbEnvVar,
      user_env_var: form.userEnvVar,
      password_env_var: form.passwordEnvVar,
      from_table: form.fromTable,
      fields: form.fields,
      id_field: form.idField,
      text_fields: form.textFields,
      metadata_fields: form.metadataFields,
      output_fields: form.outputFields,
    }
    try {
      const r = await create.mutateAsync(payload)
      toast.success('Entity creata.')
      onDone(r.collection)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  // ── Step indicator ───────────────────────────────────────────────────────────

  const STEPS = [
    { n: 1 as Step, label: '1. Identity' },
    { n: 2 as Step, label: '2. Connection' },
    { n: 3 as Step, label: '3. Query & Fields' },
  ]

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Step indicator */}
      <div className="flex gap-4 mb-6">
        {STEPS.map(({ n, label }) => (
          <span
            key={n}
            className={step === n ? 'text-primary font-semibold' : 'text-muted-foreground'}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Step 1 — Identity */}
      {step === 1 && (
        <div className="space-y-4">
          <h3 className="text-base font-semibold">Add MySQL source</h3>
          <Field
            label="Entity name"
            value={form.collection}
            onChange={(v) => setForm({ ...form, collection: v })}
            placeholder="Collaboratori"
          />
          {form.collection !== '' && !step1Valid && (
            <p className="text-destructive text-xs">
              Il nome deve iniziare con una lettera e contenere solo lettere e cifre.
            </p>
          )}
          <span className="text-sm text-muted-foreground">Source type: MySQL</span>
        </div>
      )}

      {/* Step 2 — Connection */}
      {step === 2 && (
        <div className="space-y-4">
          <h3 className="text-base font-semibold">Connection settings</h3>
          <Field
            label="Host env var name"
            value={form.hostEnvVar}
            onChange={(v) => setForm({ ...form, hostEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_HOST"
          />
          <label className="block text-sm">
            <span className="text-muted-foreground">Port</span>
            <Input
              type="number"
              min={1}
              max={65535}
              value={form.port}
              onChange={(e) => setForm({ ...form, port: parseInt(e.target.value, 10) || 3306 })}
              className="mt-1"
            />
          </label>
          <Field
            label="Database env var name"
            value={form.dbEnvVar}
            onChange={(v) => setForm({ ...form, dbEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_DB"
          />
          <Field
            label="User env var name"
            value={form.userEnvVar}
            onChange={(v) => setForm({ ...form, userEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_USER"
          />
          <Field
            label="Password env var name"
            value={form.passwordEnvVar}
            onChange={(v) => setForm({ ...form, passwordEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_PASSWORD"
          />
          <p className="text-xs text-muted-foreground bg-muted p-2 rounded font-mono">
            {'Credentials are stored as ${VAR} in config.yaml — set the actual values in .env'}
          </p>
        </div>
      )}

      {/* Step 3 — Query & Fields */}
      {step === 3 && (
        <div className="space-y-4">
          <h3 className="text-base font-semibold">Query & Fields</h3>
          <Field
            label="From (table name)"
            value={form.fromTable}
            onChange={(v) => setForm({ ...form, fromTable: v })}
            placeholder="dipendenti"
          />
          <Field
            label="ID field"
            value={form.idField}
            onChange={(v) => setForm({ ...form, idField: v })}
            placeholder="id"
          />
          <ListField
            label="Fields (comma-separated)"
            value={form.fields}
            onChange={(v) => setForm({ ...form, fields: v })}
          />
          {form.idField.trim() !== '' && !form.fields.includes(form.idField) && form.fields.length > 0 && (
            <p className="text-destructive text-xs">
              ID field must be one of the listed fields.
            </p>
          )}
          <div className="space-y-2">
            <ListField
              label="Text fields"
              value={form.textFields}
              onChange={(v) => setForm({ ...form, textFields: v })}
            />
            <ListField
              label="Metadata fields"
              value={form.metadataFields}
              onChange={(v) => setForm({ ...form, metadataFields: v })}
            />
            <ListField
              label="Output fields"
              value={form.outputFields}
              onChange={(v) => setForm({ ...form, outputFields: v })}
            />
            <div className="pt-2">
              <SuggestConfigButton
                fields={form.fields}
                onResult={(suggested) => {
                  setForm({
                    ...form,
                    idField: suggested.id_field !== '' ? suggested.id_field : form.idField,
                    textFields: suggested.text_fields,
                    metadataFields: suggested.metadata_fields,
                    outputFields: suggested.output_fields,
                  })
                }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Navigation footer */}
      <div className="flex gap-2 pt-4">
        <Button variant="ghost" type="button" onClick={onCancel}>
          Discard and close
        </Button>
        {step > 1 && (
          <Button variant="outline" type="button" onClick={() => setStep((s) => (s - 1) as Step)}>
            Back
          </Button>
        )}
        {step < 3 && (
          <Button
            type="button"
            disabled={(step === 1 && !step1Valid) || (step === 2 && !step2Valid)}
            onClick={() => setStep((s) => (s + 1) as Step)}
          >
            Next step
          </Button>
        )}
        {step === 3 && (
          <Button
            type="button"
            disabled={!step3Valid || create.isPending}
            onClick={handleCreate}
          >
            {create.isPending ? 'Creating...' : 'Create entity'}
          </Button>
        )}
      </div>
    </div>
  )
}

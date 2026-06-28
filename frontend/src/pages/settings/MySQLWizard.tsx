import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCreateConfig } from '@/api/config'
import type { CreateConfigPayload } from '@/api/config'
import { toast } from 'sonner'
import SuggestConfigButton from './SuggestConfigButton'

// ── Regex constants ───────────────────────────────────────────────────────────

const ENTITY_NAME_RE = /^[A-Za-z][A-Za-z0-9]*$/
const UPPERCASE_RE = /^[A-Z][A-Z0-9_]*$/

// ── Search mode definitions ───────────────────────────────────────────────────

interface SearchModeInfo {
  value: SearchMode
  icon: string
  requiresEmbedding: boolean
}

const SEARCH_MODE_DEFS: SearchModeInfo[] = [
  { value: 'hybrid', icon: '⚡', requiresEmbedding: true },
  { value: 'fts', icon: '🔤', requiresEmbedding: false },
  { value: 'bm25', icon: '📊', requiresEmbedding: false },
  { value: 'vector', icon: '🧠', requiresEmbedding: true },
]

// ── SearchModeCard ────────────────────────────────────────────────────────────

function SearchModeCard({
  def,
  selected,
  onSelect,
}: {
  def: SearchModeInfo
  selected: boolean
  onSelect: () => void
}) {
  const { t } = useTranslation()
  const [showInfo, setShowInfo] = useState(false)
  const label = t(`mysqlMode.${def.value}.label`)
  const tagline = t(`mysqlMode.${def.value}.tagline`)
  const detail = t(`mysqlMode.${def.value}.detail`)
  const examples = t(`mysqlMode.${def.value}.examples`, { returnObjects: true }) as string[]

  return (
    <>
      <div
        onClick={onSelect}
        className={[
          'relative cursor-pointer rounded-lg border p-3 transition-all',
          selected
            ? 'border-primary bg-primary/5 ring-1 ring-primary'
            : 'border-border hover:border-muted-foreground/40 hover:bg-muted/30',
        ].join(' ')}
      >
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setShowInfo(true) }}
          className="absolute top-2 right-2 text-muted-foreground hover:text-foreground text-xs leading-none"
          title={t('mysql.moreInfo')}
        >
          ⓘ
        </button>
        <div className="text-2xl mb-1">{def.icon}</div>
        <div className="text-sm font-semibold leading-tight">{label}</div>
        <div className="text-xs text-muted-foreground mt-0.5">{tagline}</div>
        {!def.requiresEmbedding && (
          <span className="mt-1.5 inline-block text-[10px] bg-green-100 text-green-700 rounded px-1.5 py-0.5">
            {t('mysql.noEmbedding')}
          </span>
        )}
      </div>

      {showInfo && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowInfo(false)}
        >
          <div
            className="bg-background rounded-xl shadow-xl max-w-sm w-full p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2">
              <span className="text-2xl">{def.icon}</span>
              <div>
                <div className="font-semibold">{label}</div>
                <div className="text-xs text-muted-foreground">{tagline}</div>
              </div>
            </div>
            <p className="text-sm text-muted-foreground">{detail}</p>
            <div>
              <div className="text-xs font-medium mb-1">{t('mysql.examplesTitle')}</div>
              <ul className="space-y-1">
                {examples.map((ex) => (
                  <li key={ex} className="text-xs text-muted-foreground bg-muted rounded px-2 py-1 font-mono">
                    {ex}
                  </li>
                ))}
              </ul>
            </div>
            <Button size="sm" variant="outline" className="w-full" onClick={() => setShowInfo(false)}>
              {t('common.close')}
            </Button>
          </div>
        </div>
      )}
    </>
  )
}

// ── Step type ─────────────────────────────────────────────────────────────────

type Step = 1 | 2 | 3

// ── Form state interface ──────────────────────────────────────────────────────

type SearchMode = 'fts' | 'bm25' | 'hybrid' | 'vector'

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
  searchMode: SearchMode
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
  searchMode: 'hybrid',
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
  const { t } = useTranslation()
  return (
    <label className="block text-sm">
      <span className="text-muted-foreground">{label} {t('common.commaSeparated')}</span>
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
  const { t } = useTranslation()
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
      search_mode: form.searchMode,
    }
    try {
      const r = await create.mutateAsync(payload)
      toast.success(t('mysql.created'))
      onDone(r.collection)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  // ── Step indicator ───────────────────────────────────────────────────────────

  const STEPS = [
    { n: 1 as Step, label: t('mysql.step1') },
    { n: 2 as Step, label: t('mysql.step2') },
    { n: 3 as Step, label: t('mysql.step3') },
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
          <h3 className="text-base font-semibold">{t('mysql.addTitle')}</h3>
          <Field
            label={t('mysql.entityName')}
            value={form.collection}
            onChange={(v) => setForm({ ...form, collection: v })}
            placeholder="Collaboratori"
          />
          {form.collection !== '' && !step1Valid && (
            <p className="text-destructive text-xs">
              {t('mysql.nameError')}
            </p>
          )}
          <span className="text-sm text-muted-foreground">{t('mysql.sourceTypeMysql')}</span>
        </div>
      )}

      {/* Step 2 — Connection */}
      {step === 2 && (
        <div className="space-y-4">
          <h3 className="text-base font-semibold">{t('mysql.connTitle')}</h3>
          <Field
            label={t('mysql.hostEnv')}
            value={form.hostEnvVar}
            onChange={(v) => setForm({ ...form, hostEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_HOST"
          />
          <label className="block text-sm">
            <span className="text-muted-foreground">{t('mysql.port')}</span>
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
            label={t('mysql.dbEnv')}
            value={form.dbEnvVar}
            onChange={(v) => setForm({ ...form, dbEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_DB"
          />
          <Field
            label={t('mysql.userEnv')}
            value={form.userEnvVar}
            onChange={(v) => setForm({ ...form, userEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_USER"
          />
          <Field
            label={t('mysql.passwordEnv')}
            value={form.passwordEnvVar}
            onChange={(v) => setForm({ ...form, passwordEnvVar: v.toUpperCase() })}
            placeholder="MYSQL_PASSWORD"
          />
          <p className="text-xs text-muted-foreground bg-muted p-2 rounded font-mono">
            {t('mysql.credNote')}
          </p>
        </div>
      )}

      {/* Step 3 — Query & Fields */}
      {step === 3 && (
        <div className="space-y-4">
          <h3 className="text-base font-semibold">{t('mysql.queryTitle')}</h3>
          <Field
            label={t('mysql.fromTable')}
            value={form.fromTable}
            onChange={(v) => setForm({ ...form, fromTable: v })}
            placeholder="dipendenti"
          />
          <Field
            label={t('upload.idField')}
            value={form.idField}
            onChange={(v) => setForm({ ...form, idField: v })}
            placeholder="id"
          />
          <ListField
            label={t('mysql.fields')}
            value={form.fields}
            onChange={(v) => setForm({ ...form, fields: v })}
          />
          {form.idField.trim() !== '' && !form.fields.includes(form.idField) && form.fields.length > 0 && (
            <p className="text-destructive text-xs">
              {t('mysql.idFieldError')}
            </p>
          )}
          <div className="block text-sm">
            <span className="text-muted-foreground">{t('mysql.searchMode')}</span>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {SEARCH_MODE_DEFS.map((def) => (
                <SearchModeCard
                  key={def.value}
                  def={def}
                  selected={form.searchMode === def.value}
                  onSelect={() => setForm({ ...form, searchMode: def.value })}
                />
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <ListField
              label={t('mysql.textFields')}
              value={form.textFields}
              onChange={(v) => setForm({ ...form, textFields: v })}
            />
            <ListField
              label={t('mysql.metadataFields')}
              value={form.metadataFields}
              onChange={(v) => setForm({ ...form, metadataFields: v })}
            />
            <ListField
              label={t('mysql.outputFields')}
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
          {t('mysql.discard')}
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
            {t('mysql.nextStep')}
          </Button>
        )}
        {step === 3 && (
          <Button
            type="button"
            disabled={!step3Valid || create.isPending}
            onClick={handleCreate}
          >
            {create.isPending ? t('mysql.creating') : t('mysql.create')}
          </Button>
        )}
      </div>
    </div>
  )
}

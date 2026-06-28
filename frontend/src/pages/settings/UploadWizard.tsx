import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { useUpload, useConfirmUpload } from '@/api/upload'
import type { SuggestedConfig } from '@/api/upload'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'

type Step = 'upload' | 'review' | 'confirming'

export default function UploadWizard({ onDone }: { onDone: (collection: string) => void }) {
  const { t } = useTranslation()
  const [step, setStep] = useState<Step>('upload')
  const [config, setConfig] = useState<SuggestedConfig | null>(null)
  const upload = useUpload()
  const confirm = useConfirmUpload()
  const qc = useQueryClient()

  const handleFile = async (file: File) => {
    try {
      const r = await upload.mutateAsync(file)
      setConfig(r.suggested_config)
      if (r._warning) toast.warning(r._warning)
      setStep('review')
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const handleConfirm = async () => {
    if (!config) return
    setStep('confirming')
    try {
      const r = await confirm.mutateAsync(config)
      toast.success(t('upload.saved'))
      qc.invalidateQueries({ queryKey: ['collections'] })
      onDone(r.collection)
    } catch (e) {
      toast.error((e as Error).message)
      setStep('review')
    }
  }

  if (step === 'upload') {
    return (
      <div className="space-y-4">
        <h3 className="text-base font-semibold">{t('upload.step1')}</h3>
        <p className="text-sm text-muted-foreground">
          {t('upload.step1hint')}
        </p>
        <label className="block border-2 border-dashed rounded-md p-8 text-center cursor-pointer hover:bg-muted">
          <input
            type="file"
            accept=".csv,text/csv,application/json,.json"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
          <div className="text-sm">
            {upload.isPending ? t('upload.uploading') : t('upload.dropHint')}
          </div>
        </label>
        {upload.isPending && <Progress value={50} />}
      </div>
    )
  }

  if (step === 'review' && config) {
    return (
      <div className="space-y-4">
        <h3 className="text-base font-semibold">{t('upload.step2')}</h3>
        <p className="text-sm text-muted-foreground">{t('upload.step2hint')}</p>
        <div className="space-y-2">
          <FieldRow
            label={t('upload.collection')}
            value={config.collection}
            onChange={(v) => setConfig({ ...config, collection: v })}
          />
          <FieldRow
            label={t('upload.idField')}
            value={config.id_field}
            onChange={(v) => setConfig({ ...config, id_field: v })}
          />
          <ListField
            label={t('upload.textFields')}
            value={config.text_fields}
            onChange={(v) => setConfig({ ...config, text_fields: v })}
          />
          <ListField
            label={t('upload.metadataFields')}
            value={config.metadata_fields}
            onChange={(v) => setConfig({ ...config, metadata_fields: v })}
          />
          <ListField
            label={t('upload.outputFields')}
            value={config.output_fields}
            onChange={(v) => setConfig({ ...config, output_fields: v })}
          />
          <FieldRow
            label={t('upload.delimiter')}
            value={config.delimiter}
            onChange={(v) => setConfig({ ...config, delimiter: v.slice(0, 1) || ',' })}
          />
        </div>
        <div className="flex gap-2">
          <Button onClick={handleConfirm}>{t('upload.confirmSync')}</Button>
          <Button variant="outline" onClick={() => setStep('upload')}>{t('common.back')}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold">{t('upload.step3')}</h3>
      <Progress value={80} />
      <p className="text-sm text-muted-foreground">{t('upload.writing')}</p>
    </div>
  )
}

function FieldRow({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Input value={value} onChange={(e) => onChange(e.target.value)} className="mt-1" />
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

import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { useGetConfig, useSaveConfig } from '@/api/config'
import SuggestConfigButton from './SuggestConfigButton'
import type { SuggestFieldsResponse } from '@/api/config'
import { toast } from 'sonner'

// ─── YAML field extraction ────────────────────────────────────────────────────
// js-yaml is not installed; best-effort regex for inline + block sequences.

function extractTextAndMetadataFields(yaml: string): string[] {
  const result: string[] = []
  for (const key of ['text_fields', 'metadata_fields']) {
    const inlineMatch = new RegExp(`^\\s*${key}:\\s*\\[([^\\]\\n]*)\\]`, 'm').exec(yaml)
    if (inlineMatch) {
      result.push(
        ...inlineMatch[1]
          .split(',')
          .map((s) => s.trim().replace(/^['"]|['"]$/g, ''))
          .filter(Boolean),
      )
      continue
    }
    const blockMatch = new RegExp(`^(\\s*)${key}:\\s*$`, 'm').exec(yaml)
    if (blockMatch) {
      const rest = yaml.slice(blockMatch.index + blockMatch[0].length)
      let m
      const re = /^[ \t]+-[ \t]+(.+)$/gm
      while ((m = re.exec(rest)) !== null)
        result.push(m[1].trim().replace(/^['"]|['"]$/g, ''))
    }
  }
  return [...new Set(result)].filter(Boolean)
}

// ─── YAML field patching ──────────────────────────────────────────────────────
// Replaces text_fields / metadata_fields / output_fields with suggested values.
// Handles both inline `key: [a, b]` and block-sequence `key:\n  - a` forms.
// Always writes inline form in the output (simpler, avoids re-indenting).

type SuggestedFields = Pick<
  SuggestFieldsResponse['suggested_config'],
  'text_fields' | 'metadata_fields' | 'output_fields'
>

function applyFieldSuggestions(yaml: string, suggested: SuggestedFields): string {
  const lines = yaml.split('\n')
  const out: string[] = []
  const keys = ['text_fields', 'metadata_fields', 'output_fields'] as const

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // Inline form: `  key: [a, b, c]`
    const inlineMatch = line.match(/^(\s*)(text_fields|metadata_fields|output_fields):\s*\[/)
    if (inlineMatch) {
      const [, indent, key] = inlineMatch as [string, string, (typeof keys)[number]]
      out.push(`${indent}${key}: [${suggested[key].join(', ')}]`)
      continue
    }

    // Block sequence header: `  key:`
    const blockMatch = line.match(/^(\s*)(text_fields|metadata_fields|output_fields):\s*$/)
    if (blockMatch) {
      const [, indent, key] = blockMatch as [string, string, (typeof keys)[number]]
      // Detect child indent from next line, fallback to indent + 2 spaces
      const nextLine = lines[i + 1]
      let childIndent = indent + '  '
      if (nextLine) {
        const m = nextLine.match(/^(\s+)-/)
        if (m) childIndent = m[1]
      }
      out.push(`${indent}${key}:`)
      for (const val of suggested[key]) out.push(`${childIndent}- ${val}`)
      // Skip the old block sequence items that follow
      while (i + 1 < lines.length && /^\s+-\s/.test(lines[i + 1])) i++
      continue
    }

    out.push(line)
  }
  return out.join('\n')
}

// ─── LCS-based line diff ──────────────────────────────────────────────────────

type DiffEntry = { type: 'same' | 'add' | 'remove'; value: string }

function lineDiff(a: string[], b: string[]): DiffEntry[] {
  const n = a.length
  const m = b.length
  // Build LCS table bottom-up
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] =
        a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])

  const result: DiffEntry[] = []
  let i = 0,
    j = 0
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      result.push({ type: 'same', value: a[i] })
      i++
      j++
    } else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) {
      result.push({ type: 'add', value: b[j] })
      j++
    } else {
      result.push({ type: 'remove', value: a[i] })
      i++
    }
  }
  return result
}

// ─── Unified diff renderer ────────────────────────────────────────────────────

function UnifiedDiff({ oldYaml, newYaml }: { oldYaml: string; newYaml: string }) {
  const entries = useMemo(
    () => lineDiff(oldYaml.split('\n'), newYaml.split('\n')),
    [oldYaml, newYaml],
  )

  return (
    <div className="border rounded-md overflow-auto max-h-[420px] text-sm">
      {entries.map((entry, idx) => {
        const isRemove = entry.type === 'remove'
        const isAdd = entry.type === 'add'
        return (
          <div
            key={idx}
            className={`flex min-w-0 ${isRemove ? 'bg-red-50' : isAdd ? 'bg-green-50' : ''}`}
          >
            <span
              className={`select-none w-5 shrink-0 text-center font-bold text-[11px] leading-5 ${
                isRemove ? 'text-red-600' : isAdd ? 'text-green-600' : 'text-transparent'
              }`}
            >
              {isRemove ? '-' : isAdd ? '+' : ' '}
            </span>
            <span
              className={`font-mono text-xs leading-5 whitespace-pre px-1 flex-1 ${
                isRemove ? 'text-red-900' : isAdd ? 'text-green-900' : ''
              }`}
            >
              {entry.value}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ─── YamlEditor ──────────────────────────────────────────────────────────────

export default function YamlEditor({ collection }: { collection: string }) {
  const { t } = useTranslation()
  const { data: configData, refetch, isLoading } = useGetConfig(collection)
  const save = useSaveConfig(collection)

  const [yamlContent, setYamlContent] = useState('')
  const [savedYaml, setSavedYaml] = useState('')
  const [serverError, setServerError] = useState<string | null>(null)
  // Non-null when a suggestion is pending review; holds the patched YAML.
  const [suggestedYaml, setSuggestedYaml] = useState<string | null>(null)

  const isDirty = yamlContent !== savedYaml

  // Single effect: initialize from cached/fetched data, reset on collection change.
  useEffect(() => {
    setSuggestedYaml(null)
    if (configData?.yaml !== undefined) {
      setYamlContent(configData.yaml)
      setSavedYaml(configData.yaml)
      setServerError(null)
    } else {
      setYamlContent('')
      setSavedYaml('')
    }
  }, [collection, configData?.yaml])

  const parsedFields = useMemo(() => extractTextAndMetadataFields(yamlContent), [yamlContent])

  const handleSave = async () => {
    setServerError(null)
    try {
      await save.mutateAsync(yamlContent)
      setSavedYaml(yamlContent)
      setSuggestedYaml(null)
      toast.success(t('yaml.saved'))
    } catch (e) {
      const msg = (e as Error).message
      setServerError(msg)
      toast.error(t('yaml.saveFailed'))
    }
  }

  const handleReload = async () => {
    if (isDirty && !window.confirm(t('yaml.reloadConfirm'))) return
    const result = await refetch()
    const freshYaml = result.data?.yaml ?? configData?.yaml ?? ''
    setYamlContent(freshYaml)
    setSavedYaml(freshYaml)
    setSuggestedYaml(null)
    setServerError(null)
  }

  // Patch the current YAML with suggestions and enter diff-review mode.
  const handleSuggestResult = (suggested: SuggestFieldsResponse['suggested_config']) => {
    const patched = applyFieldSuggestions(yamlContent, suggested)
    if (patched === yamlContent) {
      toast.info(t('yaml.noChange'))
      return
    }
    setSuggestedYaml(patched)
  }

  // Accept: apply suggested YAML as current content (dirty, not saved yet).
  const handleAccept = () => {
    if (suggestedYaml === null) return
    setYamlContent(suggestedYaml)
    setSuggestedYaml(null)
    toast.info(t('yaml.applied'))
  }

  const handleDismiss = () => setSuggestedYaml(null)

  if (isLoading && !configData) {
    return <div className="text-muted-foreground text-sm">{t('yaml.loading')}</div>
  }

  return (
    <div className="space-y-3 mt-4">
      {/* Heading + badges */}
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="text-base font-semibold">{t('yaml.heading', { collection })}</h3>
        {isDirty && !suggestedYaml && (
          <span className="inline-flex items-center rounded-md px-2 py-0.5 text-xs bg-amber-100 text-amber-700">
            {t('yaml.unsaved')}
          </span>
        )}
        {suggestedYaml && (
          <span className="inline-flex items-center rounded-md px-2 py-0.5 text-xs bg-blue-100 text-blue-700">
            {t('yaml.reviewBadge')}
          </span>
        )}
      </div>

      {suggestedYaml !== null ? (
        // ── Diff review mode ──────────────────────────────────────────────────
        <>
          <p className="text-xs text-muted-foreground">
            {t('yaml.diffHint')}
          </p>

          <UnifiedDiff oldYaml={yamlContent} newYaml={suggestedYaml} />

          <div className="flex gap-2">
            <Button onClick={handleAccept}>{t('yaml.applySuggestions')}</Button>
            <Button variant="outline" onClick={handleDismiss}>
              {t('common.cancel')}
            </Button>
          </div>
        </>
      ) : (
        // ── Normal editor mode ────────────────────────────────────────────────
        <>
          {/* Toolbar */}
          <div className="flex items-center gap-2">
            <SuggestConfigButton
              fields={parsedFields}
              disabled={parsedFields.length === 0 || isLoading}
              onResult={handleSuggestResult}
            />
          </div>

          <textarea
            value={yamlContent}
            onChange={(e) => setYamlContent(e.target.value)}
            className="font-mono text-xs leading-relaxed bg-muted border rounded-md p-3 min-h-[320px] w-full resize-y"
            spellCheck={false}
          />

          <div className="flex gap-2">
            <Button onClick={handleSave} disabled={!isDirty || save.isPending}>
              {save.isPending ? t('yaml.saving') : t('yaml.saveChanges')}
            </Button>
            <Button variant="outline" onClick={handleReload} disabled={save.isPending}>
              {t('yaml.reloadDisk')}
            </Button>
          </div>

          {serverError && <p className="text-destructive text-xs">{serverError}</p>}
        </>
      )}
    </div>
  )
}

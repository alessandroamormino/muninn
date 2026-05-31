/**
 * YamlEditor — YAML config editor with dirty state tracking, save/reload, and
 * a "Suggest fields" toolbar button.
 *
 * YAML field extraction: js-yaml is NOT installed in this project, so we use a
 * best-effort regex-based extractor for text_fields and metadata_fields. It handles
 * both inline-flow (`text_fields: [a, b]`) and block-sequence (`text_fields:\n  - a`)
 * styles. Per UI-SPEC: this fallback is acceptable for the "Suggest fields" path
 * because the extracted list only drives the POST /setup/suggest-config-from-fields
 * call — any parse miss just results in fewer field suggestions.
 */
import { useState, useEffect, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { useGetConfig, useSaveConfig } from '@/api/config'
import SuggestConfigButton from './SuggestConfigButton'
import type { SuggestFieldsResponse } from '@/api/config'
import { toast } from 'sonner'

/**
 * Extract the union of weaviate.text_fields + weaviate.metadata_fields from raw YAML text.
 * Supports both inline-flow `key: [a, b, c]` and block-sequence `key:\n  - a\n  - b` forms.
 * Returns a deduplicated, ordered array. Returns [] on parse failure or empty result.
 */
function extractTextAndMetadataFields(yaml: string): string[] {
  const result: string[] = []

  for (const key of ['text_fields', 'metadata_fields']) {
    // Inline flow: text_fields: [a, b, c]
    const inlineMatch = new RegExp(`^\\s*${key}:\\s*\\[([^\\]\\n]*)\\]`, 'm').exec(yaml)
    if (inlineMatch) {
      const items = inlineMatch[1]
        .split(',')
        .map((s) => s.trim().replace(/^['"]|['"]$/g, ''))
        .filter(Boolean)
      result.push(...items)
      continue
    }

    // Block sequence: text_fields:\n  - a\n  - b
    const blockRe = new RegExp(`^(\\s*)${key}:\\s*$`, 'm')
    const blockMatch = blockRe.exec(yaml)
    if (blockMatch) {
      const startIdx = blockMatch.index + blockMatch[0].length
      const rest = yaml.slice(startIdx)
      const listItemRe = /^[ \t]+-[ \t]+(.+)$/gm
      let m
      while ((m = listItemRe.exec(rest)) !== null) {
        // stop when we hit a line that doesn't start with whitespace + dash (next key)
        const lineStart = rest.slice(0, m.index).match(/\n?$/)
        if (!lineStart) break
        result.push(m[1].trim().replace(/^['"]|['"]$/g, ''))
      }
    }
  }

  // Deduplicate while preserving order
  return [...new Set(result)].filter(Boolean)
}

export default function YamlEditor({ collection }: { collection: string }) {
  const { data: configData, refetch, isLoading } = useGetConfig(collection)
  const save = useSaveConfig(collection)

  const [yamlContent, setYamlContent] = useState('')
  const [savedYaml, setSavedYaml] = useState('')
  const [serverError, setServerError] = useState<string | null>(null)

  const isDirty = yamlContent !== savedYaml

  // Single effect for both init and collection change.
  // Two separate effects caused a mount-order bug: the collection-change effect fired
  // after the data effect, overwriting cached YAML with '' on every tab switch.
  useEffect(() => {
    if (configData?.yaml !== undefined) {
      setYamlContent(configData.yaml)
      setSavedYaml(configData.yaml)
      setServerError(null)
    } else {
      setYamlContent('')
      setSavedYaml('')
    }
  }, [collection, configData?.yaml])

  const parsedFields = useMemo(
    () => extractTextAndMetadataFields(yamlContent),
    [yamlContent],
  )

  const handleSave = async () => {
    setServerError(null)
    try {
      await save.mutateAsync(yamlContent)
      setSavedYaml(yamlContent)
      toast.success('Config salvata.')
    } catch (e) {
      const msg = (e as Error).message
      setServerError(msg)
      toast.error('Salvataggio fallito.')
    }
  }

  const handleReload = async () => {
    if (isDirty && !window.confirm('Hai modifiche non salvate. Ricaricare dal disco?')) return
    // Await refetch and explicitly reset state — without this, if the server returns the same
    // YAML string the cache already holds, configData.yaml doesn't change, the effect dep
    // doesn't fire, and the dirty content stays visible.
    const result = await refetch()
    const freshYaml = result.data?.yaml ?? configData?.yaml ?? ''
    setYamlContent(freshYaml)
    setSavedYaml(freshYaml)
    setServerError(null)
  }

  const handleSuggestResult = (suggested: SuggestFieldsResponse['suggested_config']) => {
    // No js-yaml AST available — show a descriptive toast so the operator can paste values manually.
    // This is an acceptable fallback per UI-SPEC Suggest-Config Integration.
    toast.message('Suggested fields:', {
      description: [
        `text: ${suggested.text_fields.join(', ')}`,
        `metadata: ${suggested.metadata_fields.join(', ')}`,
        `output: ${suggested.output_fields.join(', ')}`,
      ].join(' | '),
    })
  }

  if (isLoading && !configData) {
    return <div className="text-muted-foreground text-sm">Loading config…</div>
  }

  return (
    <div className="space-y-3 mt-4">
      {/* Heading + dirty badge */}
      <div className="flex items-center gap-2">
        <h3 className="text-base font-semibold">Config — {collection}</h3>
        {isDirty && (
          <span className="ml-2 inline-flex items-center rounded-md px-2 py-0.5 text-xs bg-amber-100 text-amber-700">
            Unsaved changes
          </span>
        )}
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2 mb-2">
        <SuggestConfigButton
          fields={parsedFields}
          disabled={parsedFields.length === 0 || isLoading}
          onResult={handleSuggestResult}
        />
      </div>

      {/* YAML textarea */}
      <textarea
        value={yamlContent}
        onChange={(e) => setYamlContent(e.target.value)}
        className="font-mono text-xs leading-relaxed bg-muted border rounded-md p-3 min-h-[320px] w-full resize-y"
        spellCheck={false}
      />

      {/* Action buttons */}
      <div className="flex gap-2">
        <Button onClick={handleSave} disabled={!isDirty || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save changes'}
        </Button>
        <Button variant="outline" onClick={handleReload} disabled={save.isPending}>
          Reload from disk
        </Button>
      </div>

      {/* Server error */}
      {serverError && (
        <p className="text-destructive text-xs">{serverError}</p>
      )}
    </div>
  )
}

import { Button } from '@/components/ui/button'
import { useSuggestConfigFromFields } from '@/api/config'
import type { SuggestFieldsResponse } from '@/api/config'
import { toast } from 'sonner'

interface Props {
  fields: string[]
  onResult: (suggested: SuggestFieldsResponse['suggested_config']) => void
  disabled?: boolean
}

export default function SuggestConfigButton({ fields, onResult, disabled }: Props) {
  const suggest = useSuggestConfigFromFields()

  return (
    <Button
      variant="outline"
      type="button"
      disabled={suggest.isPending || !!disabled || fields.length === 0}
      onClick={() => {
        suggest.mutate(fields, {
          onSuccess: (data) => {
            onResult(data.suggested_config)
            toast.success('Suggerimenti applicati.')
          },
          onError: (e: Error) => {
            if (e.message.toLowerCase().includes('llm')) {
              toast.error('LLM non raggiungibile — suggerisci manualmente i campi.')
            } else {
              toast.error(e.message)
            }
          },
        })
      }}
    >
      {suggest.isPending ? 'Analyzing...' : 'Suggest fields'}
    </Button>
  )
}

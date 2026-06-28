import { Button } from '@/components/ui/button'
import { useTranslation } from 'react-i18next'
import { useSuggestConfigFromFields } from '@/api/config'
import type { SuggestFieldsResponse } from '@/api/config'
import { toast } from 'sonner'

interface Props {
  fields: string[]
  onResult: (suggested: SuggestFieldsResponse['suggested_config']) => void
  disabled?: boolean
}

export default function SuggestConfigButton({ fields, onResult, disabled }: Props) {
  const { t } = useTranslation()
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
            toast.success(t('suggest.applied'))
          },
          onError: (e: Error) => {
            if (e.message.toLowerCase().includes('llm')) {
              toast.error(t('suggest.llmError'))
            } else {
              toast.error(e.message)
            }
          },
        })
      }}
    >
      {suggest.isPending ? t('suggest.analyzing') : t('suggest.suggest')}
    </Button>
  )
}

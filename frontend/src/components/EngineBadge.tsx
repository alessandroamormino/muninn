import { Badge } from '@/components/ui/badge'

interface Props {
  engine: string
}

export function EngineBadge({ engine }: Props) {
  const variant = engine === 'qdrant' ? 'outline' : 'secondary'
  const label = engine === 'qdrant' ? 'Qdrant' : 'Weaviate'
  return <Badge variant={variant}>{label}</Badge>
}

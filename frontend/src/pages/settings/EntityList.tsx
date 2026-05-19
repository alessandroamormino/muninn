import { Button } from '@/components/ui/button'

interface Props {
  collections: string[]
  selected: string | null
  onSelect: (c: string) => void
  onCreateCsv: () => void
  onCreateRestApi: () => void
}

export default function EntityList({ collections, selected, onSelect, onCreateCsv, onCreateRestApi }: Props) {
  return (
    <div className="flex flex-col h-full">
      {collections.length === 0 ? (
        <div className="text-sm text-muted-foreground py-4 flex-1">
          <div className="font-medium mb-1">No entities configured</div>
          <div>Add your first entity by uploading a CSV or connecting a REST API.</div>
        </div>
      ) : (
        <ul className="flex-1 space-y-1 overflow-y-auto">
          {collections.map((c) => (
            <li key={c}>
              <button
                onClick={() => onSelect(c)}
                className={`w-full text-left px-3 py-2 rounded-md text-sm ${
                  selected === c ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
                }`}
              >
                {c}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="space-y-2 pt-3 border-t">
        <Button size="sm" className="w-full" onClick={onCreateCsv}>Upload CSV</Button>
        <Button size="sm" variant="outline" className="w-full" onClick={onCreateRestApi}>Add REST API</Button>
      </div>
    </div>
  )
}

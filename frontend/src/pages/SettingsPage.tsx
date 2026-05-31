import { useState } from 'react'
import { useCollections } from '@/api/collections'
import EntityList from './settings/EntityList'
import UploadWizard from './settings/UploadWizard'
import RestApiForm from './settings/RestApiForm'
import SyncTab from './settings/SyncTab'
import YamlEditor from './settings/YamlEditor'
import EntityInfoPanel from './settings/EntityInfoPanel'
import LogsTab from './settings/LogsTab'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'

type Mode =
  | { kind: 'idle' }
  | { kind: 'view'; collection: string }
  | { kind: 'new'; sourceType: 'csv' | 'rest_api' | 'mysql' }

export default function SettingsPage() {
  const { data } = useCollections()
  const [mode, setMode] = useState<Mode>({ kind: 'idle' })

  return (
    <div className="flex gap-6 h-full">
      <Card className="w-80 p-4 flex flex-col gap-3">
        <h2 className="text-base font-semibold">Entities</h2>
        <EntityList
          collections={data?.collections ?? []}
          selected={mode.kind === 'view' ? mode.collection : null}
          onSelect={(c) => setMode({ kind: 'view', collection: c })}
          onCreateCsv={() => setMode({ kind: 'new', sourceType: 'csv' })}
          onCreateRestApi={() => setMode({ kind: 'new', sourceType: 'rest_api' })}
          onCreateMySQL={() => setMode({ kind: 'new', sourceType: 'mysql' })}
        />
      </Card>
      <Card className="flex-1 p-6 overflow-y-auto">
        {mode.kind === 'idle' && (
          <div className="text-sm text-muted-foreground">
            Select an entity from the left panel, or add a new one.
          </div>
        )}
        {mode.kind === 'new' && mode.sourceType === 'csv' && (
          <UploadWizard onDone={(c) => setMode({ kind: 'view', collection: c })} />
        )}
        {mode.kind === 'new' && mode.sourceType === 'rest_api' && (
          <RestApiForm onDone={(c) => setMode({ kind: 'view', collection: c })} />
        )}
        {mode.kind === 'new' && mode.sourceType === 'mysql' && (
          <div className="text-sm text-muted-foreground">
            <p className="font-medium text-foreground mb-2">MySQL source</p>
            <p>
              Crea manualmente il file{' '}
              <code className="font-mono text-xs">configuration/{'<NomeEntity>'}/config.yaml</code>{' '}
              con <code className="font-mono text-xs">source.type: mysql</code> e le credenziali via variabili d'ambiente.
            </p>
            <p className="mt-2">
              Consulta la documentazione in <code className="font-mono text-xs">CLAUDE.md</code> per la struttura completa del config.
            </p>
          </div>
        )}
        {mode.kind === 'view' && (
          <Tabs defaultValue="config">
            <TabsList>
              <TabsTrigger value="config">Config</TabsTrigger>
              <TabsTrigger value="info">Info</TabsTrigger>
              <TabsTrigger value="sync">Sync</TabsTrigger>
              <TabsTrigger value="logs">Logs</TabsTrigger>
            </TabsList>
            <TabsContent value="config">
              <YamlEditor collection={mode.collection} />
            </TabsContent>
            <TabsContent value="info">
              <EntityInfoPanel collection={mode.collection} />
            </TabsContent>
            <TabsContent value="sync">
              <SyncTab collection={mode.collection} />
            </TabsContent>
            <TabsContent value="logs">
              <LogsTab collection={mode.collection} />
            </TabsContent>
          </Tabs>
        )}
      </Card>
    </div>
  )
}

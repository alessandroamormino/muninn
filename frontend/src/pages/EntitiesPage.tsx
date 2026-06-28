import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useCollections } from '@/api/collections'
import EntityList from './settings/EntityList'
import UploadWizard from './settings/UploadWizard'
import RestApiForm from './settings/RestApiForm'
import MySQLWizard from './settings/MySQLWizard'
import SyncTab from './settings/SyncTab'
import YamlEditor from './settings/YamlEditor'
import EntityInfoPanel from './settings/EntityInfoPanel'
import LogsTab from './settings/LogsTab'
import BackupTab from './settings/BackupTab'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'

type Mode =
  | { kind: 'idle' }
  | { kind: 'view'; collection: string }
  | { kind: 'new'; sourceType: 'csv' | 'rest_api' | 'mysql' }

export default function EntitiesPage() {
  const { t } = useTranslation()
  const { data } = useCollections()
  const [mode, setMode] = useState<Mode>({ kind: 'idle' })

  return (
    <div className="flex gap-6 h-full">
      <Card className="w-80 p-4 flex flex-col gap-3">
        <h2 className="text-base font-semibold">{t('entities.title')}</h2>
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
            {t('entities.idle')}
          </div>
        )}
        {mode.kind === 'new' && mode.sourceType === 'csv' && (
          <UploadWizard onDone={(c) => setMode({ kind: 'view', collection: c })} />
        )}
        {mode.kind === 'new' && mode.sourceType === 'rest_api' && (
          <RestApiForm onDone={(c) => setMode({ kind: 'view', collection: c })} />
        )}
        {mode.kind === 'new' && mode.sourceType === 'mysql' && (
          <MySQLWizard
            onDone={(collection) => setMode({ kind: 'view', collection })}
            onCancel={() => setMode({ kind: 'idle' })}
          />
        )}
        {mode.kind === 'view' && (
          <Tabs defaultValue="config">
            <TabsList>
              <TabsTrigger value="config">{t('entities.tab.config')}</TabsTrigger>
              <TabsTrigger value="info">{t('entities.tab.info')}</TabsTrigger>
              <TabsTrigger value="sync">{t('entities.tab.sync')}</TabsTrigger>
              <TabsTrigger value="logs">{t('entities.tab.logs')}</TabsTrigger>
              <TabsTrigger value="backup">{t('entities.tab.backup')}</TabsTrigger>
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
            <TabsContent value="backup">
              <BackupTab collection={mode.collection} />
            </TabsContent>
          </Tabs>
        )}
      </Card>
    </div>
  )
}

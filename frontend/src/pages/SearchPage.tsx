import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import EntityDropdown from '@/components/EntityDropdown'
import SearchBar from './search/SearchBar'
import FilterBar from './search/FilterBar'
import ResultCard from './search/ResultCard'
import SearchModeSelector from './search/SearchModeSelector'
import { useSearch } from '@/api/search'
import { useEntityInfo } from '@/api/config'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'

const PAGE_SIZE = 10

export default function SearchPage() {
  const { t } = useTranslation()
  const [collection, setCollection] = useState<string | null>(null)
  const [q, setQ] = useState<string>('')
  const [filter, setFilter] = useState<string>('')
  const [minScore, setMinScore] = useState<number | null>(null)
  const [searchMode, setSearchMode] = useState<string>('hybrid')
  const [page, setPage] = useState(0)

  const { data: entityInfo } = useEntityInfo(collection)
  const configuredMode = entityInfo?.search_mode ?? 'hybrid'

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setSearchMode(configuredMode)
    setPage(0)
  }, [collection, configuredMode])

  // Reset page when query changes
  useEffect(() => { setPage(0) }, [q, filter, minScore, searchMode])
  /* eslint-enable react-hooks/set-state-in-effect */

  const search = useSearch({
    q,
    collection,
    filter: filter || null,
    min_score: minScore,
    limit: entityInfo?.max_limit ?? undefined,
    search_mode: entityInfo?.vector_store_engine === 'qdrant' ? searchMode : undefined,
  })

  const allResults = search.data?.results ?? []
  const totalResults = allResults.length
  const totalPages = Math.ceil(totalResults / PAGE_SIZE)
  const pageResults = allResults.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const errMsg = (() => {
    const e = search.error as Error | undefined
    if (!e) return null
    if (/HTTP 422/i.test(e.message)) {
      const match = e.message.match(/HTTP 422[^"]*?"detail":\s*"([^"]+)"/)
      return match ? match[1] : e.message
    }
    // Phase 26 — entity unloaded: surface the backend detail (it tells the user to reload it).
    if (/HTTP 409/i.test(e.message)) {
      const match = e.message.match(/HTTP 409[^"]*?"detail":\s*"([^"]+)"/)
      return match ? match[1] : t('search.errUnloaded')
    }
    if (/HTTP 404/i.test(e.message)) return t('search.errNoConfig')
    return t('search.errUnreachable')
  })()

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t('search.title')}</h1>
      <EntityDropdown activeOnly value={collection} onChange={(c) => { setCollection(c); setFilter(''); setMinScore(null); setPage(0) }} />
      <SearchBar
        placeholder={collection ? t('search.placeholder', { collection }) : t('search.selectFirst')}
        onSubmit={setQ}
        disabled={!collection}
        collection={collection}
      />
      <FilterBar
        key={collection}
        filter={filter}
        minScore={minScore}
        fields={entityInfo?.metadata_fields ?? []}
        onChange={({ filter: f, minScore: m }) => { setFilter(f); setMinScore(m) }}
      />

      {collection && entityInfo?.vector_store_engine === 'qdrant' && (
        <SearchModeSelector
          value={searchMode}
          onChange={(m) => { setSearchMode(m); setPage(0) }}
          disabled={search.isFetching}
          configuredMode={configuredMode}
        />
      )}

      {!q && (
        <div className="text-sm text-muted-foreground py-8 text-center">
          {t('search.typePrompt')}
        </div>
      )}

      {q && search.isPending && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-24 w-full" />)}
        </div>
      )}

      {q && errMsg && (
        <div className="border border-destructive/30 bg-destructive/5 text-destructive p-4 rounded-md text-sm">
          {errMsg}
        </div>
      )}

      {q && search.data && !errMsg && (
        <>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {t('search.results', { n: totalResults, ms: search.data.took_ms })}
              {totalPages > 1 && ` — ${t('search.pageOf', { page: page + 1, total: totalPages })}`}
            </span>
          </div>

          {totalResults === 0 ? (
            <div className="text-center py-8">
              <h3 className="text-base font-semibold mb-1">{t('search.noResults')}</h3>
              <p className="text-sm text-muted-foreground">
                {t('search.noResultsHint')}
              </p>
            </div>
          ) : (
            <>
              <div className="space-y-3">
                {pageResults.map((r, i) => (
                  <ResultCard key={`${page}-${i}-${r._score}`} result={r} />
                ))}
              </div>

              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 pt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                  >
                    {t('search.prev')}
                  </Button>
                  <span className="text-xs text-muted-foreground px-2">
                    {page + 1} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={page >= totalPages - 1}
                  >
                    {t('search.next')}
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}

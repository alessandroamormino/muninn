import { useState, useEffect } from 'react'
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
    if (/HTTP 404/i.test(e.message)) return 'No config found for this collection.'
    return 'Could not reach the search service. Check that the orchestrator container is running.'
  })()

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Search</h1>
      <EntityDropdown value={collection} onChange={(c) => { setCollection(c); setPage(0) }} />
      <SearchBar
        placeholder={collection ? `Search across ${collection}...` : 'Select a collection first'}
        onSubmit={setQ}
        disabled={!collection}
        collection={collection}
      />
      <FilterBar
        filter={filter}
        minScore={minScore}
        onChange={({ filter: f, minScore: m }) => { setFilter(f); setMinScore(m) }}
      />

      {collection && entityInfo?.vector_store_engine === 'qdrant' && (
        <SearchModeSelector
          value={searchMode}
          onChange={(m) => { setSearchMode(m); setPage(0) }}
          disabled={search.isPending}
          configuredMode={configuredMode}
        />
      )}

      {!q && (
        <div className="text-sm text-muted-foreground py-8 text-center">
          Type a query and press Search.
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
              {totalResults} results in {search.data.took_ms} ms
              {totalPages > 1 && ` — page ${page + 1} of ${totalPages}`}
            </span>
          </div>

          {totalResults === 0 ? (
            <div className="text-center py-8">
              <h3 className="text-base font-semibold mb-1">No results found</h3>
              <p className="text-sm text-muted-foreground">
                Try a different query or lower the minimum score threshold.
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
                    ← Prev
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
                    Next →
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

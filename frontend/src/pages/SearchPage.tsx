import { useState } from 'react'
import EntityDropdown from '@/components/EntityDropdown'
import SearchBar from './search/SearchBar'
import FilterBar from './search/FilterBar'
import ResultCard from './search/ResultCard'
import { useSearch } from '@/api/search'
import { Skeleton } from '@/components/ui/skeleton'

export default function SearchPage() {
  const [collection, setCollection] = useState<string | null>(null)
  const [q, setQ] = useState<string>('')
  const [filter, setFilter] = useState<string>('')
  const [minScore, setMinScore] = useState<number | null>(null)

  const search = useSearch({
    q,
    collection,
    filter: filter || null,
    min_score: minScore,
  })

  const errMsg = (() => {
    const e = search.error as Error | undefined
    if (!e) return null
    // 422 messages already contain server-side detail
    if (/HTTP 422/i.test(e.message)) {
      const match = e.message.match(/HTTP 422[^"]*?"detail":\s*"([^"]+)"/)
      return match ? match[1] : e.message
    }
    if (/HTTP 404/i.test(e.message)) return 'No config found for this collection.'
    return 'Could not reach the search service. Check that the orchestrator container is running.'
  })()

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <h1 className="text-xl font-semibold">Search</h1>
      <EntityDropdown value={collection} onChange={setCollection} />
      <SearchBar
        placeholder={collection ? `Search across ${collection}...` : 'Select a collection first'}
        onSubmit={setQ}
        disabled={!collection}
      />
      <FilterBar
        filter={filter}
        minScore={minScore}
        onChange={({ filter: f, minScore: m }) => { setFilter(f); setMinScore(m) }}
      />

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
          <div className="text-xs text-muted-foreground">
            {search.data.results.length} results in {search.data.took_ms} ms
          </div>
          {search.data.results.length === 0 ? (
            <div className="text-center py-8">
              <h3 className="text-base font-semibold mb-1">No results found</h3>
              <p className="text-sm text-muted-foreground">
                Try a different query or lower the minimum score threshold.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {search.data.results.map((r, i) => (
                <ResultCard key={`${i}-${r._score}`} result={r} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

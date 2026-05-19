import { NavLink } from 'react-router'
import { useCollections } from '@/api/collections'
import { Separator } from '@/components/ui/separator'

const NAV = [
  { to: '/search', label: 'Search' },
  { to: '/settings', label: 'Settings' },
  { to: '/logs', label: 'Logs' },
  { to: '/graph', label: 'Knowledge Graph' },
]

export default function Sidebar() {
  const { data, isLoading, isError } = useCollections()
  return (
    <aside className="w-60 border-r bg-card flex flex-col">
      <div className="p-4 text-base font-semibold">smart-search</div>
      <Separator />
      <nav className="flex flex-col p-2 gap-1">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) =>
              `px-3 py-2 rounded-md text-sm ${
                isActive
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-muted text-foreground'
              }`
            }
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
      <Separator />
      <div className="p-4 text-xs text-muted-foreground">
        <div className="font-medium mb-1">Entities</div>
        {isLoading && <div>Loading...</div>}
        {isError && <div className="text-destructive">Unreachable</div>}
        {data?.collections?.length === 0 && <div>None configured</div>}
        <ul className="space-y-1">
          {data?.collections?.map((c) => (
            <li key={c} className="font-mono">{c}</li>
          ))}
        </ul>
      </div>
    </aside>
  )
}

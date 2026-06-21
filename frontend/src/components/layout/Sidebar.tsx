import { NavLink } from 'react-router'
import { Separator } from '@/components/ui/separator'

const NAV = [
  { to: '/search', label: 'Search' },
  { to: '/entities', label: 'Entities' },
  { to: '/settings', label: 'Settings' },
  { to: '/logs', label: 'Logs' },
  { to: '/graph', label: 'Knowledge Graph' },
]

export default function Sidebar() {
  return (
    <aside className="w-60 border-r bg-card flex flex-col">
      <div className="h-14 border-b flex items-center px-4 text-base font-semibold">smart-search</div>
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
    </aside>
  )
}

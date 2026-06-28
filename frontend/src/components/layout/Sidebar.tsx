import { NavLink } from 'react-router'
import { useTranslation } from 'react-i18next'
import { Separator } from '@/components/ui/separator'
import LanguageToggle from '@/components/LanguageToggle'

const NAV = [
  { to: '/search', key: 'nav.search' },
  { to: '/entities', key: 'nav.entities' },
  { to: '/settings', key: 'nav.settings' },
  { to: '/logs', key: 'nav.logs' },
  { to: '/graph', key: 'nav.graph' },
]

export default function Sidebar() {
  const { t } = useTranslation()
  return (
    <aside className="w-60 border-r bg-card flex flex-col">
      <div className="h-14 border-b flex items-center gap-2 px-4 text-base font-semibold">
        <img src="/muninn.svg" alt="" className="h-7 w-7" />
        {t('app.title')}
      </div>
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
            {t(n.key)}
          </NavLink>
        ))}
      </nav>
      <Separator />
      <div className="mt-auto p-3 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{t('app.language')}</span>
        <LanguageToggle />
      </div>
    </aside>
  )
}

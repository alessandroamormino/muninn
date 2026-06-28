import { useTranslation } from 'react-i18next'
import { setLang, LANGS, type Lang } from '@/i18n'

// Compact IT/EN segmented switch. Persists to localStorage via setLang.
export default function LanguageToggle() {
  const { i18n } = useTranslation()
  const current = i18n.language as Lang

  return (
    <div className="flex items-center gap-0.5 rounded-md border p-0.5" role="group" aria-label="Language">
      {LANGS.map((l) => (
        <button
          key={l}
          onClick={() => setLang(l)}
          aria-pressed={current === l}
          className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
            current === l ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
          }`}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  )
}

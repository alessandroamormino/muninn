import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'
import it from './locales/it.json'

// Translations are static JSON bundled at build time; the chosen language is
// persisted in localStorage (the frontend is a static SPA — no DB, no backend call).
export const LANGS = ['en', 'it'] as const
export type Lang = (typeof LANGS)[number]

const STORE_KEY = 'lang'
const saved = (localStorage.getItem(STORE_KEY) as Lang | null)
const initial: Lang = saved && LANGS.includes(saved) ? saved : 'en'

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    it: { translation: it },
  },
  lng: initial,
  fallbackLng: 'en',
  interpolation: { escapeValue: false }, // React already escapes
})

export function setLang(lang: Lang): void {
  localStorage.setItem(STORE_KEY, lang)
  i18n.changeLanguage(lang)
}

export default i18n

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import sv from './sv.js';
import en from './en.js';

const DICTIONARIES = { sv, en };
const STORAGE_KEY = 'bb_lang';
const DEFAULT_LANG = 'sv';

const I18nContext = createContext(null);

function loadInitialLang() {
  if (typeof window === 'undefined') return DEFAULT_LANG;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored && DICTIONARIES[stored] ? stored : DEFAULT_LANG;
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(loadInitialLang);

  useEffect(() => {
    document.documentElement.setAttribute('lang', lang);
    try {
      window.localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // Privat läge / quota — strunt samma.
    }
  }, [lang]);

  const setLang = useCallback((next) => {
    if (DICTIONARIES[next]) setLangState(next);
  }, []);

  const value = useMemo(
    () => ({ lang, setLang, t: DICTIONARIES[lang] }),
    [lang, setLang],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n måste användas inuti <I18nProvider>');
  return ctx;
}

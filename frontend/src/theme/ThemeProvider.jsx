import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

const STORAGE_KEY = 'bb_variant';
const DENSITY_KEY = 'bb_density';
const VARIANTS = ['A', 'B'];
const DEFAULT_VARIANT = 'A';
const DEFAULT_DENSITY = 'cozy';

const ThemeContext = createContext(null);

function loadVariant() {
  if (typeof window === 'undefined') return DEFAULT_VARIANT;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored && VARIANTS.includes(stored) ? stored : DEFAULT_VARIANT;
}

function loadDensity() {
  if (typeof window === 'undefined') return DEFAULT_DENSITY;
  const stored = window.localStorage.getItem(DENSITY_KEY);
  return stored === 'compact' ? 'compact' : DEFAULT_DENSITY;
}

function applyDensity(density) {
  const root = document.documentElement;
  root.setAttribute('data-density', density);
  root.style.setProperty('--pad', density === 'compact' ? '0.5rem' : '0.75rem');
  root.style.setProperty('--pad-2', density === 'compact' ? '0.75rem' : '1.125rem');
  root.style.setProperty('--row-h', density === 'compact' ? '36px' : '44px');
}

export function ThemeProvider({ children }) {
  const [variant, setVariantState] = useState(loadVariant);
  const [density, setDensityState] = useState(loadDensity);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', variant);
    try {
      window.localStorage.setItem(STORAGE_KEY, variant);
    } catch {
      // ignore
    }
  }, [variant]);

  useEffect(() => {
    applyDensity(density);
    try {
      window.localStorage.setItem(DENSITY_KEY, density);
    } catch {
      // ignore
    }
  }, [density]);

  const setVariant = useCallback((next) => {
    if (VARIANTS.includes(next)) setVariantState(next);
  }, []);

  const setDensity = useCallback((next) => {
    if (next === 'compact' || next === 'cozy') setDensityState(next);
  }, []);

  const value = useMemo(
    () => ({ variant, setVariant, density, setDensity }),
    [variant, density, setVariant, setDensity],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme måste användas inuti <ThemeProvider>');
  return ctx;
}

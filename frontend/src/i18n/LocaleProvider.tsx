import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { api } from "../api/client";
import {
  activateLocale,
  isLocale,
  readStoredLocale,
  SOURCE_LOCALE,
  storeLocale,
  type Locale,
} from "./locale";

/** Response of GET/PUT /api/i18n/language. */
interface LanguageState {
  language: string | null;
  supported: string[];
}

interface LocaleContextValue {
  locale: Locale;
  /** Switch the UI now and persist the choice (localStorage + backend). Rejects if the backend save fails. */
  setLocale: (locale: Locale) => Promise<void>;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

/**
 * Activates the UI language before rendering the app and keeps it in step with the backend.
 *
 * Start-up order: the language remembered in this browser (or English) renders at once; then the
 * backend setting, the durable store, wins if it differs — a cleared browser or another browser
 * still gets the user's choice. A browser-only choice with no backend setting yet is copied to it.
 */
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function start() {
      const stored = readStoredLocale();
      const initial = stored ?? SOURCE_LOCALE;
      await activateLocale(initial);
      if (cancelled) return;
      setLocaleState(initial);

      let backend: LanguageState;
      try {
        backend = await api.get<LanguageState>("/api/i18n/language");
      } catch {
        return; // Backend down: the footer reports it; keep the local choice.
      }
      if (cancelled) return;
      if (isLocale(backend.language)) {
        if (backend.language !== initial) {
          await activateLocale(backend.language);
          storeLocale(backend.language);
          if (!cancelled) setLocaleState(backend.language);
        }
      } else if (stored) {
        await api
          .put<LanguageState>("/api/i18n/language", { language: stored })
          .catch(() => undefined);
      }
    }

    void start();
    return () => {
      cancelled = true;
    };
  }, []);

  if (locale === null) {
    return null; // First activation takes one tick (catalog import); avoids a flash of English.
  }

  const setLocale = async (next: Locale) => {
    await activateLocale(next);
    storeLocale(next);
    setLocaleState(next);
    await api.put<LanguageState>("/api/i18n/language", { language: next });
  };

  return (
    <LocaleContext value={{ locale, setLocale }}>
      <I18nProvider i18n={i18n}>{children}</I18nProvider>
    </LocaleContext>
  );
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) {
    throw new Error("useLocale must be used inside <LocaleProvider>");
  }
  return value;
}

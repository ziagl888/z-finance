/**
 * UI translations (Lingui). English is the source language: the text inside the macros is the
 * English UI and there is no English catalog. Other languages load `src/locales/<locale>.po`,
 * which `@lingui/vite-plugin` compiles at build time. See ARCHITECTURE.md "Internationalisation".
 */
import { i18n, type Messages } from "@lingui/core";
import { compileMessage } from "@lingui/message-utils/compileMessage";

/** Supported UI languages with their names in their own language (never translated). */
export const LOCALES = { en: "English", de: "Deutsch" } as const;
export type Locale = keyof typeof LOCALES;
export const SOURCE_LOCALE: Locale = "en";

const STORAGE_KEY = "z-finance.locale";

// Without a catalog, English renders the message text the macros keep in the bundle
// (descriptorFields "message", vite.config.ts). Lingui installs the compiler for such raw messages
// only in development builds, so install it for production too.
i18n.setMessagesCompiler(compileMessage);

const catalogs = import.meta.glob<{ messages: Messages }>("../locales/*.po");

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && Object.hasOwn(LOCALES, value);
}

/** Load the catalog for `locale` (none for English) and make it the active language. */
export async function activateLocale(locale: Locale): Promise<void> {
  const load = catalogs[`../locales/${locale}.po`];
  const messages = load ? (await load()).messages : {};
  i18n.loadAndActivate({ locale, messages });
  document.documentElement.lang = locale;
}

/** The language remembered in this browser, if any. Storage can be unavailable (privacy modes). */
export function readStoredLocale(): Locale | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return isLocale(stored) ? stored : null;
  } catch {
    return null;
  }
}

export function storeLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // Not persisted in this browser; the backend setting still carries the choice.
  }
}

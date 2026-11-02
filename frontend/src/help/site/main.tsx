/**
 * Entry of the GitHub Pages manual (vite.manual.config.ts): the in-app Handbook without the app.
 * Language and chapter live in the URL hash, `#<lang>/<chapter>`, so every chapter has a link.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  activateLocale,
  isLocale,
  LOCALES,
  SOURCE_LOCALE,
  type Locale,
} from "../../i18n/locale";
import { Handbook } from "../Handbook";
import { START_CHAPTER } from "../manual";
import "../../styles/index.css";
import "../help.css";

interface Location {
  locale: Locale;
  slug: string;
}

function readHash(): Location {
  const [lang = "", slug = ""] = window.location.hash
    .replace(/^#\/?/, "")
    .split("/");
  const fromBrowser = navigator.language.slice(0, 2);
  return {
    locale: isLocale(lang)
      ? lang
      : isLocale(fromBrowser)
        ? fromBrowser
        : SOURCE_LOCALE,
    slug: decodeURIComponent(slug) || START_CHAPTER,
  };
}

function ManualSite() {
  const [location, setLocation] = useState(readHash);

  useEffect(() => {
    const onHash = () => {
      const next = readHash();
      void activateLocale(next.locale).then(() => setLocation(next));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (locale: Locale, slug: string) => {
    window.location.hash = `${locale}/${encodeURIComponent(slug)}`;
    window.scrollTo(0, 0);
  };

  return (
    <div className="shell">
      <header className="shell-header">
        <span className="brand">z-finance</span>
        <nav aria-label="Language">
          <ul>
            {Object.entries(LOCALES).map(([code, name]) => (
              <li key={code}>
                <a
                  href={`#${code}/${location.slug}`}
                  lang={code}
                  className={code === location.locale ? "active" : undefined}
                >
                  {name}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main className="shell-main">
        <Handbook
          slug={location.slug}
          onSelect={(slug) => go(location.locale, slug)}
        />
      </main>
    </div>
  );
}

const root = document.getElementById("root");
if (!root) {
  throw new Error('index.html is missing <div id="root">');
}

void activateLocale(readHash().locale).then(() =>
  createRoot(root).render(
    <StrictMode>
      <I18nProvider i18n={i18n}>
        <ManualSite />
      </I18nProvider>
    </StrictMode>,
  ),
);

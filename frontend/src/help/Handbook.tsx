import { Trans, useLingui } from "@lingui/react/macro";
import { useState } from "react";

import { ChapterView } from "./ChapterView";
import { manual as embedded, START_CHAPTER, type Manual } from "./manual";

/**
 * The manual view: chapter navigation with full-text search beside the selected chapter, in the
 * active UI language. Used by the in-app manual page and by the GitHub Pages site; the caller owns
 * where the selected chapter lives (URL query, hash).
 */
export function Handbook({
  slug,
  onSelect,
  manual = embedded,
}: {
  slug: string;
  onSelect: (slug: string) => void;
  manual?: Manual;
}) {
  const { i18n, t } = useLingui();
  const lang = i18n.locale;
  const [query, setQuery] = useState("");

  const chapters = manual.chapters(lang);
  const current =
    manual.chapter(lang, slug) ?? manual.chapter(lang, START_CHAPTER);
  const hits = query.trim() ? manual.search(lang, query) : null;

  return (
    <div className="handbook">
      <nav className="handbook-nav" aria-label={t`Manual chapters`}>
        <input
          type="search"
          className="handbook-search"
          placeholder={t`Search the manual`}
          aria-label={t`Search the manual`}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {hits === null ? (
          <ul>
            {chapters.map((c) => (
              <li key={c.slug}>
                <button
                  type="button"
                  className="help-link"
                  aria-current={c.slug === current?.slug ? "page" : undefined}
                  onClick={() => onSelect(c.slug)}
                >
                  {c.title}
                </button>
              </li>
            ))}
          </ul>
        ) : hits.length === 0 ? (
          <p className="help-muted" role="status">
            <Trans>No chapter contains these words.</Trans>
          </p>
        ) : (
          <ul className="handbook-hits" role="status">
            {hits.map(({ chapter, snippet }) => (
              <li key={chapter.slug}>
                <button
                  type="button"
                  className="help-link"
                  aria-current={
                    chapter.slug === current?.slug ? "page" : undefined
                  }
                  onClick={() => onSelect(chapter.slug)}
                >
                  {chapter.title}
                </button>
                {snippet && <p className="help-muted">{snippet}</p>}
              </li>
            ))}
          </ul>
        )}
      </nav>
      <div className="handbook-content">
        {current ? (
          <ChapterView chapter={current} onNavigate={onSelect} />
        ) : (
          <p>
            <Trans>The manual has no chapters yet.</Trans>
          </p>
        )}
      </div>
    </div>
  );
}

import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { ChapterView } from "./ChapterView";
import { chapterForPath, manual as embedded, type Manual } from "./manual";
import { manualHref } from "./ManualPage";

/**
 * The help button in the layout header. Opens a side panel with the chapter of the current
 * screen (UC NF11); the panel follows route changes while it is open.
 */
export function HelpButton({ manual = embedded }: { manual?: Manual }) {
  const { i18n, t } = useLingui();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const slug = chapterForPath(pathname);
  const chapter = manual.chapter(i18n.locale, slug);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) =>
      event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const openInManual = (target: string) => {
    setOpen(false);
    void navigate(manualHref(target));
  };

  return (
    <>
      <button
        type="button"
        className="help-button"
        aria-label={t`Help for this page`}
        title={t`Help for this page`}
        aria-expanded={open}
        aria-controls="help-panel"
        onClick={() => setOpen((value) => !value)}
      >
        ?
      </button>
      {open && (
        <aside id="help-panel" className="help-panel" aria-label={t`Help`}>
          <div className="help-panel-bar">
            <strong>
              <Trans>Help</Trans>
            </strong>
            <Link to={manualHref(slug)} onClick={() => setOpen(false)}>
              <Trans>Open in the manual</Trans>
            </Link>
            <button
              type="button"
              className="help-link"
              onClick={() => setOpen(false)}
            >
              <Trans>Close</Trans>
            </button>
          </div>
          {chapter ? (
            <ChapterView chapter={chapter} onNavigate={openInManual} />
          ) : (
            <p>
              <Trans>There is no help for this page yet.</Trans>
            </p>
          )}
        </aside>
      )}
    </>
  );
}

import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";

import { useLocale } from "../../i18n";
import { Button, Card, Table } from "../../ui";
import {
  AREAS,
  CATEGORY_KINDS,
  completeSetup,
  getSetup,
  type Area,
  type CategoryDraft,
  type CategoryKind,
} from "./accountsApi";
import { AREA_LABELS, CATEGORY_KIND_LABELS } from "./labels";

interface Props {
  /** Called after the set was adopted or skipped, to reload the categories. */
  onDone: () => Promise<void>;
  onError: (reason: unknown) => void;
}

/**
 * First run (UC-ZF-001 step 4, AK3): offers the default category set in the active UI language
 * as an editable list — rename, change kind or area, remove, add — then adopts it or skips it.
 * Shown only while the backend offers the set (no categories yet, setup not finished).
 */
export function FirstRunPanel({ onDone, onError }: Props) {
  const { t, i18n } = useLingui();
  const { locale } = useLocale();
  const [drafts, setDrafts] = useState<CategoryDraft[] | null>(null);
  const [busy, setBusy] = useState(false);

  // Reloaded on a language switch while the offer is open: the set is offered in the UI language.
  useEffect(() => {
    let cancelled = false;
    getSetup(locale)
      .then((state) => {
        if (!cancelled) {
          setDrafts(state.offer_default_set ? state.default_categories : null);
        }
      })
      .catch((reason: unknown) => !cancelled && onError(reason));
    return () => {
      cancelled = true;
    };
  }, [locale, onError]);

  if (drafts === null) return null;

  const change = (index: number, changes: Partial<CategoryDraft>) =>
    setDrafts(
      drafts.map((draft, i) =>
        i === index ? { ...draft, ...changes } : draft,
      ),
    );

  async function finish(categories: CategoryDraft[]) {
    setBusy(true);
    try {
      await completeSetup(categories);
      setDrafts(null);
      await onDone();
    } catch (reason) {
      onError(reason);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      className="accounts-first-run"
      role="region"
      aria-labelledby="first-run-title"
    >
      <h2 id="first-run-title">
        <Trans>Start with the default categories?</Trans>
      </h2>
      <p>
        <Trans>
          z-finance suggests a set of categories for private and business use.
          Change names, kinds or areas, remove what you do not need and add your
          own. Nothing is saved until you click “Adopt categories”.
        </Trans>
      </p>
      <Table>
        <thead>
          <tr>
            <th>
              <Trans>Name</Trans>
            </th>
            <th>
              <Trans>Kind</Trans>
            </th>
            <th>
              <Trans>Area</Trans>
            </th>
            <th>
              <span className="sr-only">
                <Trans>Actions</Trans>
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {drafts.map((draft, index) => (
            <tr key={index}>
              <td>
                <input
                  aria-label={t`Name`}
                  required
                  maxLength={100}
                  value={draft.name}
                  onChange={(event) =>
                    change(index, { name: event.target.value })
                  }
                />
              </td>
              <td>
                <select
                  aria-label={t`Kind`}
                  value={draft.kind}
                  onChange={(event) =>
                    change(index, { kind: event.target.value as CategoryKind })
                  }
                >
                  {CATEGORY_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {i18n._(CATEGORY_KIND_LABELS[kind])}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <select
                  aria-label={t`Area`}
                  value={draft.area ?? ""}
                  onChange={(event) =>
                    change(index, {
                      area: (event.target.value || null) as Area | null,
                    })
                  }
                >
                  <option value="">{t`Both areas`}</option>
                  {AREAS.map((area) => (
                    <option key={area} value={area}>
                      {i18n._(AREA_LABELS[area])}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <button
                  type="button"
                  onClick={() =>
                    setDrafts(drafts.filter((_, i) => i !== index))
                  }
                >
                  <Trans>Remove</Trans>
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
      <div className="ui-actions">
        <button
          type="button"
          onClick={() =>
            setDrafts([...drafts, { name: "", kind: "expense", area: null }])
          }
        >
          <Trans>Add category</Trans>
        </button>
        <Button
          variant="primary"
          type="button"
          disabled={busy || drafts.some((draft) => !draft.name.trim())}
          onClick={() => void finish(drafts)}
        >
          <Trans>Adopt categories</Trans>
        </Button>
        <button type="button" disabled={busy} onClick={() => void finish([])}>
          <Trans>Skip</Trans>
        </button>
      </div>
    </Card>
  );
}

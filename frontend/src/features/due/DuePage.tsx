import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Table } from "../../ui";
import { listAccounts, type Account } from "../accounts/accountsApi";
import { DueRow } from "./DueRow";
import { listDue, type DueItem } from "./dueApi";
import "./due.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Due items (UC-ZF-004): planned items up to 14 days ahead, overdue ones first. Each is confirmed,
 * postponed or skipped from its row; nothing is booked by itself.
 */
export function DuePage() {
  const { t } = useLingui();
  const [items, setItems] = useState<DueItem[] | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const table = useRef<HTMLTableSectionElement>(null);
  // Index of the row to focus after a reload, so Enter can go on with the next item.
  const focusAfter = useRef<number | null>(null);

  const reload = useCallback(async () => setItems(await listDue()), []);

  useEffect(() => {
    let cancelled = false;
    const fail = (reason: unknown) => !cancelled && setError(message(reason));
    listAccounts(true)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch(fail);
    listDue()
      .then((loaded) => !cancelled && setItems(loaded))
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (focusAfter.current === null || items === null) return;
    const rows =
      table.current?.querySelectorAll<HTMLElement>("tr[data-due-row]");
    rows?.[Math.min(focusAfter.current, rows.length - 1)]?.focus();
    focusAfter.current = null;
  }, [items]);

  async function changed(
    what: "booked" | "postponed" | "skipped",
    item: DueItem,
  ) {
    const position = items?.findIndex((other) => other.id === item.id) ?? 0;
    const name = item.text === "" ? "—" : item.text;
    setNotice(
      what === "booked"
        ? t`Booked “${name}”.`
        : what === "skipped"
          ? t`Skipped “${name}”.`
          : t`Postponed “${name}”.`,
    );
    focusAfter.current = position;
    try {
      await reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  const lateCount = items?.filter((item) => item.long_overdue).length ?? 0;

  return (
    <section className="due">
      <h1>
        <Trans>Due items</Trans>
      </h1>
      <p className="due-intro ui-hint">
        <Trans>
          Planned items up to 14 days ahead, overdue ones first. Press Enter to
          confirm an item with the amount and date shown, or change them first.
          Nothing is booked by itself.
        </Trans>
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {lateCount > 0 && (
        <p role="alert" className="due-alert">
          <span aria-hidden="true">⚠ </span>
          <Plural
            value={lateCount}
            one="# item is more than 30 days overdue: confirm, postpone or skip it."
            other="# items are more than 30 days overdue: confirm, postpone or skip them."
          />
        </p>
      )}
      {notice && (
        <p role="status" className="ui-notice due-notice">
          {notice}
        </p>
      )}
      {items === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : items.length === 0 ? (
        <p className="ui-hint">
          <Trans>
            Nothing is due. Recurring and expected items are entered under{" "}
            <Link to="/recurring">Recurring items</Link>.
          </Trans>
        </p>
      ) : (
        <Table className="due-table">
          <thead>
            <tr>
              <th>
                <Trans>Planned for</Trans>
              </th>
              <th>
                <Trans>Item</Trans>
              </th>
              <th className="num">
                <Trans>Amount</Trans>
              </th>
              <th>
                <Trans>Booking date</Trans>
              </th>
              <th>
                <span className="sr-only">
                  <Trans>Actions</Trans>
                </span>
              </th>
            </tr>
          </thead>
          <tbody ref={table}>
            {items.map((item) => (
              <DueRow
                key={item.id}
                item={item}
                accounts={accounts}
                onChanged={changed}
              />
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}

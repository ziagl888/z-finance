import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useState } from "react";

import { formatCents } from "../../api/client";
import { Badge, Field, KpiTile } from "../../ui";
import { listCategories, type Category } from "../accounts/accountsApi";
import { AREA_LABELS } from "../accounts/labels";
import { updateTransaction } from "../entry/entryApi";
import { Drilldown } from "./Drilldown";
import {
  applyRules,
  currentMonth,
  deleteRule,
  draftRule,
  exportUrl,
  getMonthBookings,
  getMonthReport,
  listRules,
  REPORT_AREAS,
  shiftMonth,
  type Comparison,
  type Line,
  type MonthBooking,
  type MonthReport,
  type ReportArea,
  type Rule,
  type RuleInput,
} from "./monthApi";
import { ReportTable } from "./ReportTable";
import { RuleDialog } from "./RuleDialog";
import { RulesSection } from "./RulesSection";
import { UncategorizedList } from "./UncategorizedList";
import "./month.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** One of the three figures on top: the amount with the two comparison values. */
function Figure({ label, value }: { label: string; value: Comparison }) {
  const { i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const previous = money(value.previous_cents);
  const average = money(value.average_6m_cents);
  return (
    <KpiTile
      label={label}
      value={<strong>{money(value.amount_cents)}</strong>}
      sub={
        <Trans>
          Previous month {previous} · Average of last 6 months {average}
        </Trans>
      }
    />
  );
}

/**
 * Month (UC-ZF-007): income, expenses and balance of a month for one area or both, by category
 * and compared with the previous month and the six months before; the bookings behind a
 * category; the uncategorised bookings with "make a rule"; the rules; the CSV export.
 */
export function MonthPage() {
  const { t, i18n } = useLingui();
  const [month, setMonth] = useState(currentMonth);
  const [area, setArea] = useState<ReportArea>("all");
  const [report, setReport] = useState<MonthReport | null>(null);
  const [uncategorized, setUncategorized] = useState<MonthBooking[] | null>(
    null,
  );
  const [categories, setCategories] = useState<Category[]>([]);
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [selected, setSelected] = useState<Line | null>(null);
  const [drilldown, setDrilldown] = useState<MonthBooking[] | null>(null);
  const [draft, setDraft] = useState<RuleInput | null>(null);
  const [lastApplied, setLastApplied] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped after every change so report, lists and rules are read again.
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    let cancelled = false;
    listCategories()
      .then((loaded) => !cancelled && setCategories(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getMonthReport(month, area), getMonthBookings(month, area)])
      .then(([loadedReport, loadedList]) => {
        if (cancelled) return;
        setReport(loadedReport);
        setUncategorized(loadedList);
        setError(null);
      })
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    listRules()
      .then((loaded) => !cancelled && setRules(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [month, area, reloadKey]);

  useEffect(() => {
    if (selected === null) return;
    let cancelled = false;
    getMonthBookings(
      month,
      area,
      selected.category_id ?? undefined,
      selected.flow,
    )
      .then((loaded) => !cancelled && setDrilldown(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [selected, month, area, reloadKey]);

  function choose(nextMonth: string, nextArea: ReportArea) {
    if (nextMonth === month && nextArea === area) return;
    // The old figures must not stay under the new heading: show "Loading…" until the
    // answer for the new month arrives (the effect drops answers of an older choice).
    setReport(null);
    setUncategorized(null);
    setError(null);
    setMonth(nextMonth);
    setArea(nextArea);
    setSelected(null);
    setDrilldown(null);
  }

  function select(line: Line) {
    setDrilldown(null);
    setSelected(line);
  }

  async function assign(item: MonthBooking, categoryId: number) {
    setError(null);
    try {
      await updateTransaction(item.id, {
        date: item.date,
        amount_cents: item.amount_cents,
        account_id: item.account_id,
        category_id: categoryId,
        text: item.text,
        counterparty: item.counterparty,
        memo: item.memo,
      });
    } catch (reason) {
      setError(message(reason));
      return;
    }
    // A booking without text has nothing to make a rule from: no offer, no error.
    setDraft(await draftRule(item.id).catch(() => null));
    reload();
  }

  async function removeRule(rule: Rule) {
    if (
      !window.confirm(
        t`Delete this rule? Bookings it categorised keep their category.`,
      )
    )
      return;
    setError(null);
    try {
      await deleteRule(rule.id);
      reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function runRules() {
    setError(null);
    try {
      setLastApplied((await applyRules()).categorised);
      reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  const lines = (flow: Line["flow"]) =>
    (report?.lines ?? []).filter((line) => line.flow === flow);
  const selectedName = selected?.category_name ?? t`Uncategorised`;
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const areaLabel = (value: ReportArea) =>
    value === "all" ? t`Total` : i18n._(AREA_LABELS[value]);
  const movementIn = money(report?.movement_in_cents ?? 0);
  const movementOut = money(report?.movement_out_cents ?? 0);
  const moving =
    report !== null &&
    (report.movement_in_cents !== 0 || report.movement_out_cents !== 0);
  const monthDate = new Date(`${month}-01T00:00:00`);
  const monthTitle = monthDate.toLocaleDateString(i18n.locale, {
    month: "long",
    year: "numeric",
  });

  return (
    <section className="month">
      <h1>
        <Trans>Month</Trans>
      </h1>
      <div className="month-toolbar">
        <div className="ui-field">
          <label htmlFor="month-picker">
            <Trans>Month</Trans>
          </label>
          <div className="month-picker">
            <button
              type="button"
              aria-label={t`Previous month`}
              onClick={() => choose(shiftMonth(month, -1), area)}
            >
              ‹
            </button>
            <input
              id="month-picker"
              type="month"
              value={month}
              onChange={(event) =>
                event.target.value && choose(event.target.value, area)
              }
            />
            <button
              type="button"
              aria-label={t`Next month`}
              onClick={() => choose(shiftMonth(month, 1), area)}
            >
              ›
            </button>
          </div>
        </div>
        <Field htmlFor="month-area" label={<Trans>Area</Trans>}>
          <select
            id="month-area"
            value={area}
            onChange={(event) =>
              choose(month, event.target.value as ReportArea)
            }
          >
            {REPORT_AREAS.map((value) => (
              <option key={value} value={value}>
                {areaLabel(value)}
              </option>
            ))}
          </select>
        </Field>
        <a className="month-export" href={exportUrl(month, area)} download>
          <Trans>Export as CSV</Trans>
        </a>
      </div>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {report === null ? (
        !error && (
          <p>
            <Trans>Loading…</Trans>
          </p>
        )
      ) : (
        <>
          <h2 className="month-title">{monthTitle}</h2>
          <div className="ui-kpis">
            <Figure label={t`Income this month`} value={report.income} />
            <Figure label={t`Expenses this month`} value={report.expense} />
            <Figure label={t`Balance`} value={report.balance} />
          </div>
          {moving && (
            <p className="ui-hint">
              <Trans>
                Transfers to and from the other area (owner draw or
                contribution) are not counted as income or expenses: in{" "}
                {movementIn}, out {movementOut}.
              </Trans>
            </p>
          )}
          <div className="month-tables">
            <ReportTable
              flow="income"
              lines={lines("income")}
              total={report.income}
              selected={selected}
              onSelect={select}
            />
            <ReportTable
              flow="expense"
              lines={lines("expense")}
              total={report.expense}
              selected={selected}
              onSelect={select}
            />
          </div>
          {selected && (
            <Drilldown
              title={
                selected.flow === "income"
                  ? t`Income: ${selectedName}`
                  : t`Expenses: ${selectedName}`
              }
              bookings={drilldown}
              onClose={() => {
                setSelected(null);
                setDrilldown(null);
              }}
            />
          )}
        </>
      )}

      <h2>
        <Trans>Uncategorised bookings</Trans>
        {report !== null && report.uncategorized_count > 0 && (
          <Badge>{report.uncategorized_count}</Badge>
        )}
      </h2>
      <UncategorizedList
        bookings={uncategorized}
        categories={categories}
        onAssign={(item, id) => void assign(item, id)}
      />
      {draft && (
        <RuleDialog
          key={`${draft.pattern}-${draft.category_id}`}
          draft={draft}
          categories={categories}
          onCreated={reload}
          onClose={() => setDraft(null)}
        />
      )}

      <h2>
        <Trans>Rules</Trans>
      </h2>
      <RulesSection
        rules={rules}
        categories={categories}
        onDelete={(rule) => void removeRule(rule)}
        onApply={() => void runRules()}
        lastApplied={lastApplied}
      />
    </section>
  );
}

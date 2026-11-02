import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { listAccounts, type Account } from "../accounts/accountsApi";
import { ApiError } from "../../api/client";
import { ChartsSection } from "./ChartsSection";
import { FreeAmountCard } from "./FreeAmountCard";
import { RequiredCard } from "./RequiredCard";
import { ReserveSection } from "./ReserveSection";
import { SettingsForm } from "./SettingsForm";
import {
  extendSlots,
  formatRatePercent,
  isRateJump,
  type SlotMap,
} from "./draft";
import {
  archiveBackpayment,
  getExpenseChart,
  getFreeAmount,
  getIncomeChart,
  getRequiredRevenue,
  getStatus,
  listPots,
  putSettings,
  type ChartArea,
  type ExpenseMonth,
  type FreeAmount,
  type IncomeMonth,
  type LevySettingsInput,
  type LevyStatus,
  type PotOption,
  type RequiredRevenue,
} from "./levyApi";
import "./levy.css";

/** Everything the page shows, fetched together so the figures always belong to one state. */
interface Snapshot {
  status: LevyStatus;
  free: FreeAmount;
  required: RequiredRevenue;
  income: IncomeMonth[];
  expenses: ExpenseMonth[];
}

/** Reads everything the page shows. The status comes first: it syncs the back-payment items. */
async function fetchSnapshot(area: ChartArea): Promise<Snapshot> {
  const status = await getStatus();
  const [free, required, income, expenses] = await Promise.all([
    getFreeAmount(),
    getRequiredRevenue(),
    getIncomeChart(),
    getExpenseChart(area),
  ]);
  return { status, free, required, income, expenses };
}

function message(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : String(error);
}

/**
 * Levy & revenue (UC-ZF-015): the levy reserve of the year (target, set aside, gap), the expected
 * back-payment, the free amount net of the gap, the required revenue per month and the income and
 * expense charts. Saving the settings recalculates all of it; the previous figures stay on screen,
 * dimmed, while the new ones load.
 */
export function LevyPage() {
  const { t, i18n } = useLingui();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [pots, setPots] = useState<PotOption[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [area, setArea] = useState<ChartArea>("all");
  const [slots, setSlots] = useState<SlotMap>(() => new Map());
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jump, setJump] = useState<{ from: number; to: number } | null>(null);
  // A response of an older request must never overwrite a newer one (fast area switches).
  const latest = useRef(0);

  /** Puts a fetched snapshot on screen (shared by the first load and every reload). */
  const show = useCallback((next: Snapshot) => {
    setSnapshot(next);
    setSlots((current) => extendSlots(current, next.expenses));
    setError(null);
  }, []);

  /**
   * Reload after the user changed something: the old figures stay, dimmed, until the new ones
   * arrive. `true` when the figures on screen are the new ones.
   */
  const refresh = useCallback(
    async (chartArea: ChartArea): Promise<boolean> => {
      const ticket = ++latest.current;
      setBusy(true);
      try {
        const next = await fetchSnapshot(chartArea);
        if (ticket !== latest.current) return false;
        show(next);
        return true;
      } catch (reason) {
        if (ticket === latest.current) setError(message(reason));
        return false;
      } finally {
        if (ticket === latest.current) setBusy(false);
      }
    },
    [show],
  );

  useEffect(() => {
    let cancelled = false;
    const ticket = ++latest.current;
    const current = () => !cancelled && ticket === latest.current;
    fetchSnapshot("all")
      .then((first) => current() && show(first))
      .catch((reason: unknown) => current() && setError(message(reason)))
      .finally(() => current() && setBusy(false));
    // Pots and accounts only fill the settings form; the page works without them.
    listPots()
      .then((loaded) => !cancelled && setPots(loaded))
      .catch(() => undefined);
    listAccounts(false)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch(() => undefined);
    return () => {
      cancelled = true;
      latest.current += 1;
    };
  }, [show]);

  async function onSave(input: LevySettingsInput) {
    const previous = snapshot?.status.settings;
    setNotice(null);
    await putSettings(input);
    setJump(
      previous &&
        !previous.settings_missing &&
        isRateJump(previous.levy_rate_bp, input.levy_rate_bp)
        ? { from: previous.levy_rate_bp, to: input.levy_rate_bp }
        : null,
    );
    if (await refresh(area)) {
      setNotice(t`Saved. All figures and charts are recalculated.`);
    }
  }

  async function onArchive(year: number) {
    if (
      !window.confirm(
        t`Keep the estimate for ${year} and remove its planned back-payment? Enter the real amount of the assessment as an expected item afterwards.`,
      )
    ) {
      return;
    }
    setNotice(null);
    try {
      await archiveBackpayment(year);
      if (await refresh(area)) {
        setNotice(
          t`The estimate for ${year} is archived. Enter the real assessment as an expected item under Recurring items.`,
        );
      }
    } catch (reason) {
      setError(message(reason));
    }
  }

  function onArea(next: ChartArea) {
    setArea(next);
    void refresh(next);
  }

  const settingsKey = snapshot
    ? JSON.stringify(snapshot.status.settings)
    : "loading";
  const locale = i18n.locale;
  const jumpFrom = jump ? formatRatePercent(jump.from, locale) : "";
  const jumpTo = jump ? formatRatePercent(jump.to, locale) : "";

  return (
    <section className="levy">
      <h1>
        <Trans>Levy &amp; revenue</Trans>
      </h1>
      <p className="levy-intro muted">
        <Trans>
          Every income you receive triggers income tax and SVS that are only
          collected next summer. See what your income has already triggered, how
          much of it you have set aside, and how much revenue you need each
          month. Your fixed tax prepayments are set up under{" "}
          <Link to="/tax">Taxes &amp; levies</Link>.
        </Trans>
      </p>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="ui-notice">
          {notice}
        </p>
      )}
      {jump && (
        <p role="status" className="ui-hint levy-jump">
          <Trans>
            The rate changed from {jumpFrom} % to {jumpTo} %, more than 10
            percentage points. Your reserve so far was built with the old rate,
            so the gap shows the difference. Consider adjusting your allocation
            rule so new income is set aside at the new rate.
          </Trans>
        </p>
      )}

      {snapshot === null ? (
        !error && (
          <p role="status" className="muted">
            <Trans>Loading…</Trans>
          </p>
        )
      ) : (
        <>
          <ReserveSection status={snapshot.status} onArchive={onArchive} />
          <div className="levy-split">
            <div className="levy-stack">
              <FreeAmountCard free={snapshot.free} />
              <RequiredCard required={snapshot.required} />
            </div>
            <SettingsForm
              key={settingsKey}
              settings={snapshot.status.settings}
              pots={pots}
              accounts={accounts}
              busy={busy}
              onSave={onSave}
            />
          </div>
          <ChartsSection
            income={snapshot.income}
            expenses={snapshot.expenses}
            area={area}
            onArea={onArea}
            slots={slots}
            busy={busy}
          />
        </>
      )}
    </section>
  );
}

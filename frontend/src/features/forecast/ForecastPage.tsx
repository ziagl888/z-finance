import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { formatCents } from "../../api/client";
import { listAccounts, type Account } from "../accounts/accountsApi";
import { formatDay } from "../levy/draft";
import { createExpected } from "../recurring/recurringApi";
import { DayItems } from "./DayItems";
import { EmergencyPanel } from "./EmergencyPanel";
import { FiguresPanel } from "./FiguresPanel";
import { ForecastChart } from "./ForecastChart";
import { WhatIfForm } from "./WhatIfForm";
import {
  isHorizon,
  paymentAccounts,
  TOTAL_VIEW,
  viewOf,
  whatIfRows,
  type ViewValue,
} from "./draft";
import {
  getEmergency,
  getForecast,
  getItems,
  HORIZONS,
  postWhatIf,
  takeEmergency,
  type Emergency,
  type EmergencyProposal,
  type Forecast,
  type ForecastItem,
  type Horizon,
  type WhatIfItem,
} from "./forecastApi";
import "./forecast.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The curve and what belongs to it, fetched together so the figures always match. */
interface Snapshot {
  forecast: Forecast;
  emergency: Emergency | null;
}

/**
 * Forecast (UC-ZF-006): the liquidity curve of the next 3, 6 or 12 months for all accounts, an
 * area or one account, with the reserve, the lowest point, the items of a chosen day, what-if
 * items that are stored nowhere and the pots that would cover a shortfall. The what-if list lives
 * in this component's state only, so a reload always shows the saved plan (AK3).
 */
export function ForecastPage() {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [viewValue, setViewValue] = useState<ViewValue>(TOTAL_VIEW);
  const [horizon, setHorizon] = useState<Horizon>(12);
  const [whatIf, setWhatIf] = useState<WhatIfItem[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [chosenDay, setChosenDay] = useState<string | null>(null);
  const [loadedRows, setLoadedRows] = useState<{
    day: string;
    rows: ForecastItem[];
  } | null>(null);
  // The key of the request whose answer (or failure) is on screen; a newer request means busy.
  const [settled, setSettled] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Bumped after a change the curve cannot see by itself (a pot withdrawal, an expected item).
  const [reloadKey, setReloadKey] = useState(0);
  const view = useMemo(() => viewOf(viewValue), [viewValue]);
  const requestKey = useMemo(
    () => JSON.stringify([viewValue, horizon, whatIf, reloadKey]),
    [viewValue, horizon, whatIf, reloadKey],
  );
  const busy = settled !== requestKey;
  const payment = useMemo(() => paymentAccounts(accounts), [accounts]);

  useEffect(() => {
    let cancelled = false;
    listAccounts(false)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  // The curve first (it syncs the levy back-payment items), then the emergency proposal.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const forecast =
        whatIf.length > 0
          ? await postWhatIf(view, horizon, whatIf)
          : await getForecast(view, horizon);
      const emergency = await getEmergency(view, horizon);
      if (cancelled) return;
      setSnapshot({ forecast, emergency });
      setError(null);
    })()
      .catch((reason: unknown) => !cancelled && setError(message(reason)))
      .finally(() => !cancelled && setSettled(requestKey));
    return () => {
      cancelled = true;
    };
  }, [view, horizon, whatIf, requestKey]);

  // A chosen day only counts while it is a day of the curve on screen (a shorter horizon drops it).
  const days = snapshot?.forecast.days;
  const dayIndex = useMemo(
    () =>
      chosenDay === null || !days
        ? -1
        : days.findIndex((day) => day.date === chosenDay),
    [days, chosenDay],
  );
  const day = dayIndex === -1 || !days ? null : (days[dayIndex] ?? null);
  const dayKey = day?.date ?? null;

  useEffect(() => {
    if (dayKey === null) return;
    let cancelled = false;
    getItems(dayKey, view)
      .then((rows) => !cancelled && setLoadedRows({ day: dayKey, rows }))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [dayKey, view, snapshot]);

  const dayRows = useMemo(() => {
    if (dayKey === null || loadedRows?.day !== dayKey) return null;
    return [...loadedRows.rows, ...whatIfRows(whatIf, dayKey, accounts, view)];
  }, [dayKey, loadedRows, whatIf, accounts, view]);

  const onSelect = useCallback(
    (index: number) => {
      const chosen = days?.[index];
      if (chosen) setChosenDay(chosen.date);
    },
    [days],
  );

  function onAdd(item: WhatIfItem) {
    setNotice(null);
    setWhatIf((items) => [...items, item]);
  }

  async function onAdopt(index: number) {
    const item = whatIf[index];
    if (!item) return;
    setNotice(null);
    try {
      await createExpected({
        date: item.date,
        amount_cents: item.amount_cents,
        account_id: item.account_id,
        category_id: null,
        text: item.text,
        counterparty: null,
      });
      setWhatIf((items) => items.filter((_, i) => i !== index));
      setReloadKey((n) => n + 1);
      setNotice(
        t`Added as an expected item. It is now part of your saved plan.`,
      );
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function onTake(proposal: EmergencyProposal) {
    setNotice(null);
    try {
      await takeEmergency(proposal);
      setReloadKey((n) => n + 1);
      setNotice(t`Taken from the pots. The forecast is recalculated.`);
    } catch (reason) {
      setError(message(reason));
    }
  }

  const forecast = snapshot?.forecast ?? null;
  const breach = forecast?.first_breach_date ?? null;
  const breachDate = breach === null ? "" : formatDay(breach, locale);
  const shortfall = forecast
    ? formatCents(forecast.shortfall_cents, locale)
    : "";

  return (
    <section className="forecast">
      <h1>
        <Trans>Forecast</Trans>
      </h1>
      <p className="forecast-intro muted">
        <Trans>
          See where your money is heading: the balance of the coming months day
          by day, when it is lowest, and whether it falls below your reserve
          before it happens.
        </Trans>
      </p>

      <div className="forecast-controls">
        <div className="forecast-view">
          <label htmlFor="forecast-view">
            <Trans>View</Trans>
          </label>
          <select
            id="forecast-view"
            value={viewValue}
            onChange={(event) => setViewValue(event.target.value)}
          >
            <option value={TOTAL_VIEW}>{t`All accounts`}</option>
            <option value="area:private">{t`Private accounts`}</option>
            <option value="area:business">{t`Business accounts`}</option>
            {payment.length > 0 && (
              <optgroup label={t`One account`}>
                {payment.map((account) => (
                  <option key={account.id} value={`account:${account.id}`}>
                    {account.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
        <fieldset className="ui-segmented">
          <legend className="sr-only">
            <Trans>Horizon</Trans>
          </legend>
          {HORIZONS.map((months) => (
            <label key={months}>
              <input
                type="radio"
                name="forecast-horizon"
                value={months}
                checked={horizon === months}
                onChange={(event) => {
                  const chosen = Number(event.target.value);
                  if (isHorizon(chosen)) setHorizon(chosen);
                }}
              />
              {t`${months} months`}
            </label>
          ))}
        </fieldset>
      </div>

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

      {forecast === null ? (
        !error && (
          <p role="status" className="muted">
            <Trans>Loading…</Trans>
          </p>
        )
      ) : (
        <>
          {breach !== null && (
            <p role="alert" className="forecast-alert">
              <Trans>
                Below your reserve from {breachDate}: the lowest point is{" "}
                {shortfall} short of it.
              </Trans>
            </p>
          )}
          {forecast.reserve_missing && (
            <p role="status" className="ui-hint">
              <Trans>
                No reserve defined, so there is no alarm. Set a reserve floor
                per account, or a total reserve, under{" "}
                <Link to="/accounts">Accounts</Link>.
              </Trans>
            </p>
          )}
          <FiguresPanel forecast={forecast} />
          <div className="forecast-layout">
            <div className="forecast-main">
              <ForecastChart
                forecast={forecast}
                selected={dayIndex === -1 ? null : dayIndex}
                onSelect={onSelect}
                busy={busy}
              />
              <DayItems day={day} rows={dayRows} accounts={accounts} />
            </div>
            <div className="forecast-side">
              {snapshot?.emergency && (
                <EmergencyPanel
                  emergency={snapshot.emergency}
                  accounts={accounts}
                  whatIfActive={whatIf.length > 0}
                  busy={busy}
                  onTake={onTake}
                />
              )}
              <WhatIfForm
                accounts={payment}
                view={view}
                start={forecast.start}
                items={whatIf}
                busy={busy}
                onAdd={onAdd}
                onRemove={(index) =>
                  setWhatIf((items) => items.filter((_, i) => i !== index))
                }
                onClear={() => setWhatIf([])}
                onAdopt={onAdopt}
              />
            </div>
          </div>
        </>
      )}
    </section>
  );
}

import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { Badge, Button, Card, KpiTile, MoneyCell, Table } from "../../ui";
import { formatDay, formatRatePercent } from "./draft";
import type { BackpaymentState, LevyStatus, LevyYear } from "./levyApi";

interface Props {
  status: LevyStatus;
  /** Keeps the estimate of `year` and removes its planned back-payment (UC 3a). */
  onArchive: (year: number) => void;
}

/**
 * Reserve target, set-aside amount and gap of the current year (UC steps 2–3), the warnings of
 * the extensions 2a/2b and one row per open year with its back-payment.
 */
export function ReserveSection({ status, onArchive }: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const { settings } = status;
  const current = status.years[status.years.length - 1];
  const year = current?.year ?? 0;
  const potShare = formatCents(current?.pot_share_cents ?? 0, locale);
  const prepayments = formatCents(current?.prepayments_cents ?? 0, locale);
  const rate = formatRatePercent(settings.levy_rate_bp, locale);
  const gapFree = formatCents(status.free_balance_cents, locale);
  const gapAmount = formatCents(status.levy_gap_cents, locale);

  return (
    <section className="levy-reserve" aria-labelledby="levy-reserve-title">
      <h2 id="levy-reserve-title">
        {current ? (
          <Trans>Levy reserve {year}</Trans>
        ) : (
          <Trans>Levy reserve</Trans>
        )}
      </h2>

      {settings.settings_missing && (
        <p className="ui-hint">
          <Trans>
            Nothing is reserved yet: enter your levy rate in the settings and
            save.
          </Trans>
        </p>
      )}
      {status.levy_uncovered && (
        <p role="alert" className="levy-alert">
          <Badge tone="overdue">
            <Trans>Levies not covered</Trans>
          </Badge>{" "}
          <Trans>
            The open gap of {gapAmount} is larger than the free balance of your
            payment accounts ({gapFree}). The tax office and SVS will claim
            money you have not set aside.
          </Trans>
        </p>
      )}
      {status.levy_pot_missing && !settings.settings_missing && (
        <p className="ui-hint" role="status">
          <Trans>
            No levy pot is set: only prepayments count as set aside. Choose a
            savings pot in the settings, or flag one as levy pot when you create
            it.
          </Trans>
        </p>
      )}
      {status.levy_pot_ambiguous && (
        <p className="ui-hint" role="status">
          <Trans>
            Several pots are flagged as levy pot; the one created first is used.
            Choose one in the settings.
          </Trans>
        </p>
      )}
      {status.backpayment_account_missing && (
        <p className="ui-hint" role="status">
          <Trans>
            Choose a back-payment account in the settings so the expected
            back-payment appears in your forecast.
          </Trans>
        </p>
      )}

      {current && (
        <div className="ui-kpis">
          <KpiTile
            label={
              settings.levy_base === "profit" ? (
                <Trans>Business profit so far</Trans>
              ) : (
                <Trans>Business income so far</Trans>
              )
            }
            value={formatCents(current.base_cents, locale)}
            sub={t`January to today, ${year}`}
          />
          <KpiTile
            label={<Trans>Target</Trans>}
            value={formatCents(current.target_cents, locale)}
            sub={
              settings.levy_base === "profit"
                ? t`${rate} % of the business profit`
                : t`${rate} % of the business income`
            }
          />
          <KpiTile
            label={<Trans>Set aside</Trans>}
            value={formatCents(current.actual_cents, locale)}
            sub={t`Levy pot ${potShare} + prepayments ${prepayments}`}
          />
          <KpiTile
            label={<Trans>Gap</Trans>}
            value={formatCents(current.gap_cents, locale)}
            tone={current.gap_cents > 0 ? "alert" : "ok"}
            sub={
              current.gap_cents > 0
                ? t`Target minus set aside`
                : t`Fully covered`
            }
          />
        </div>
      )}

      {status.years.length > 0 && (
        <Card as="div" dense>
          {/* the table scrolls inside the card on a narrow window instead of overflowing it */}
          <div
            className="levy-scroll"
            role="region"
            tabIndex={0}
            aria-label={t`Open years and their back-payment`}
          >
            <Table>
              <thead>
                <tr>
                  <th scope="col">
                    <Trans>Year</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Target</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Prepayments</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Set aside</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Gap</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Expected back-payment</Trans>
                  </th>
                  <th scope="col">
                    <Trans>Due</Trans>
                  </th>
                  <th scope="col">
                    <span className="sr-only">
                      <Trans>Actions</Trans>
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {status.years.map((year) => (
                  <YearRow
                    key={year.year}
                    year={year}
                    locale={locale}
                    onArchive={onArchive}
                  />
                ))}
              </tbody>
            </Table>
          </div>
        </Card>
      )}
    </section>
  );
}

function YearRow({
  year,
  locale,
  onArchive,
}: {
  year: LevyYear;
  locale: string;
  onArchive: (year: number) => void;
}) {
  const { t } = useLingui();
  const states: Record<
    BackpaymentState,
    { tone: "planned" | "ok" | "neutral"; label: string }
  > = {
    open: { tone: "planned", label: t`Planned` },
    settled: { tone: "ok", label: t`Paid` },
    archived: { tone: "neutral", label: t`Archived` },
  };
  const state = states[year.backpayment_state];
  // Only a planned item can be archived; without a booking there is nothing to replace yet.
  const canArchive =
    year.backpayment_state === "open" &&
    year.backpayment_transaction_id !== null;
  return (
    <tr>
      <th scope="row">{year.year}</th>
      <MoneyCell cents={year.target_cents} />
      <MoneyCell cents={year.prepayments_cents} />
      <MoneyCell cents={year.actual_cents} />
      <MoneyCell cents={year.gap_cents} plain />
      <MoneyCell cents={year.backpayment_cents} plain>
        <Badge tone={state.tone}>{state.label}</Badge>
      </MoneyCell>
      <td>{formatDay(year.backpayment_date, locale)}</td>
      <td className="levy-row-actions">
        {canArchive && (
          <Button onClick={() => onArchive(year.year)}>
            <Trans>Assessment received</Trans>
          </Button>
        )}
      </td>
    </tr>
  );
}

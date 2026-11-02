import { Trans, useLingui } from "@lingui/react/macro";

interface Props {
  id: string;
  label: string;
  /** "MM-DD" values; day 31 = last day of the month. */
  dates: string[];
  invalid: boolean;
  onChange: (dates: string[]) => void;
}

const MONTHS = Array.from({ length: 12 }, (_, index) => index + 1);
const DAYS = Array.from({ length: 31 }, (_, index) => index + 1);
const pad = (value: number) => String(value).padStart(2, "0");

/** A due date as a readable label: "15 February", or "End of February" for the last day. */
export function useDueLabel() {
  const { t, i18n } = useLingui();
  return (due: string) => {
    const [m, d] = [Number(due.slice(0, 2)), Number(due.slice(3))];
    if (d >= new Date(2027, m, 0).getDate()) {
      const name = new Intl.DateTimeFormat(i18n.locale, {
        month: "long",
      }).format(new Date(2027, m - 1, 1));
      return t`End of ${name}`;
    }
    return new Intl.DateTimeFormat(i18n.locale, {
      day: "numeric",
      month: "long",
    }).format(new Date(2027, m - 1, d));
  };
}

/**
 * The due dates of one kind: a month and a day per row (31 = end of the month), add and remove.
 * The server keeps them ascending, so the order typed here does not matter.
 */
export function DueDatesField({ id, label, dates, invalid, onChange }: Props) {
  const { t, i18n } = useLingui();
  const monthName = (number: number) =>
    new Intl.DateTimeFormat(i18n.locale, { month: "long" }).format(
      new Date(2027, number - 1, 1),
    );
  const change = (index: number, month: number, day: number) =>
    onChange(
      dates.map((d, i) => (i === index ? `${pad(month)}-${pad(day)}` : d)),
    );

  return (
    <fieldset className="tax-dates" aria-invalid={invalid || undefined}>
      <legend>{label}</legend>
      {dates.map((due, index) => (
        <div className="tax-date-row" key={index}>
          <select
            id={`${id}-month-${index}`}
            aria-label={t`Month`}
            value={Number(due.slice(0, 2))}
            onChange={(e) =>
              change(index, Number(e.target.value), Number(due.slice(3)))
            }
          >
            {MONTHS.map((m) => (
              <option key={m} value={m}>
                {monthName(m)}
              </option>
            ))}
          </select>
          <select
            id={`${id}-day-${index}`}
            aria-label={t`Day`}
            value={Number(due.slice(3))}
            onChange={(e) =>
              change(index, Number(due.slice(0, 2)), Number(e.target.value))
            }
          >
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d === 31 ? t`End of month` : d}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => onChange(dates.filter((_, i) => i !== index))}
            disabled={dates.length <= 1}
          >
            <Trans>Remove</Trans>
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...dates, "01-15"])}
        disabled={dates.length >= 12}
      >
        <Trans>Add due date</Trans>
      </button>
    </fieldset>
  );
}

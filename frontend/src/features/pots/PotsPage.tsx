import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { formatCents } from "../../api/client";
import { Button, Card, KpiTile } from "../../ui";
import { listAccounts, type Account } from "../accounts/accountsApi";
import { formatDate } from "../recurring/draft";
import { orderedPots } from "./draft";
import { EmergencyDialog } from "./EmergencyDialog";
import { OrderList } from "./OrderList";
import { PotCard, type PotAction } from "./PotCard";
import {
  ExpenseDialog,
  MoneyDialog,
  MoveDialog,
  SavingsPlanDialog,
} from "./PotDialogs";
import { PotForm } from "./PotForm";
import {
  closePot,
  deletePot,
  listPots,
  putRanks,
  type Overview,
  type Pot,
} from "./potsApi";
import "./pots.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

type DialogState =
  | { kind: "new" }
  | { kind: "emergency"; accountId: number }
  | {
      kind: "edit" | "deposit" | "withdraw" | "move" | "expense" | "plan";
      potId: number;
    };

/**
 * Savings pots (UC-ZF-011): per account its balance, the pots on it with their progress, the free
 * balance and what an emergency could take, the emergency order (dragged or moved with buttons),
 * and the dialogs to create, fill, empty, move and close pots. All figures come from /api/pots and
 * are reloaded after every change; the screen keeps no arithmetic of its own.
 */
export function PotsPage() {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const reload = useCallback(async () => {
    setOverview(await listPots());
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fail = (reason: unknown) => !cancelled && setError(message(reason));
    listAccounts(true)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch(fail);
    listPots()
      .then((loaded) => !cancelled && setOverview(loaded))
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, []);

  // Bring a freshly opened dialog into view (the cards can be far below the toolbar).
  useEffect(() => {
    if (dialog) dialogRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [dialog]);

  async function run(change: () => Promise<unknown>, done?: string) {
    setError(null);
    setNotice(null);
    try {
      await change();
      await reload();
      if (done) setNotice(done);
    } catch (reason) {
      setError(message(reason));
    }
  }

  /** Called by a dialog after its change went through: close it, show the line, reload. */
  async function finish(text: string) {
    setDialog(null);
    setError(null);
    setNotice(text);
    await reload();
  }

  const all = accounts ?? [];
  // A pot lives on a real money account, never on a liability (the backend refuses it too).
  const usable = all.filter(
    (a) => a.archived_at === null && a.type !== "liability",
  );
  const nameOf = (id: number) => all.find((a) => a.id === id)?.name ?? `#${id}`;
  const pots = overview?.pots ?? [];
  const open = pots.filter((p) => !p.closed);
  const potOf = (id: number) => pots.find((p) => p.id === id);
  const noAccounts = accounts !== null && usable.length === 0;
  const withEmergency = overview?.accounts.filter(
    (split) => orderedPots(pots, split.account_id).length > 0,
  );

  function onAction(action: PotAction, pot: Pot) {
    setNotice(null);
    setError(null);
    const name = pot.name;
    if (action === "close") {
      if (
        window.confirm(
          t`Close the pot ${name}? Its rest returns to the free balance.`,
        )
      ) {
        void run(() => closePot(pot.id), t`Pot ${name} closed.`);
      }
    } else if (action === "delete") {
      if (
        window.confirm(
          t`Delete the pot ${name} with its history? Its money returns to the free balance.`,
        )
      ) {
        if (dialog && "potId" in dialog && dialog.potId === pot.id)
          setDialog(null);
        void run(() => deletePot(pot.id), t`Pot ${name} deleted.`);
      }
    } else {
      setDialog({ kind: action, potId: pot.id });
    }
  }

  function onReorder(accountId: number, potIds: number[]) {
    void run(() => putRanks(accountId, potIds), t`Emergency order updated.`);
  }

  const close = () => setDialog(null);
  const active = dialog && "potId" in dialog ? potOf(dialog.potId) : undefined;

  let dialogView = null;
  if (dialog?.kind === "new") {
    dialogView = (
      <PotForm
        key="new"
        accounts={usable}
        pots={open}
        editing={null}
        onSaved={async (saved, warning) => {
          const name = saved.name;
          await finish(t`Pot ${name} saved.`);
          if (warning) setError(warning);
        }}
        onClose={close}
      />
    );
  } else if (dialog?.kind === "emergency") {
    dialogView = (
      <EmergencyDialog
        key="emergency"
        accounts={all.filter((a) =>
          withEmergency?.some((s) => s.account_id === a.id),
        )}
        pots={open}
        initialAccountId={dialog.accountId}
        onDone={finish}
        onClose={close}
      />
    );
  } else if (dialog && active) {
    const key = `${dialog.kind}-${active.id}`;
    const others = open.filter(
      (p) => p.account_id === active.account_id && p.id !== active.id,
    );
    if (dialog.kind === "edit") {
      dialogView = (
        <PotForm
          key={key}
          accounts={usable}
          pots={open}
          editing={active}
          onSaved={async (saved, warning) => {
            const name = saved.name;
            await finish(t`Pot ${name} saved.`);
            if (warning) setError(warning);
          }}
          onClose={close}
        />
      );
    } else if (dialog.kind === "deposit" || dialog.kind === "withdraw") {
      dialogView = (
        <MoneyDialog
          key={key}
          kind={dialog.kind}
          pot={active}
          split={overview?.accounts.find(
            (s) => s.account_id === active.account_id,
          )}
          otherAccounts={usable.filter((a) => a.id !== active.account_id)}
          onDone={finish}
          onClose={close}
        />
      );
    } else if (dialog.kind === "move") {
      dialogView = (
        <MoveDialog
          key={key}
          pot={active}
          targets={others}
          onDone={finish}
          onClose={close}
        />
      );
    } else if (dialog.kind === "expense") {
      dialogView = (
        <ExpenseDialog key={key} pot={active} onDone={finish} onClose={close} />
      );
    } else {
      dialogView = (
        <SavingsPlanDialog
          key={key}
          pot={active}
          fromAccounts={usable.filter((a) => a.id !== active.account_id)}
          onDone={finish}
          onClose={close}
        />
      );
    }
  }

  return (
    <section className="pots">
      <h1>
        <Trans>Savings pots</Trans>
      </h1>
      <p className="pots-intro muted">
        <Trans>
          Set money aside for a purpose. A pot is a part of an account, not an
          account of its own: the balance of the account stays what it is.
        </Trans>
      </p>

      {noAccounts ? (
        <p className="ui-hint">
          <Trans>
            Create an account first: <Link to="/accounts">Accounts</Link>.
          </Trans>
        </p>
      ) : (
        <div className="ui-actions pots-toolbar">
          <Button
            variant="primary"
            disabled={accounts === null}
            onClick={() => {
              setNotice(null);
              setDialog({ kind: "new" });
            }}
          >
            <Trans>New pot</Trans>
          </Button>
          <Button
            disabled={!withEmergency || withEmergency.length === 0}
            onClick={() => {
              const first = withEmergency?.[0];
              if (first)
                setDialog({ kind: "emergency", accountId: first.account_id });
            }}
          >
            <Trans>Emergency withdrawal</Trans>
          </Button>
        </div>
      )}

      {notice && (
        <p role="status" className="ui-notice">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div ref={dialogRef}>{dialogView}</div>

      {overview === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : overview.accounts.length === 0 ? (
        <p className="ui-hint">
          <Trans>No pots yet. Create the first one with New pot.</Trans>
        </p>
      ) : (
        overview.accounts.map((split) => {
          const account = nameOf(split.account_id);
          const mine = pots.filter((p) => p.account_id === split.account_id);
          const openMine = mine.filter((p) => !p.closed);
          const closed = mine.filter((p) => p.closed);
          return (
            <Card
              as="section"
              key={split.account_id}
              className="pot-account"
              aria-label={account}
            >
              <h2>{account}</h2>
              <div className="ui-kpis">
                <KpiTile
                  label={<Trans>Account balance</Trans>}
                  value={money(split.balance_cents)}
                />
                <KpiTile
                  label={<Trans>In pots</Trans>}
                  value={money(split.pots_cents)}
                />
                <KpiTile
                  label={<Trans>Free balance</Trans>}
                  value={money(split.free_cents)}
                  tone={split.free_cents < 0 ? "alert" : "default"}
                  sub={<Trans>Balance minus all pots</Trans>}
                />
                <KpiTile
                  label={<Trans>Available in an emergency</Trans>}
                  value={money(split.emergency_available_cents)}
                  sub={<Trans>Unlocked pots, in rank order</Trans>}
                />
              </div>
              <OrderList
                accountId={split.account_id}
                ranked={orderedPots(pots, split.account_id)}
                locked={openMine.filter((p) => p.locked)}
                onReorder={(ids) => onReorder(split.account_id, ids)}
              />
              <div className="pot-grid">
                {openMine.map((pot) => (
                  <PotCard
                    key={pot.id}
                    pot={pot}
                    canMove={openMine.length > 1}
                    onAction={onAction}
                  />
                ))}
              </div>
              {closed.length > 0 && (
                <details className="pot-closed">
                  <summary>
                    <Trans>Closed pots ({closed.length})</Trans>
                  </summary>
                  <ul>
                    {closed.map((pot) => (
                      <li key={pot.id}>
                        {pot.name}
                        {pot.closed_at &&
                          ` · ${formatDate(pot.closed_at.slice(0, 10), i18n.locale)}`}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </Card>
          );
        })
      )}
    </section>
  );
}

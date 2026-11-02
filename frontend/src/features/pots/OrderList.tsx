import { Trans, useLingui } from "@lingui/react/macro";
import { useState } from "react";

import { formatCents } from "../../api/client";
import { Badge, Button, cx } from "../../ui";
import { reorder } from "./draft";
import type { Pot } from "./potsApi";

interface Props {
  accountId: number;
  /** Open, unlocked pots of the account in rank order. */
  ranked: Pot[];
  /** Open, locked pots of the account. */
  locked: Pot[];
  /** Called with the new order of all ranked pots. */
  onReorder: (potIds: number[]) => void;
}

/**
 * The emergency order of one account (UC-ZF-011 step 4, AK7): rank 1 is attacked first. A pot is
 * dragged onto another one to take its place (native HTML5 drag and drop, nothing to bundle); the
 * Up/Down buttons do the same for the keyboard. Locked pots are listed below and never move.
 */
export function OrderList({ accountId, ranked, locked, onReorder }: Props) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);
  const ids = ranked.map((pot) => pot.id);

  function change(next: number[] | null) {
    if (next) onReorder(next);
  }

  if (ranked.length === 0 && locked.length === 0) return null;

  return (
    <div className="pot-order">
      <h3 id={`pot-order-${accountId}`}>
        <Trans>Emergency order</Trans>
      </h3>
      <p className="ui-hint">
        <Trans>
          In an emergency rank 1 is used first. Drag a pot onto another one to
          change the order, or use the arrow buttons.
        </Trans>
      </p>
      <ol className="pot-order-list" aria-labelledby={`pot-order-${accountId}`}>
        {ranked.map((pot, index) => {
          const name = pot.name;
          return (
            <li
              key={pot.id}
              className={cx(
                "pot-order-item",
                dragId === pot.id && "pot-order-dragging",
                overId === pot.id && dragId !== pot.id && "pot-order-over",
              )}
              draggable
              data-pot-id={pot.id}
              onDragStart={(event) => {
                setDragId(pot.id);
                if (event.dataTransfer) {
                  event.dataTransfer.effectAllowed = "move";
                  // Firefox starts a drag only when some data is set.
                  event.dataTransfer.setData("text/plain", String(pot.id));
                }
              }}
              onDragOver={(event) => {
                if (dragId === null) return;
                event.preventDefault();
                if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
                setOverId(pot.id);
              }}
              onDragLeave={() =>
                setOverId((now) => (now === pot.id ? null : now))
              }
              onDrop={(event) => {
                event.preventDefault();
                if (dragId !== null) {
                  change(reorder(ids, dragId, { before: pot.id }));
                }
                setDragId(null);
                setOverId(null);
              }}
              onDragEnd={() => {
                setDragId(null);
                setOverId(null);
              }}
            >
              <span className="pot-order-rank num">{index + 1}</span>
              <span className="pot-order-grip" aria-hidden="true">
                ⠿
              </span>
              <span className="pot-order-name">{pot.name}</span>
              <span className="pot-order-balance num">
                {money(pot.balance_cents)}
              </span>
              <span className="pot-order-buttons">
                <Button
                  aria-label={t`Move ${name} up`}
                  disabled={index === 0}
                  onClick={() =>
                    change(reorder(ids, pot.id, { index: index - 1 }))
                  }
                >
                  ↑
                </Button>
                <Button
                  aria-label={t`Move ${name} down`}
                  disabled={index === ranked.length - 1}
                  onClick={() =>
                    change(reorder(ids, pot.id, { index: index + 1 }))
                  }
                >
                  ↓
                </Button>
              </span>
            </li>
          );
        })}
        {locked.map((pot) => (
          <li key={pot.id} className="pot-order-item pot-order-locked">
            <span className="pot-order-rank" aria-hidden="true">
              —
            </span>
            <span className="pot-order-grip" aria-hidden="true" />
            <span className="pot-order-name">
              {pot.name}
              <Badge>
                <Trans>Locked</Trans>
              </Badge>
            </span>
            <span className="pot-order-balance num">
              {money(pot.balance_cents)}
            </span>
            <span className="pot-order-buttons ui-hint">
              <Trans>never used in an emergency</Trans>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

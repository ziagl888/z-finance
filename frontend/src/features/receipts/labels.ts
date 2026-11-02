import { msg } from "@lingui/core/macro";
import type { MessageDescriptor } from "@lingui/core";

import type { Area } from "../accounts/accountsApi";
import type { ReviewReason } from "./receiptsApi";

/** Why a receipt is on the review list; render with `i18n._(...)`. */
export const REASON_LABELS: Record<ReviewReason, MessageDescriptor> = {
  unreadable: msg`Photo not readable`,
  missing_fields: msg`Date, total or merchant missing`,
  foreign_currency: msg`Foreign currency`,
  low_confidence: msg`Agent was unsure`,
  no_account: msg`No account for this payment`,
  not_bookable: msg`Account does not accept this date`,
};

/** Label of each account choice in the settings (area × payment method). */
export const MAPPING_LABELS: Record<
  Area,
  Record<"cash" | "card", MessageDescriptor>
> = {
  private: {
    cash: msg`Cash payments, private`,
    card: msg`Card payments, private`,
  },
  business: {
    cash: msg`Cash payments, business`,
    card: msg`Card payments, business`,
  },
};

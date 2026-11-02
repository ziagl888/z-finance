import { msg } from "@lingui/core/macro";
import type { MessageDescriptor } from "@lingui/core";

import type { AccountType, Area, CategoryKind } from "./accountsApi";

/** Display names of the stored codes; render with `i18n._(...)`. */
export const AREA_LABELS: Record<Area, MessageDescriptor> = {
  private: msg`Private`,
  business: msg`Business`,
};

export const ACCOUNT_TYPE_LABELS: Record<AccountType, MessageDescriptor> = {
  giro: msg`Current account`,
  savings: msg`Savings account`,
  cash: msg`Cash`,
  card: msg`Credit card`,
  liability: msg`Liability`,
};

export const CATEGORY_KIND_LABELS: Record<CategoryKind, MessageDescriptor> = {
  income: msg`Income`,
  expense: msg`Expense`,
  transfer: msg`Transfer`,
};

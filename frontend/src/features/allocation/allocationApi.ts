import { api } from "../../api/client";

/** Where a share goes. `levy` exists only as a derived line, never as a stored target. */
export type LineKind = "levy" | "debt_plan" | "pot" | "reserve";
export type TargetKind = "debt_plan" | "pot" | "reserve";
export const TARGET_KINDS: TargetKind[] = ["debt_plan", "pot", "reserve"];
export type FallbackKind = "pot" | "reserve";

/** 100 % in basis points: the percentages of a rule may not add up to more. */
export const FULL_BP = 10_000;

/** One share of a rule: `percent_bp` (2000 = 20 %) **or** `fixed_cents`, not both. */
export interface RuleTarget {
  kind: TargetKind;
  pot_id: number | null;
  percent_bp: number | null;
  fixed_cents: number | null;
}

/** Body of POST/PUT /api/allocation/rules (full replacement). */
export interface RuleInput {
  name: string;
  area: "private" | "business" | null;
  category_id: number | null;
  min_amount_cents: number;
  targets: RuleTarget[];
  fallback_kind: FallbackKind;
  fallback_pot_id: number | null;
  active: boolean;
}

export interface Rule extends RuleInput {
  id: number;
  created_at: string;
}

/** One line of a proposal; `target` is empty for the total reserve (this screen names it). */
export interface ProposalLine {
  kind: LineKind;
  amount_cents: number;
  floor_cents: number;
  capped: boolean;
  target: string;
  pot_id: number | null;
  debt_id: number | null;
  editable: boolean;
  books: boolean;
}

/** A proposal for one booked income; `overdue` after seven open days (AK5). */
export interface Proposal {
  id: number;
  transaction_id: number | null;
  rule_id: number | null;
  rule_name: string | null;
  amount_cents: number;
  text: string;
  account_id: number;
  account_name: string;
  date: string;
  status: string;
  lines: ProposalLine[];
  remaining_cents: number;
  bookable_cents: number;
  free_cents: number;
  reserve_warning: boolean;
  reduced_total_cents: number;
  days_open: number;
  overdue: boolean;
  created_at: string;
  confirmed_at: string | null;
}

/** A confirmed proposal whose income was deleted (UC 2a); nothing is reversed automatically. */
export interface Reversal {
  proposal_id: number;
  text: string;
  amount_cents: number;
  date: string;
  booked_cents: number;
  transfer_group_ids: string[];
  confirmed_at: string | null;
}

export interface PotOption {
  id: number;
  name: string;
  account_id: number;
  account_name: string;
  is_levy_pot: boolean;
}

export interface AllocationOverview {
  rules: Rule[];
  proposals: Proposal[];
  reversals: Reversal[];
  pots: PotOption[];
  levy_rate_bp: number;
  levy_pot_id: number | null;
  levy_pot_missing: boolean;
  free_cents: number;
  skipped_this_month: number;
}

export interface Confirmation {
  proposal: Proposal;
  transfer_count: number;
  booked_cents: number;
}

export const getAllocation = () =>
  api.get<AllocationOverview>("/api/allocation");
export const postRule = (input: RuleInput) =>
  api.post<Rule>("/api/allocation/rules", input);
export const putRule = (id: number, input: RuleInput) =>
  api.put<Rule>(`/api/allocation/rules/${id}`, input);
export const deleteRule = (id: number) =>
  api.delete<void>(`/api/allocation/rules/${id}`);
export const confirmProposal = (id: number, amounts: Record<number, number>) =>
  api.post<Confirmation>(`/api/allocation/proposals/${id}/confirm`, {
    amounts,
  });
export const skipProposal = (id: number) =>
  api.post<Proposal>(`/api/allocation/proposals/${id}/skip`);

/** An empty rule for the editor: the whole income of any area, nothing allocated yet. */
export function emptyRule(): RuleInput {
  return {
    name: "",
    area: "business",
    category_id: null,
    min_amount_cents: 0,
    targets: [
      { kind: "debt_plan", pot_id: null, percent_bp: 2000, fixed_cents: null },
    ],
    fallback_kind: "reserve",
    fallback_pot_id: null,
    active: true,
  };
}

/** The stored rule as the editor's input (the id and the timestamp are dropped). */
export function toInput(rule: Rule): RuleInput {
  return {
    name: rule.name,
    area: rule.area,
    category_id: rule.category_id,
    min_amount_cents: rule.min_amount_cents,
    targets: rule.targets.map((target) => ({ ...target })),
    fallback_kind: rule.fallback_kind,
    fallback_pot_id: rule.fallback_pot_id,
    active: rule.active,
  };
}

/** Basis points as a percentage in the UI locale ("20", "12.5"). */
export function formatBp(bp: number, locale: string): string {
  return (bp / 100).toLocaleString(locale);
}

/** Σ of the percentage shares in basis points (the editor shows it live). */
export function percentSum(targets: RuleTarget[]): number {
  return targets.reduce((sum, target) => sum + (target.percent_bp ?? 0), 0);
}

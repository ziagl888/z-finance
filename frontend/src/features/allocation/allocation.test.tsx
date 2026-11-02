// @vitest-environment jsdom
/**
 * Allocation (task -025, UC-ZF-014): the rule editor, the proposal list with its read-only levy
 * line, the reserve warning and the two actions. The API is a fake `fetch`; every value is
 * synthetic and taken from the acceptance criteria.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { AllocationPage } from "./AllocationPage";
import { ProposalCard } from "./ProposalList";
import { RuleForm } from "./RuleForm";
import {
  emptyRule,
  percentSum,
  type AllocationOverview,
  type Proposal,
} from "./allocationApi";

const PROPOSAL: Proposal = {
  id: 7,
  transaction_id: 3,
  rule_id: 1,
  rule_name: "Test business income",
  amount_cents: 400_000,
  text: "Test client fee",
  account_id: 1,
  account_name: "Test business",
  date: "2026-09-19",
  status: "open",
  lines: [
    {
      kind: "levy",
      amount_cents: 120_000,
      floor_cents: 120_000,
      capped: false,
      target: "Test levies",
      pot_id: 1,
      debt_id: null,
      editable: false,
      books: true,
    },
    {
      kind: "debt_plan",
      amount_cents: 80_000,
      floor_cents: 0,
      capped: false,
      target: "Test lender",
      pot_id: null,
      debt_id: 4,
      editable: true,
      books: true,
    },
    {
      kind: "pot",
      amount_cents: 20_000,
      floor_cents: 0,
      capped: false,
      target: "Test reserve",
      pot_id: 2,
      debt_id: null,
      editable: true,
      books: true,
    },
  ],
  remaining_cents: 180_000,
  bookable_cents: 100_000,
  free_cents: 280_000,
  reserve_warning: false,
  reduced_total_cents: 100_000,
  days_open: 0,
  overdue: false,
  created_at: "2026-09-19T08:00:00+00:00",
  confirmed_at: null,
};

const OVERVIEW: AllocationOverview = {
  rules: [
    {
      id: 1,
      name: "Test business income",
      area: "business",
      category_id: null,
      min_amount_cents: 50_000,
      targets: [
        {
          kind: "debt_plan",
          pot_id: null,
          percent_bp: 2000,
          fixed_cents: null,
        },
        { kind: "pot", pot_id: 2, percent_bp: 500, fixed_cents: null },
      ],
      fallback_kind: "pot",
      fallback_pot_id: 2,
      active: true,
      created_at: "2026-09-01T08:00:00+00:00",
    },
  ],
  proposals: [PROPOSAL],
  reversals: [],
  pots: [
    {
      id: 1,
      name: "Test levies",
      account_id: 2,
      account_name: "Test savings",
      is_levy_pot: true,
    },
    {
      id: 2,
      name: "Test reserve",
      account_id: 2,
      account_name: "Test savings",
      is_levy_pot: false,
    },
  ],
  levy_rate_bp: 3000,
  levy_pot_id: 1,
  levy_pot_missing: false,
  free_cents: 280_000,
  skipped_this_month: 0,
};

function renderWithI18n(node: React.ReactNode) {
  return render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>{node}</MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("helpers", () => {
  it("sums the percentage shares and starts an empty rule", () => {
    // Oracle: spec-anchored — UC-ZF-014 precondition (Σ shares ≤ 100 %), hand-computed.
    expect(percentSum(OVERVIEW.rules[0]!.targets)).toBe(2500);
    expect(percentSum([])).toBe(0);
    expect(emptyRule().targets).toHaveLength(1);
  });
});

describe("proposal", () => {
  it("shows the lines with a read-only levy share and confirms the edited amounts", async () => {
    // Oracle: spec-anchored — UC-ZF-014 AK1 (1,200 / 800 / 200, 1,800 free), AK7 (the levy share
    // cannot be changed) and step 4 (confirm after adjusting single amounts).
    const onConfirm = vi.fn(async () => {});
    const onSkip = vi.fn(async () => {});
    renderWithI18n(
      <ProposalCard
        proposal={PROPOSAL}
        onConfirm={onConfirm}
        onSkip={onSkip}
      />,
    );

    expect(screen.getByText(/Test client fee/)).toBeTruthy();
    expect(screen.getByText("€1,200.00")).toBeTruthy();
    expect(screen.queryByLabelText("Amount for Test levies")).toBeNull();
    const debtAmount = screen.getByLabelText("Amount for Test lender");
    expect((debtAmount as HTMLInputElement).value).toBe("800.00");

    fireEvent.change(debtAmount, { target: { value: "500" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and book" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect(onConfirm).toHaveBeenCalledWith(7, { 1: 50_000 });
    expect(onSkip).not.toHaveBeenCalled();
  });

  it("refuses shares above the income and highlights a proposal open for seven days", () => {
    // Oracle: spec-anchored — contract sketch ("Σ lines ≤ income") and AK5 (seven days highlighted).
    const onConfirm = vi.fn(async () => {});
    renderWithI18n(
      <ProposalCard
        proposal={{ ...PROPOSAL, days_open: 7, overdue: true }}
        onConfirm={onConfirm}
        onSkip={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText("open for 7 days")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Amount for Test lender"), {
      target: { value: "4000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and book" }));
    expect(screen.getByRole("alert").textContent).toMatch(
      /more than the income/,
    );
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("warns when the allocation would eat into the reserve", () => {
    // Oracle: spec-anchored — UC-ZF-014 3a (reserve warning with a reduced amount).
    renderWithI18n(
      <ProposalCard
        proposal={{
          ...PROPOSAL,
          reserve_warning: true,
          free_cents: 50_000,
          reduced_total_cents: 50_000,
        }}
        onConfirm={vi.fn(async () => {})}
        onSkip={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByRole("alert").textContent).toMatch(/€500.00/);
  });
});

describe("rule form", () => {
  it("sends trigger, shares and fallback, and refuses shares above 100 %", async () => {
    // Oracle: spec-anchored — UC-ZF-014 step 1 (trigger, shares, fallback) and its precondition.
    const onSave = vi.fn(async () => {});
    renderWithI18n(
      <RuleForm
        value={emptyRule()}
        pots={OVERVIEW.pots}
        levyRateBp={3000}
        editing={false}
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/levy share of 30 %/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Percentage of target 1"), {
      target: { value: "120" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Test rule" },
    });
    fireEvent.change(screen.getByLabelText("From this amount"), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(screen.getByRole("alert").textContent).toMatch(/more than 100 %/);
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Percentage of target 1"), {
      target: { value: "20" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({
      name: "Test rule",
      area: "business",
      category_id: null,
      min_amount_cents: 50_000,
      targets: [
        {
          kind: "debt_plan",
          pot_id: null,
          percent_bp: 2000,
          fixed_cents: null,
        },
      ],
      fallback_kind: "reserve",
      fallback_pot_id: null,
      active: true,
    });
  });
});

describe("allocation page", () => {
  it("lists the rules and the proposals of the overview", async () => {
    // Oracle: spec-anchored — UC-ZF-014 step 3 (the proposal appears under "To allocate") and the
    // rule list of step 1.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(OVERVIEW)),
    );
    renderWithI18n(<AllocationPage />);

    // The heading is there while loading too, so wait for a value of the overview.
    expect(await screen.findByText("Test business income")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "Allocation",
    );
    expect(screen.getByText(/Test client fee/)).toBeTruthy();
    expect(screen.getByText("25 %")).toBeTruthy();
    expect(screen.getByText("30 %")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New rule" })).toBeTruthy();
  });

  it("shows the reversal suggestion of a removed income and no rule hint when empty", async () => {
    // Oracle: spec-anchored — UC-ZF-014 2a (the bookings of a deleted income are only suggested).
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          ...OVERVIEW,
          rules: [],
          proposals: [],
          reversals: [
            {
              proposal_id: 7,
              text: "Test client fee",
              amount_cents: 400_000,
              date: "2026-09-19",
              booked_cents: 220_000,
              transfer_group_ids: ["a", "b", "c"],
              confirmed_at: "2026-09-19T09:00:00+00:00",
            },
          ],
        }),
      ),
    );
    renderWithI18n(<AllocationPage />);

    await waitFor(() =>
      expect(screen.getByText(/These incomes are gone/)).toBeTruthy(),
    );
    expect(screen.getByText("€2,200.00")).toBeTruthy();
    expect(screen.getByText(/No rule yet/)).toBeTruthy();
    expect(screen.getByText(/Nothing to allocate/)).toBeTruthy();
  });
});

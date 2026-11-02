// @vitest-environment jsdom
/**
 * Shared UI components (task -032): one smoke test per component for the contract it promises —
 * element, role, class hooks, accessible wiring. Visual appearance is checked in the browser.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { ReactNode, SyntheticEvent } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatCents } from "../api/client";
import { activateLocale } from "../i18n/locale";
import {
  Badge,
  Button,
  Card,
  Dialog,
  Field,
  KpiTile,
  MoneyCell,
  selectableRow,
  Table,
} from ".";

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(cleanup);

const withI18n = (node: ReactNode) =>
  render(<I18nProvider i18n={i18n}>{node}</I18nProvider>);

describe("Button", () => {
  it("defaults to a secondary non-submit button", () => {
    /** Oracle: spec-anchored — SPEC -032 contract sketch (type defaults to "button"). */
    render(<Button>Test action</Button>);
    const button = screen.getByRole("button", { name: "Test action" });
    expect(button.getAttribute("type")).toBe("button");
    expect(button.className).toBe("ui-button ui-button-secondary");
  });

  it("keeps an explicit submit type and the variant", () => {
    /** Oracle: spec-anchored — SPEC -032 AK3 (primary/secondary/danger variants). */
    render(
      <Button type="submit" variant="primary" disabled>
        Test save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Test save" });
    expect(button.getAttribute("type")).toBe("submit");
    expect(button.classList.contains("ui-button-primary")).toBe(true);
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Card", () => {
  it("renders the requested element with the card class", () => {
    /** Oracle: spec-anchored — SPEC -032 AK3 (Card; receipts need an article element). */
    render(
      <Card as="article" aria-label="Test card">
        <h3>Test title</h3>
      </Card>,
    );
    const card = screen.getByRole("article", { name: "Test card" });
    expect(card.className).toBe("ui-card");
  });
});

describe("KpiTile", () => {
  it("shows label, value and sub line, tinted for alert", () => {
    /** Oracle: spec-anchored — SPEC -032 AK3 (KpiTile after dev-front). */
    const { container } = render(
      <KpiTile label="Test label" value="12" sub="Test sub" tone="alert" />,
    );
    const tile = container.firstElementChild as HTMLElement;
    expect(tile.className).toBe("ui-kpi ui-kpi-alert");
    expect(tile.textContent).toBe("Test label12Test sub");
  });
});

describe("Badge", () => {
  it("renders a pill with the tone class", () => {
    /** Oracle: spec-anchored — SPEC -032 AK3 (Badge planned/skipped/overdue). */
    render(<Badge tone="overdue">Test overdue</Badge>);
    expect(screen.getByText("Test overdue").className).toBe(
      "ui-badge ui-badge-overdue",
    );
  });
});

describe("MoneyCell", () => {
  it("formats cents in the UI locale and marks negatives", () => {
    /** Oracle: spec-anchored — SPEC -032 contract sketch (text = formatCents, negative class). */
    withI18n(
      <table>
        <tbody>
          <tr>
            <MoneyCell cents={-123_45} />
            <MoneyCell cents={500} />
            <MoneyCell plain cents={-700} />
          </tr>
        </tbody>
      </table>,
    );
    const [negative, positive, plain] = screen.getAllByRole("cell");
    expect(negative!.textContent).toBe(formatCents(-123_45, "en"));
    expect(negative!.classList.contains("ui-money-negative")).toBe(true);
    expect(positive!.textContent).toBe(formatCents(500, "en"));
    expect(positive!.classList.contains("ui-money-negative")).toBe(false);
    expect(positive!.classList.contains("num")).toBe(true);
    expect(plain!.textContent).toBe(formatCents(-700, "en"));
    expect(plain!.classList.contains("ui-money-negative")).toBe(false);
  });
});

describe("Table", () => {
  it("renders a zebra table by default", () => {
    /** Oracle: spec-anchored — SPEC -032 AK3 (Table: sticky header, subtle zebra). */
    render(
      <Table aria-label="Test table">
        <tbody>
          <tr>
            <td>x</td>
          </tr>
        </tbody>
      </Table>,
    );
    expect(screen.getByRole("table", { name: "Test table" }).className).toBe(
      "ui-table ui-table-zebra",
    );
  });
});

describe("Field", () => {
  it("links label, hint and error to the control", () => {
    /** Oracle: spec-anchored — SPEC -032 contract sketch (htmlFor, aria-describedby, role=alert). */
    render(
      <Field
        htmlFor="test-amount"
        label="Test amount"
        hint="Test hint"
        error="Test error"
      >
        <input id="test-amount" />
      </Field>,
    );
    const input = screen.getByLabelText("Test amount");
    expect(input.getAttribute("aria-describedby")).toBe(
      "test-amount-hint test-amount-error",
    );
    expect(screen.getByRole("alert").textContent).toBe("Test error");
    expect(document.getElementById("test-amount-hint")?.textContent).toBe(
      "Test hint",
    );
  });

  it("keeps the control's own aria-describedby", () => {
    /** Oracle: spec-anchored — SPEC -032 contract sketch (only added when the control has none). */
    render(
      <Field htmlFor="test-x" label="Test x" hint="Test hint">
        <input id="test-x" aria-describedby="own-id" />
      </Field>,
    );
    expect(
      screen.getByLabelText("Test x").getAttribute("aria-describedby"),
    ).toBe("own-id");
  });
});

describe("Dialog", () => {
  it("is a named dialog, as a form too, and Escape closes it", () => {
    /** Oracle: spec-anchored — SPEC -032 contract sketch (role=dialog, Escape → onClose). */
    const onClose = vi.fn();
    const onSubmit = vi.fn((event: SyntheticEvent) => event.preventDefault());
    render(
      <Dialog
        as="form"
        label="Test dialog"
        onClose={onClose}
        onSubmit={onSubmit}
      >
        <button type="submit">Test submit</button>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog", { name: "Test dialog" });
    expect(dialog.tagName).toBe("FORM");
    fireEvent.click(screen.getByRole("button", { name: "Test submit" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("selectableRow", () => {
  it("selects on click, Enter and Space, not on keys from inner controls", () => {
    /** Oracle: spec-anchored — SPEC -032 AK6 (row focusable, Enter/Space select like a click). */
    const onSelect = vi.fn();
    render(
      <table>
        <tbody>
          <tr {...selectableRow(onSelect)} aria-label="Test row">
            <td>
              <button type="button">Test inner</button>
            </td>
          </tr>
        </tbody>
      </table>,
    );
    const row = screen.getByRole("row", { name: "Test row" });
    expect(row.tabIndex).toBe(0);
    fireEvent.click(row);
    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.keyDown(row, { key: " " });
    fireEvent.keyDown(row, { key: "a" });
    fireEvent.keyDown(screen.getByRole("button", { name: "Test inner" }), {
      key: "Enter",
    });
    expect(onSelect).toHaveBeenCalledTimes(3);
  });
});

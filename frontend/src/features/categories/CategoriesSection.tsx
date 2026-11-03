import { plural } from "@lingui/core/macro";
import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Button, Card, Field, Table } from "../../ui";

import {
  AREAS,
  CATEGORY_KINDS,
  createCategory,
  deleteCategory,
  updateCategory,
  type Area,
  type Category,
  type CategoryInput,
  type CategoryKind,
  type RunChange,
} from "../accounts/accountsApi";
import { AREA_LABELS, CATEGORY_KIND_LABELS } from "../accounts/labels";

const emptyDraft = (): CategoryInput => ({
  name: "",
  kind: "expense",
  area: null,
  parent_id: null,
});

/** Top-level categories, each followed by its subcategories (the API sorts by kind and name). */
function tree(categories: Category[]): { category: Category; depth: 0 | 1 }[] {
  const rows: { category: Category; depth: 0 | 1 }[] = [];
  for (const top of categories.filter((c) => c.parent_id === null)) {
    rows.push({ category: top, depth: 0 });
    for (const child of categories.filter((c) => c.parent_id === top.id)) {
      rows.push({ category: child, depth: 1 });
    }
  }
  return rows;
}

interface Props {
  categories: Category[] | null;
  run: RunChange;
}

/** Category tree (one level), the form to create or edit, and delete with re-booking. */
export function CategoriesSection({ categories, run }: Props) {
  const { t, i18n } = useLingui();
  const [draft, setDraft] = useState<CategoryInput>(emptyDraft);
  const [editing, setEditing] = useState<number | null>(null);
  const [removing, setRemoving] = useState<Category | null>(null);
  const [target, setTarget] = useState<string>("");

  const update = (changes: Partial<CategoryInput>) =>
    setDraft((current) => ({ ...current, ...changes }));

  const all = categories ?? [];
  // A parent is a top-level category of the same kind, never the edited category itself.
  const parents = all.filter(
    (c) => c.parent_id === null && c.kind === draft.kind && c.id !== editing,
  );

  function startEdit(category: Category) {
    setEditing(category.id);
    setDraft({
      name: category.name,
      kind: category.kind,
      area: category.area,
      parent_id: category.parent_id,
    });
  }

  function reset() {
    setEditing(null);
    setDraft(emptyDraft());
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const saved = await run(() =>
      editing === null ? createCategory(draft) : updateCategory(editing, draft),
    );
    if (saved) reset();
  }

  function onDelete(category: Category) {
    if (category.booking_count > 0) {
      setRemoving(category);
      setTarget("");
      return;
    }
    const { name } = category;
    if (window.confirm(t`Delete the category ${name}?`)) {
      void run(() => deleteCategory(category.id));
    }
  }

  async function onMoveAndDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (removing === null || target === "") return;
    const done = await run(() => deleteCategory(removing.id, Number(target)));
    if (done) setRemoving(null);
  }

  // Lingui placeholders must be plain identifiers.
  const removingName = removing?.name ?? "";
  const bookingCount = removing?.booking_count ?? 0;

  return (
    <Card>
      <h2>
        <Trans>All categories</Trans>
      </h2>
      {categories === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : categories.length === 0 ? (
        <p>
          <Trans>No categories yet.</Trans>
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <th>
                <Trans>Name</Trans>
              </th>
              <th>
                <Trans>Kind</Trans>
              </th>
              <th>
                <Trans>Area</Trans>
              </th>
              <th>
                <Trans>Actions</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {tree(categories).map(({ category, depth }) => (
              <tr key={category.id}>
                <td className={depth ? "accounts-child" : ""}>
                  {category.name}
                </td>
                <td>{i18n._(CATEGORY_KIND_LABELS[category.kind])}</td>
                <td>
                  {category.area ? i18n._(AREA_LABELS[category.area]) : "—"}
                </td>
                <td className="ui-actions">
                  <button type="button" onClick={() => startEdit(category)}>
                    <Trans>Edit</Trans>
                  </button>
                  <Button
                    variant="danger"
                    type="button"
                    onClick={() => onDelete(category)}
                  >
                    <Trans>Delete</Trans>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {removing && (
        <form className="accounts-form" onSubmit={onMoveAndDelete}>
          <h3>
            <Trans>Delete {removingName}</Trans>
          </h3>
          <p>
            {t`${plural(bookingCount, {
              one: "This category has # booking.",
              other: "This category has # bookings.",
            })}`}{" "}
            <Trans>Choose the category they move to.</Trans>
          </p>
          <div className="ui-form-grid">
            <Field
              htmlFor="category-target"
              label={<Trans>Move bookings to</Trans>}
            >
              <select
                id="category-target"
                required
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              >
                <option value="">{t`Choose…`}</option>
                {all
                  .filter((c) => c.id !== removing.id)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          <div className="ui-actions">
            <Button variant="danger" type="submit" disabled={target === ""}>
              <Trans>Move and delete</Trans>
            </Button>
            <button type="button" onClick={() => setRemoving(null)}>
              <Trans>Cancel</Trans>
            </button>
          </div>
        </form>
      )}

      <form className="accounts-form" onSubmit={onSubmit}>
        <h3>{editing === null ? t`New category` : t`Edit category`}</h3>
        <div className="ui-form-grid">
          <Field htmlFor="category-name" label={<Trans>Name</Trans>}>
            <input
              id="category-name"
              required
              maxLength={100}
              value={draft.name}
              onChange={(event) => update({ name: event.target.value })}
            />
          </Field>
          <Field htmlFor="category-kind" label={<Trans>Kind</Trans>}>
            <select
              id="category-kind"
              value={draft.kind}
              onChange={(event) =>
                update({
                  kind: event.target.value as CategoryKind,
                  parent_id: null,
                })
              }
            >
              {CATEGORY_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {i18n._(CATEGORY_KIND_LABELS[kind])}
                </option>
              ))}
            </select>
          </Field>
          <Field htmlFor="category-area" label={<Trans>Area</Trans>}>
            <select
              id="category-area"
              value={draft.area ?? ""}
              onChange={(event) =>
                update({ area: (event.target.value || null) as Area | null })
              }
            >
              <option value="">{t`Both areas`}</option>
              {AREAS.map((area) => (
                <option key={area} value={area}>
                  {i18n._(AREA_LABELS[area])}
                </option>
              ))}
            </select>
          </Field>
          <Field
            htmlFor="category-parent"
            label={<Trans>Parent category</Trans>}
          >
            <select
              id="category-parent"
              value={draft.parent_id ?? ""}
              onChange={(event) =>
                update({
                  parent_id: event.target.value
                    ? Number(event.target.value)
                    : null,
                })
              }
            >
              <option value="">{t`None (top level)`}</option>
              {parents.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="ui-actions">
          <Button variant="primary" type="submit">
            {editing === null ? t`Create category` : t`Save changes`}
          </Button>
          {editing !== null && (
            <button type="button" onClick={reset}>
              <Trans>Cancel</Trans>
            </button>
          )}
        </div>
      </form>
    </Card>
  );
}

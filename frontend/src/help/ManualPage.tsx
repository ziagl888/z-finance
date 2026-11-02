import { useSearchParams } from "react-router";

import { Handbook } from "./Handbook";
import { START_CHAPTER } from "./manual";

/** Path of the in-app manual page; equal to `path` in features/manual/route.tsx, which must stay a literal. */
export const MANUAL_PATH = "manual";

/** Link to a chapter in the in-app manual. */
export function manualHref(slug: string): string {
  return slug === START_CHAPTER
    ? `/${MANUAL_PATH}`
    : `/${MANUAL_PATH}?chapter=${encodeURIComponent(slug)}`;
}

/** "Manual" screen: the whole manual, the selected chapter in the `chapter` query parameter. */
export function ManualPage() {
  const [params, setParams] = useSearchParams();
  const slug = params.get("chapter") ?? START_CHAPTER;
  return (
    <Handbook
      slug={slug}
      onSelect={(next) =>
        setParams(next === START_CHAPTER ? {} : { chapter: next })
      }
    />
  );
}

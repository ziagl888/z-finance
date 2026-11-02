import { useMemo, type MouseEvent } from "react";

import { chapterLinkTarget, renderChapter, type Chapter } from "./manual";

/**
 * One rendered chapter. Links to other chapters (`settings.md`) call `onNavigate` instead of
 * leaving the page; every other link behaves normally.
 */
export function ChapterView({
  chapter,
  onNavigate,
}: {
  chapter: Chapter;
  onNavigate: (slug: string) => void;
}) {
  const html = useMemo(() => renderChapter(chapter), [chapter]);

  const onClick = (event: MouseEvent<HTMLElement>) => {
    const link = (event.target as Element).closest("a");
    const target = chapterLinkTarget(link?.getAttribute("href") ?? "");
    if (target) {
      event.preventDefault();
      onNavigate(target);
    }
  };

  return (
    // Click delegation for the chapter links inside the rendered Markdown; they are real <a>
    // elements, so keyboard activation fires this click too.
    <article
      className="manual-chapter"
      lang={chapter.lang}
      onClick={onClick}
      // markdown-it runs with html: false, so the source cannot inject markup (see manual.ts).
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

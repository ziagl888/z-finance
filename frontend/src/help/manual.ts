/**
 * The manual: Markdown chapters from `docs/manual/<lang>/<chapter>.md`, embedded into the bundle at
 * build time (`import.meta.glob` with `?raw`), so help works offline. See ARCHITECTURE.md
 * "Help and manual".
 *
 * Invariants:
 * - A screen's chapter is its route's first path segment; the start page ("") is `index`
 *   (mirrored by `chapter_for_path` in tools/check_manual_coverage.py, which gates coverage).
 * - A chapter missing in a language falls back to English; `index` always sorts first.
 */
import MarkdownIt from "markdown-it";

export interface Chapter {
  /** File name without `.md`: `index`, or a route's first path segment. */
  slug: string;
  /** Language the chapter is written in (differs from the requested one on fallback). */
  lang: string;
  /** Text of the first `# ` heading. */
  title: string;
  /** Sort key from the front matter (`order: 20`), default 500. */
  order: number;
  /** Markdown after the front matter. */
  body: string;
  /** Plain text of title and body, for search. */
  text: string;
}

export interface SearchHit {
  chapter: Chapter;
  /** Text around the first match, with an ellipsis where it was cut. */
  snippet: string;
}

export const START_CHAPTER = "index";
const FALLBACK_LANG = "en";
const DEFAULT_ORDER = 500;
const SNIPPET_RADIUS = 60;

// Chapters are repository content, still no raw HTML reaches the DOM: `html: false` escapes it.
// markdown-it's link validation drops javascript:/vbscript:/file:/data: URLs.
const markdown = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
});

// Screenshots (docs/assets/screenshots/<lang>/<name>.png) are bundled by Vite like the chapters, so
// the manual shows them offline and the Pages site serves them next to the page. A chapter refers to
// one relative to itself (`../../assets/screenshots/en/entry.png`), which also renders on GitHub.
const imageUrls = import.meta.glob<string>(
  "../../../docs/assets/screenshots/*/*.png",
  { query: "?url", import: "default", eager: true },
);

const IMAGE_REFERENCE =
  /(?:^|\/)assets\/screenshots\/([a-z]{2}\/[a-z0-9-]+\.png)$/;

/** URL of the bundled screenshot a chapter's image reference points to, else null. */
export function resolveImage(
  reference: string,
  urls: Record<string, string> = imageUrls,
): string | null {
  const file = IMAGE_REFERENCE.exec(reference)?.[1];
  if (!file) return null;
  const key = Object.keys(urls).find((k) =>
    k.endsWith(`/assets/screenshots/${file}`),
  );
  return key ? (urls[key] ?? null) : null;
}

// An image opens full size in a new tab (a 1440 px screenshot is small in the chapter column); one
// that is not bundled shows its description instead of a broken picture.
markdown.renderer.rules.image = (tokens, index) => {
  const token = tokens[index]!;
  const alt = markdown.utils.escapeHtml(token.content);
  const url = resolveImage(String(token.attrGet("src") ?? ""));
  if (!url) return `<em>${alt}</em>`;
  const href = markdown.utils.escapeHtml(url);
  return `<a class="manual-figure" href="${href}" target="_blank" rel="noopener"><img src="${href}" alt="${alt}" loading="lazy"></a>`;
};

const FRONT_MATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/;

/** Split one chapter file into its parts. Exported for tests; the format is gated by the coverage tool. */
export function parseChapter(
  slug: string,
  lang: string,
  source: string,
): Chapter {
  let body = source;
  let order = DEFAULT_ORDER;
  const front = FRONT_MATTER.exec(source);
  if (front) {
    body = source.slice(front[0].length);
    const match = /^order:\s*(-?\d+)\s*$/m.exec(front[1] ?? "");
    if (match) order = Number(match[1]);
  }
  const title = /^# (.+)$/m.exec(body)?.[1]?.trim() ?? slug;
  return { slug, lang, title, order, body, text: plainText(body) };
}

function plainText(body: string): string {
  const parts: string[] = [];
  for (const token of markdown.parse(body, {})) {
    for (const child of token.children ?? []) {
      if (child.type === "text" || child.type === "code_inline")
        parts.push(child.content);
    }
    if (token.type === "fence" || token.type === "code_block")
      parts.push(token.content);
    if (token.block && token.type.endsWith("_close")) parts.push("\n");
  }
  return parts
    .join(" ")
    .replace(/[ \t]*\n[\s]*/g, "\n")
    .trim();
}

/** HTML of a chapter body. Safe to insert: markdown-it escapes any HTML in the source. */
export function renderChapter(chapter: Chapter): string {
  return markdown.render(chapter.body);
}

/** Chapter slug a relative link points to (`settings.md`, `./backup.md#restore`), else null. */
export function chapterLinkTarget(href: string): string | null {
  return (
    /^(?:\.\/)?([a-z0-9]+(?:-[a-z0-9]+)*)\.md(?:#.*)?$/.exec(href)?.[1] ?? null
  );
}

/** Chapter of the screen at `pathname`: its first segment, the start page `/` -> `index`. */
export function chapterForPath(pathname: string): string {
  const first = pathname.replace(/^\/+/, "").split("/", 1)[0] ?? "";
  return first === "" ? START_CHAPTER : first;
}

/** Lower-case and strip accents, keeping the string length so match positions stay valid. */
function fold(value: string): string {
  return Array.from(value)
    .map((c) => {
      const folded = c.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
      return folded.length === c.length ? folded : c;
    })
    .join("");
}

function snippetAround(text: string, index: number, length: number): string {
  const start = Math.max(0, index - SNIPPET_RADIUS);
  const end = Math.min(text.length, index + length + SNIPPET_RADIUS);
  const core = text.slice(start, end).replace(/\s+/g, " ").trim();
  return `${start > 0 ? "…" : ""}${core}${end < text.length ? "…" : ""}`;
}

/** A set of chapters in several languages. The app uses the embedded `manual`; tests build their own. */
export class Manual {
  private readonly byLang = new Map<string, Map<string, Chapter>>();

  constructor(chapters: Chapter[]) {
    for (const chapter of chapters) {
      if (!this.byLang.has(chapter.lang))
        this.byLang.set(chapter.lang, new Map());
      this.byLang.get(chapter.lang)!.set(chapter.slug, chapter);
    }
  }

  /** The chapter in `lang`, else in English, else undefined. */
  chapter(lang: string, slug: string): Chapter | undefined {
    return (
      this.byLang.get(lang)?.get(slug) ??
      this.byLang.get(FALLBACK_LANG)?.get(slug)
    );
  }

  /** Every chapter known in any language, each in `lang` where available, ordered for navigation. */
  chapters(lang: string): Chapter[] {
    const slugs = new Set<string>();
    for (const chapters of this.byLang.values())
      for (const slug of chapters.keys()) slugs.add(slug);
    return [...slugs]
      .map((slug) => this.chapter(lang, slug))
      .filter((c): c is Chapter => c !== undefined)
      .sort(
        (a, b) =>
          Number(b.slug === START_CHAPTER) - Number(a.slug === START_CHAPTER) ||
          a.order - b.order ||
          a.title.localeCompare(b.title, lang),
      );
  }

  /**
   * Chapters containing every word of `query` (case- and accent-insensitive), title matches first.
   * An empty query finds nothing.
   */
  search(lang: string, query: string): SearchHit[] {
    const terms = fold(query).split(/\s+/).filter(Boolean);
    if (terms.length === 0) return [];
    const hits: { hit: SearchHit; inTitle: boolean; count: number }[] = [];
    for (const chapter of this.chapters(lang)) {
      const haystack = fold(chapter.text);
      const title = fold(chapter.title);
      if (!terms.every((t) => haystack.includes(t) || title.includes(t)))
        continue;
      const first = terms[0]!;
      const index = haystack.indexOf(first);
      const count = terms.reduce((n, t) => n + haystack.split(t).length - 1, 0);
      hits.push({
        hit: {
          chapter,
          snippet:
            index >= 0 ? snippetAround(chapter.text, index, first.length) : "",
        },
        inTitle: terms.some((t) => title.includes(t)),
        count,
      });
    }
    return hits
      .sort(
        (a, b) => Number(b.inTitle) - Number(a.inTitle) || b.count - a.count,
      )
      .map((h) => h.hit);
  }
}

const sources = import.meta.glob<string>("../../../docs/manual/*/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});

/** Build a manual from `{ "<…>/<lang>/<slug>.md": source }`, the shape `import.meta.glob` returns. */
export function manualFromSources(files: Record<string, string>): Manual {
  const chapters: Chapter[] = [];
  for (const [file, source] of Object.entries(files)) {
    const match = /\/([^/]+)\/([^/]+)\.md$/.exec(file);
    if (match) chapters.push(parseChapter(match[2]!, match[1]!, source));
  }
  return new Manual(chapters);
}

/** The manual embedded in this build. */
export const manual = manualFromSources(sources);

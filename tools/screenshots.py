"""Regenerate the manual's screenshots from the running tool (T-2026-CU-9130-028, UC NF11).

    just build                                             # the SPA bundle the server serves
    uv run --group docs playwright install chromium        # once per machine
    uv run --group docs python tools/screenshots.py        # both languages, every screen

Per language the script starts z-finance on a throwaway data directory and a free port, takes the
first-run wizard while the tool is still empty, fills it with the demo story of ``demo_data.py``
through the public API and then photographs every screen with Chromium: 1440 px wide, light theme,
reduced motion. Every image carries a badge "EXAMPLE DATA" / "BEISPIELDATEN"; nothing on it is a
real figure, name or receipt (UC NF7). The images land in ``docs/assets/screenshots/<lang>/<name>.png``
and are what the chapters in ``docs/manual/<lang>/`` embed.

Options: ``--lang en|de`` (default both), ``--only entry,due`` (names, comma separated),
``--out DIR``, ``--height N`` (override the viewport height, default per screen).
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_data
import live_app
from live_app import DEFAULT_HEIGHT, ROOT, WIDTH

DEFAULT_OUT = ROOT / "docs" / "assets" / "screenshots"

# The four steps of the first-run wizard, photographed on the empty tool before the demo data goes in.
WIZARD_SHOTS = ("first-run", "first-run-account", "first-run-categories", "first-run-levy")

Prepare = Callable[[Any, "Context"], None]


@dataclass(frozen=True)
class Context:
    """What a screen's preparation step may need."""

    lang: str
    today: date
    csv: str


@dataclass(frozen=True)
class Shot:
    """One screenshot: the file name, the route it starts from and how to reach the wanted state."""

    name: str
    path: str
    height: int = DEFAULT_HEIGHT
    prepare: Prepare | None = None


# --- states that need more than opening the route --------------------------------------------------


def _t(ctx: Context, en: str, de: str) -> str:
    """The label of a control in the language of the run (the UI strings of frontend/src/locales/de.po)."""
    return en if ctx.lang == "en" else de


def _fill_entry(page: Any, ctx: Context) -> None:
    """Quick entry with an amount and a text typed, as a user is in the middle of a booking."""
    page.locator("#entry-amount").fill("12,50")
    page.locator("#entry-text").fill(demo_data.name(ctx.lang, "supermarket")[:6])


def _import_preview(page: Any, ctx: Context) -> None:
    """The import assistant on its third step: the demo statement, classified line by line."""
    page.get_by_label(_t(ctx, "Account", "Konto"), exact=True).select_option(label=demo_data.name(ctx.lang, "checking"))
    page.locator("input[type=file]").set_input_files(
        files=[{"name": "example-statement.csv", "mimeType": "text/csv", "buffer": ctx.csv.encode("utf-8")}]
    )
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name=_t(ctx, "Continue to the profile", "Weiter zum Profil")).click()
    page.wait_for_load_state("networkidle")
    page.get_by_label(_t(ctx, "Profile", "Profil"), exact=True).select_option(label=demo_data.name(ctx.lang, "bank"))
    page.get_by_role("button", name=_t(ctx, "Show the preview", "Vorschau anzeigen")).click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


def _manual_open(page: Any, ctx: Context) -> None:
    """The manual page with the chapter list, on the weekly routine chapter."""
    page.get_by_role("button", name=_t(ctx, "The weekly routine", "Das Wochenritual")).first.click()


def _screens() -> list[Shot]:
    """The screens, in the order of the sidebar; the names are the chapter slugs."""
    return [
        Shot("index", "/", height=1080),
        Shot("entry", "/entry", prepare=_fill_entry),
        Shot("due", "/due"),
        Shot("import", "/import", height=1100, prepare=_import_preview),
        Shot("receipts", "/receipts"),
        Shot("recurring", "/recurring", height=1000),
        Shot("pots", "/pots", height=1200),
        Shot("debts", "/debts", height=1000),
        Shot("payoff", "/payoff", height=1100),
        Shot("allocation", "/allocation", height=1000),
        Shot("tax", "/tax"),
        Shot("levy", "/levy", height=1400),
        Shot("forecast", "/forecast", height=1100),
        Shot("month", "/month", height=1200),
        Shot("accounts", "/accounts", height=1000),
        Shot("categories", "/categories", height=1260),
        Shot("settings", "/settings"),
        Shot("backup", "/backup"),
        Shot("manual", "/manual", height=1000, prepare=_manual_open),
    ]


# --- badge, files and navigation (server and browser live in tools/live_app.py) --------------------------


def _badge(page: Any, text: str) -> None:
    """Draw the example-data badge into the page (bottom left, above everything else)."""
    page.evaluate(
        """(text) => {
            const b = document.createElement('div');
            b.textContent = text;
            b.setAttribute('data-example-badge', '');
            b.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:99999;padding:6px 14px;' +
              'border-radius:999px;background:#1f1b16;color:#fff;font:600 12px/1.2 Montserrat,sans-serif;' +
              'letter-spacing:.08em;box-shadow:0 2px 8px rgba(0,0,0,.25)';
            document.body.appendChild(b);
        }""",
        text,
    )


def _save(png: bytes, target: Path) -> None:
    """Write ``png`` reduced to a 256-colour palette (a flat UI loses nothing visible, ~4x smaller)."""
    from PIL import Image

    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
        target, format="PNG", optimize=True
    )


def _open(page: Any, base: str, path: str) -> None:
    page.goto(f"{base}{path}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)  # charts and fonts settle


def _wizard_shots(page: Any, base: str, ctx: Context, out: Path) -> None:
    """The four steps of the first-run wizard, on the empty tool (nothing is saved)."""
    _open(page, base, "/")  # the shell sends a first start to the wizard
    page.wait_for_url("**/first-run")
    page.wait_for_timeout(300)
    advances = (None, _wizard_account, _wizard_next, _wizard_next)
    for name, advance in zip(WIZARD_SHOTS, advances, strict=True):
        if advance:
            advance(page, ctx)
        _badge(page, demo_data.EXAMPLE_MARKER[ctx.lang])
        _save(page.screenshot(), out / f"{name}.png")
        page.evaluate("document.querySelector('[data-example-badge]')?.remove()")


def _wizard_next(page: Any, ctx: Context) -> None:
    page.get_by_role("button", name=_t(ctx, "Continue", "Weiter")).click()
    page.wait_for_timeout(300)


def _wizard_account(page: Any, ctx: Context) -> None:
    _wizard_next(page, ctx)
    page.locator("#first-run-name").fill(demo_data.name(ctx.lang, "checking"))
    page.locator("#first-run-balance").fill("3200,00")


def run(lang: str, out_dir: Path, only: set[str] | None, height: int | None) -> None:
    """Photograph every screen in one language."""
    from playwright.sync_api import sync_playwright

    today = date.today()
    out = out_dir / lang
    with tempfile.TemporaryDirectory(prefix="zf-demo-") as tmp, live_app.server(Path(tmp)) as base:
        ctx = Context(lang=lang, today=today, csv=demo_data.demo_statement_csv(today, lang))
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = live_app.browser_context(browser, lang=lang, height=height or DEFAULT_HEIGHT)
                page = context.new_page()
                with httpx.Client(base_url=base, timeout=60) as client:
                    client.put("/api/i18n/language", json={"language": lang}).raise_for_status()
                    if only is None or any(name.startswith("first-run") for name in only):
                        _wizard_shots(page, base, ctx, out)
                    demo_data.seed(client, lang=lang, today=today, db_path=Path(tmp) / "z-finance.sqlite3")
                for shot in _screens():
                    if only is not None and shot.name not in only:
                        continue
                    page.set_viewport_size({"width": WIDTH, "height": height or shot.height})
                    _open(page, base, shot.path)
                    if shot.prepare:
                        shot.prepare(page, ctx)
                        page.wait_for_timeout(300)
                    _badge(page, demo_data.EXAMPLE_MARKER[lang])
                    _save(page.screenshot(), out / f"{shot.name}.png")
                    print(f"{lang}/{shot.name}.png")
            finally:
                browser.close()


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lang", choices=[*demo_data.DEMO_LANGUAGES, "both"], default="both")
    parser.add_argument("--only", help="comma separated screenshot names")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--height", type=int)
    args = parser.parse_args(argv)
    missing = live_app.bundle_missing()
    if missing:
        print(missing, file=sys.stderr)
        return 2
    only = set(args.only.split(",")) if args.only else None
    for lang in demo_data.DEMO_LANGUAGES if args.lang == "both" else (args.lang,):
        run(lang, args.out, only, args.height)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

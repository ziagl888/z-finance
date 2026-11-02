import { Trans } from "@lingui/react/macro";
import { Link } from "react-router";

/** Start page shown until a feature (the dashboard) claims the "" path. */
export function StartPage() {
  return (
    <section>
      <h1>z-finance</h1>
      <p>
        <Trans>
          Your local finance and liquidity overview. Features appear in the
          navigation as they are added.
        </Trans>
      </p>
    </section>
  );
}

/** Fallback for unknown client-side routes. */
export function NotFound() {
  return (
    <section>
      <h1>
        <Trans>Page not found</Trans>
      </h1>
      <p>
        <Link to="/">
          <Trans>Back to the start page</Trans>
        </Link>
      </p>
    </section>
  );
}

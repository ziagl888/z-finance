import { defineConfig } from "@lingui/cli";
import { formatter } from "@lingui/format-po";

// English is the source language: the strings in the code are the English UI, so there is no
// en.po (a pre-commit hook forbids one). `lingui extract-template` writes src/locales/messages.pot,
// `lingui extract --locale de` updates src/locales/de.po. See ARCHITECTURE.md "Internationalisation".
export default defineConfig({
  sourceLocale: "en",
  locales: ["en", "de"],
  catalogs: [
    {
      path: "<rootDir>/src/locales/{locale}",
      include: ["<rootDir>/src"],
    },
  ],
  // No line numbers: a moved string must not rewrite the catalogs.
  format: formatter({ lineNumbers: false }),
});

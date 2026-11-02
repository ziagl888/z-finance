// ESLint flat config for the frontend, run by the `eslint` pre-commit hook.
//
// CommonJS on purpose: the hook uses `language: node`, so eslint and its plugins live in
// pre-commit's own environment, found through the NODE_PATH that pre-commit sets. `require()`
// honours NODE_PATH, an ESM `import` does not, so this file works in a fresh clone without
// `npm ci`. Plugin versions are pinned in .pre-commit-config.yaml.
const js = require("@eslint/js");
const tseslint = require("typescript-eslint");
const reactHooks = require("eslint-plugin-react-hooks");
const globals = require("globals");

module.exports = tseslint.config(
  { ignores: ["**/dist/**", "**/node_modules/**"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
);

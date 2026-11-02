// `.po` imports are compiled to a message catalog by @lingui/vite-plugin.
declare module "*.po" {
  import type { Messages } from "@lingui/core";

  export const messages: Messages;
}

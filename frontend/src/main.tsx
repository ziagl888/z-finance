import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";

import { router } from "./app/router";
import { LocaleProvider } from "./i18n";
import "./styles/index.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error('index.html is missing <div id="root">');
}

createRoot(root).render(
  <StrictMode>
    <LocaleProvider>
      <RouterProvider router={router} />
    </LocaleProvider>
  </StrictMode>,
);

import { createBrowserRouter, type RouteObject } from "react-router";

import { features } from "./features";
import { Layout } from "./Layout";
import { NotFound, StartPage } from "./pages";

const featureRoutes: RouteObject[] = features.map((f) =>
  f.path === ""
    ? { index: true, Component: f.Component }
    : { path: f.path, Component: f.Component },
);

// The built-in start page only applies while no feature claims the index route.
const hasIndex = features.some((f) => f.path === "");

export const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    children: [
      ...(hasIndex ? [] : [{ index: true, Component: StartPage }]),
      ...featureRoutes,
      { path: "*", Component: NotFound },
    ],
  },
]);

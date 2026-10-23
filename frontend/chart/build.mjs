// Bundles the component into src/momentum/ui/chart/ (the asset_dir declared in
// src/momentum/pyproject.toml). The output is committed; CI rebuilds it and
// fails if it differs, so the committed bundle always matches this source.
import { build } from "esbuild";
import { copyFileSync } from "node:fs";

const out = "../../src/momentum/ui/chart";

await build({
  entryPoints: ["src/index.ts"],
  bundle: true,
  format: "esm",
  target: ["es2022"],
  minify: true,
  legalComments: "inline", // keeps the Lightweight Charts licence banner in the bundle
  outfile: `${out}/momentum-chart.js`,
  logLevel: "info",
});
copyFileSync("src/chart.css", `${out}/momentum-chart.css`);

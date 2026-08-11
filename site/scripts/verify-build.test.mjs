import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { verifyBuild } from "./verify-build.mjs";

function makeBundle(snapshot = {}) {
  const root = mkdtempSync(join(tmpdir(), "site-contract-"));
  const bundle = join(root, "bundle");
  const dist = join(root, "dist");
  const fullSnapshot = {
    graph_max_content_nodes: 80,
    graph_recent_days: 90,
    minimum_final_score: 0.5,
    target_item_bytes: 16384,
    max_item_bytes: 32768,
    max_blog_excerpt_chars: 4000,
    warn_repository_data_mb: 500,
    warn_pages_artifact_mb: 500,
    fail_pages_artifact_mb: 900,
    ...snapshot,
  };
  mkdirSync(join(bundle, "pending-data/runs/2026/08"), { recursive: true });
  mkdirSync(join(dist, "search"), { recursive: true });
  mkdirSync(join(dist, "graph"), { recursive: true });
  mkdirSync(join(dist, "pagefind"), { recursive: true });
  writeFileSync(join(bundle, "pending-data/runs/2026/08/run.json"), JSON.stringify({ config_snapshot: fullSnapshot, stage_report: {} }));
  writeFileSync(join(dist, "graph.json"), JSON.stringify({ nodes: [{ data: { type: "paper" } }, { data: { type: "blog" } }], edges: [] }));
  for (const file of ["index.html", "search/index.html", "graph/index.html", "pagefind/pagefind.js", "pagefind/filter.json"]) writeFileSync(join(dist, file), "x");
  return { bundle, dist };
}

test("verifyBuild uses report graph and artifact thresholds", () => {
  const root = makeBundle({ graph_max_content_nodes: 1, fail_pages_artifact_mb: 1 });
  assert.throws(() => verifyBuild(root), /graph content node limit/);
});

test("verifyBuild accepts a bundle within configured limits", () => {
  const root = makeBundle({ graph_max_content_nodes: 2 });
  assert.equal(verifyBuild(root).snapshot.graph_max_content_nodes, 2);
});

test("site image pins pnpm and approves only required dependency builds", () => {
  const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const workspace = readFileSync(new URL("../pnpm-workspace.yaml", import.meta.url), "utf8");
  const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");

  assert.equal(packageJson.packageManager, "pnpm@11.21.0");
  const workspaceLines = workspace.split(/\r?\n/);
  for (const dependency of ["'@tailwindcss/oxide'", "esbuild", "sharp"]) {
    assert.ok(workspaceLines.includes(`  ${dependency}: true`));
  }
  const configCopy = dockerfile.indexOf("COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./");
  assert.ok(configCopy >= 0);
  assert.ok(configCopy < dockerfile.indexOf("RUN pnpm install --frozen-lockfile"));
});

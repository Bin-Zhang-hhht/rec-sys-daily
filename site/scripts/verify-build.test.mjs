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
    minimum_metadata_relevance_score: 0.65,
    target_item_bytes: 16384,
    max_item_bytes: 32768,
    max_blog_excerpt_chars: 4000,
    warn_repository_data_mb: 500,
    warn_pages_artifact_mb: 500,
    fail_pages_artifact_mb: 900,
    ...snapshot,
  };
  mkdirSync(join(bundle, "pending-data/runs/2026/08"), { recursive: true });
  mkdirSync(join(bundle, "pending-data/digests/2026/08"), { recursive: true });
  mkdirSync(join(dist, "search"), { recursive: true });
  mkdirSync(join(dist, "graph"), { recursive: true });
  mkdirSync(join(dist, "archive/2026-08-13"), { recursive: true });
  mkdirSync(join(dist, "papers/paper-1"), { recursive: true });
  mkdirSync(join(dist, "articles/article-1"), { recursive: true });
  mkdirSync(join(dist, "pagefind"), { recursive: true });
  writeFileSync(join(bundle, "pending-data/runs/2026/08/run.json"), JSON.stringify({ config_snapshot: fullSnapshot, stage_report: {} }));
  writeFileSync(join(bundle, "pending-data/digests/2026/08/2026-08-13.json"), JSON.stringify({ date: "2026-08-13", papers: [], blogs: [] }));
  writeFileSync(join(dist, "graph.json"), JSON.stringify({
    nodes: [
      { data: { id: "paper-1", type: "paper", href: "/rec-sys-daily/papers/paper-1/", weight: 2 } },
      { data: { id: "article-1", type: "article", href: "/rec-sys-daily/articles/article-1/", weight: 2 } },
      { data: { id: "method:ranking", type: "method", weight: 2 } },
    ],
    edges: [
      { data: { id: "edge-1", source: "paper-1", target: "method:ranking" } },
      { data: { id: "edge-2", source: "article-1", target: "method:ranking" } },
    ],
  }));
  for (const file of ["index.html", "archive/index.html", "search/index.html", "graph/index.html", "archive/2026-08-13/index.html", "pagefind/pagefind.js", "pagefind/filter.json"]) writeFileSync(join(dist, file), "x");
  const pagefindMetadata = '<span data-pagefind-ignore data-pagefind-meta="kind">论文</span><span data-pagefind-ignore data-pagefind-meta="published_at">2026-08-13</span><span data-pagefind-ignore data-pagefind-meta="taxonomy">[]</span><span data-pagefind-ignore data-pagefind-meta="summary_zh">中文总结</span>';
  writeFileSync(join(dist, "papers/paper-1/index.html"), pagefindMetadata);
  writeFileSync(join(dist, "articles/article-1/index.html"), pagefindMetadata);
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

test("verifyBuild rejects links that escape the project Pages base", () => {
  const graphRoot = makeBundle({ graph_max_content_nodes: 2 });
  const graph = JSON.parse(readFileSync(join(graphRoot.dist, "graph.json"), "utf8"));
  graph.nodes[0].data.href = "/papers/paper-1/";
  writeFileSync(join(graphRoot.dist, "graph.json"), JSON.stringify(graph));
  assert.throws(() => verifyBuild(graphRoot), /invalid detail href/);

  const htmlRoot = makeBundle({ graph_max_content_nodes: 2 });
  writeFileSync(join(htmlRoot.dist, "index.html"), '<a href="/search/">search</a>');
  assert.throws(() => verifyBuild(htmlRoot), /escapes project base/);
});

test("verifyBuild rejects graph nodes without a finite positive weight", () => {
  const root = makeBundle({ graph_max_content_nodes: 2 });
  const graph = JSON.parse(readFileSync(join(root.dist, "graph.json"), "utf8"));
  graph.nodes[0].data.weight = 0;
  writeFileSync(join(root.dist, "graph.json"), JSON.stringify(graph));
  assert.throws(() => verifyBuild(root), /invalid positive weight/);
});

test("verifyBuild requires search card metadata on detail pages", () => {
  const root = makeBundle({ graph_max_content_nodes: 2 });
  writeFileSync(join(root.dist, "articles/article-1/index.html"), '<span data-pagefind-ignore data-pagefind-meta="kind">工程博客</span>');
  assert.throws(() => verifyBuild(root), /missing Pagefind metadata published_at/);
});

test("verifyBuild excludes Pagefind metadata from indexed body text", () => {
  const root = makeBundle({ graph_max_content_nodes: 2 });
  const article = join(root.dist, "articles/article-1/index.html");
  writeFileSync(article, readFileSync(article, "utf8").replace("data-pagefind-ignore ", ""));
  assert.throws(() => verifyBuild(root), /metadata must be excluded from body text kind/);
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

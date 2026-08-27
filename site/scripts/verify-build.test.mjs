import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { verifyBuild } from "./verify-build.mjs";

const base = "/rec-sys-daily/";

function writeJson(file, value) {
  writeFileSync(file, JSON.stringify(value));
}

function makeBundle(snapshot = {}) {
  const root = mkdtempSync(join(tmpdir(), "site-contract-"));
  const bundle = join(root, "bundle");
  const dist = join(root, "dist");
  const fullSnapshot = {
    graph_initial_content_nodes: 48,
    graph_shard_target_bytes: 98_304,
    minimum_final_score: 0.5,
    minimum_metadata_relevance_score: 0.65,
    target_item_bytes: 16_384,
    max_item_bytes: 32_768,
    max_blog_excerpt_chars: 4_000,
    warn_repository_data_mb: 500,
    warn_pages_artifact_mb: 500,
    fail_pages_artifact_mb: 900,
    ...snapshot,
  };
  for (const directory of [
    "pending-data/runs/2026/08", "pending-data/digests/2026/08",
  ]) mkdirSync(join(bundle, directory), { recursive: true });
  for (const directory of [
    "search", "graph", "archive/2026-08-13", "papers/paper-1", "articles/blog-1", "pagefind",
    "graph-shards/d0", "graph-shards/d1", "graph-shards/nodes", "graph-shards/adjacency",
  ]) mkdirSync(join(dist, directory), { recursive: true });

  writeJson(join(bundle, "manifest.json"), { run_id: "run-1", schema_version: "1" });
  writeJson(join(bundle, "pending-data/runs/2026/08/run-1.json"), { run_id: "run-1", config_snapshot: fullSnapshot, stage_report: {} });
  writeJson(join(bundle, "pending-data/digests/2026/08/2026-08-13.json"), {
    date: "2026-08-13",
    papers: [{ item_id: "paper-1", recommendation_reason_zh: "相关", rank: 1 }],
    blogs: [],
  });

  const paper = { data: { id: "paper-1", label: "Paper", type: "paper", href: `${base}papers/paper-1/`, summary: "摘要", published_at: "2026-08-13T00:00:00Z", tags: ["ranking"], search_terms: ["paper-1", "Paper", "ranking"], weight: 2 } };
  const blog = { data: { id: "blog-1", label: "Blog", type: "blog", href: `${base}articles/blog-1/`, summary: "摘要", published_at: "2026-08-12T00:00:00Z", tags: ["ranking"], search_terms: ["blog-1", "Blog", "ranking"], weight: 2 } };
  const taxonomy = { data: { id: "task:ranking", label: "排序 / Ranking", type: "task", search_terms: ["ranking", "排序", "Ranking"], weight: 2 } };
  const paperTaxonomy = { data: { id: "taxonomy:paper-1|task:ranking", source: "paper-1", target: "task:ranking", type: "taxonomy", confidence: 1, evidence: "canonical taxonomy label", generated_by: "topics.yaml" } };
  const blogTaxonomy = { data: { id: "taxonomy:blog-1|task:ranking", source: "blog-1", target: "task:ranking", type: "taxonomy", confidence: 1, evidence: "canonical taxonomy label", generated_by: "topics.yaml" } };
  const similarity = { source_id: "blog-1", target_id: "paper-1", score: 0.9, source_rank: 1, target_rank: 1 };
  const similarityGraph = { data: { id: "similarity:blog-1|paper-1", source: "blog-1", target: "paper-1", type: "similarity", confidence: 0.9, evidence: "FastEmbed cosine similarity", generated_by: "fastembed", score: 0.9, source_rank: 1, target_rank: 1 } };

  const d0 = { id: "d0-shard", content_ids: ["paper-1"], document: { nodes: [paper, taxonomy], edges: [paperTaxonomy] } };
  const d1 = { id: "d1-shard", content_ids: ["blog-1"], document: { nodes: [blog, taxonomy], edges: [blogTaxonomy, similarityGraph] } };
  const nodes = { id: "node-shard", content_ids: ["blog-1", "paper-1"], document: { nodes: [paper, blog, taxonomy], edges: [paperTaxonomy, blogTaxonomy] } };
  const adjacency = { id: "adj-shard", entries: [{ id: "blog-1", neighbors: [similarity] }, { id: "paper-1", neighbors: [similarity] }] };
  const urls = {
    d0: `${base}graph-shards/d0/d0-shard.json`,
    d1: `${base}graph-shards/d1/d1-shard.json`,
    nodes: `${base}graph-shards/nodes/node-shard.json`,
    adjacency: `${base}graph-shards/adjacency/adj-shard.json`,
  };
  writeJson(join(dist, "graph-shards/d0/d0-shard.json"), d0);
  writeJson(join(dist, "graph-shards/d1/d1-shard.json"), d1);
  writeJson(join(dist, "graph-shards/nodes/node-shard.json"), nodes);
  writeJson(join(dist, "graph-shards/adjacency/adj-shard.json"), adjacency);
  writeJson(join(dist, "graph-index.json"), { schema_version: "1", nodes: [
    { id: "blog-1", type: "blog", label: "Blog", href: `${base}articles/blog-1/`, summary: "摘要", published_at: "2026-08-12T00:00:00Z", tags: ["ranking"], search_terms: ["blog-1", "Blog", "ranking"], node_shard: "node-shard", adjacency_shard: "adj-shard" },
    { id: "paper-1", type: "paper", label: "Paper", href: `${base}papers/paper-1/`, summary: "摘要", published_at: "2026-08-13T00:00:00Z", tags: ["ranking"], search_terms: ["paper-1", "Paper", "ranking"], node_shard: "node-shard", adjacency_shard: "adj-shard" },
  ] });
  writeJson(join(dist, "graph-manifest.json"), {
    schema_version: "1", run_id: "run-1", index_url: `${base}graph-index.json`,
    initial: { d0_urls: [urls.d0], d1_urls: [urls.d1], d0_content_ids: ["paper-1"], d1_content_ids: ["blog-1"], max_content_nodes: fullSnapshot.graph_initial_content_nodes },
    node_shards: { "node-shard": urls.nodes }, adjacency_shards: { "adj-shard": urls.adjacency }, total_content_nodes: 2, total_similarity_edges: 1,
  });

  for (const file of ["index.html", "archive/index.html", "search/index.html", "graph/index.html", "archive/2026-08-13/index.html", "pagefind/pagefind.js", "pagefind/filter.json"]) writeFileSync(join(dist, file), "x");
  const pagefindMetadata = '<span data-pagefind-ignore data-pagefind-meta="kind">论文</span><span data-pagefind-ignore data-pagefind-meta="published_at">2026-08-13</span><span data-pagefind-ignore data-pagefind-meta="taxonomy">[]</span><span data-pagefind-ignore data-pagefind-meta="summary_zh">中文总结</span>';
  writeFileSync(join(dist, "papers/paper-1/index.html"), pagefindMetadata);
  writeFileSync(join(dist, "articles/blog-1/index.html"), pagefindMetadata);
  return { bundle, dist };
}

test("verifyBuild accepts manifest, index, d0/d1, node, and adjacency shards", () => {
  const root = makeBundle();
  assert.equal(verifyBuild(root).snapshot.graph_initial_content_nodes, 48);
});

test("verifyBuild selects the RunReport named by the bundle manifest", () => {
  const root = makeBundle();
  const currentReport = JSON.parse(readFileSync(join(root.bundle, "pending-data/runs/2026/08/run-1.json"), "utf8"));
  writeJson(join(root.bundle, "pending-data/runs/2026/08/zzz-historical.json"), {
    run_id: "zzz-historical",
    config_snapshot: { ...currentReport.config_snapshot, graph_initial_content_nodes: 99 },
    stage_report: {},
  });
  assert.equal(verifyBuild(root).snapshot.graph_initial_content_nodes, 48);
});

test("verifyBuild rejects old or out-of-range graph snapshot contracts", () => {
  assert.throws(() => verifyBuild(makeBundle({ graph_initial_content_nodes: undefined })), /config_snapshot/);
  assert.throws(() => verifyBuild(makeBundle({ graph_shard_target_bytes: 65_535 })), /graph_shard_target_bytes/);
  assert.throws(() => verifyBuild(makeBundle({ graph_shard_target_bytes: 131_073 })), /graph_shard_target_bytes/);
});

test("verifyBuild rejects graph links that escape the Project Pages base", () => {
  const root = makeBundle();
  const indexFile = join(root.dist, "graph-index.json");
  const index = JSON.parse(readFileSync(indexFile, "utf8"));
  index.nodes[0].href = "/articles/blog-1/";
  writeJson(indexFile, index);
  assert.throws(() => verifyBuild(root), /invalid detail href/);

  const htmlRoot = makeBundle();
  writeFileSync(join(htmlRoot.dist, "index.html"), '<a href="/search/">search</a>');
  assert.throws(() => verifyBuild(htmlRoot), /escapes project base/);
});

test("verifyBuild rejects invalid node shards and copied similarity edges", () => {
  const weightRoot = makeBundle();
  const weightFile = join(weightRoot.dist, "graph-shards/nodes/node-shard.json");
  const weightShard = JSON.parse(readFileSync(weightFile, "utf8"));
  weightShard.document.nodes[0].data.weight = 0;
  writeJson(weightFile, weightShard);
  assert.throws(() => verifyBuild(weightRoot), /invalid positive weight/);

  const edgeRoot = makeBundle();
  const edgeFile = join(edgeRoot.dist, "graph-shards/nodes/node-shard.json");
  const edgeShard = JSON.parse(readFileSync(edgeFile, "utf8"));
  edgeShard.document.edges.push({ data: { id: "similarity:blog-1|paper-1", source: "blog-1", target: "paper-1", type: "similarity" } });
  writeJson(edgeFile, edgeShard);
  assert.throws(() => verifyBuild(edgeRoot), /copies similarity edges/);
});

test("verifyBuild rejects asymmetric adjacency payloads", () => {
  const root = makeBundle();
  const file = join(root.dist, "graph-shards/adjacency/adj-shard.json");
  const shard = JSON.parse(readFileSync(file, "utf8"));
  shard.entries[1].neighbors[0].target_rank = 2;
  writeJson(file, shard);
  assert.throws(() => verifyBuild(root), /not duplicated symmetrically/);
});

test("verifyBuild rejects shards larger than the configured raw target", () => {
  const root = makeBundle({ graph_shard_target_bytes: 65_536 });
  const file = join(root.dist, "graph-shards/nodes/node-shard.json");
  const shard = JSON.parse(readFileSync(file, "utf8"));
  shard.padding = "x".repeat(70_000);
  writeJson(file, shard);
  assert.throws(() => verifyBuild(root), /exceeds configured target/);
});

test("verifyBuild requires search card metadata on detail pages", () => {
  const root = makeBundle();
  writeFileSync(join(root.dist, "articles/blog-1/index.html"), '<span data-pagefind-ignore data-pagefind-meta="kind">工程博客</span>');
  assert.throws(() => verifyBuild(root), /missing Pagefind metadata published_at/);
});

test("verifyBuild excludes Pagefind metadata from indexed body text", () => {
  const root = makeBundle();
  const article = join(root.dist, "articles/blog-1/index.html");
  writeFileSync(article, readFileSync(article, "utf8").replace("data-pagefind-ignore ", ""));
  assert.throws(() => verifyBuild(root), /metadata must be excluded from body text kind/);
});

test("site image pins pnpm and approves only required dependency builds", () => {
  const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  const workspace = readFileSync(new URL("../pnpm-workspace.yaml", import.meta.url), "utf8");
  const dockerfile = readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");

  assert.equal(packageJson.packageManager, "pnpm@11.21.0");
  const workspaceLines = workspace.split(/\r?\n/);
  for (const dependency of ["'@tailwindcss/oxide'", "esbuild", "sharp"]) assert.ok(workspaceLines.includes(`  ${dependency}: true`));
  const configCopy = dockerfile.indexOf("COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./");
  assert.ok(configCopy >= 0);
  assert.ok(configCopy < dockerfile.indexOf("RUN pnpm install --frozen-lockfile"));
});

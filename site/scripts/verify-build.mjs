import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const projectBase = "/rec-sys-daily/";
const required = [
  "index.html",
  "archive/index.html",
  "search/index.html",
  "graph/index.html",
  "graph-manifest.json",
  "graph-index.json",
  "pagefind/pagefind.js",
];

function filesUnder(root) {
  if (!fs.existsSync(root)) return [];
  const files = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(file); else files.push(file);
    }
  };
  walk(root);
  return files;
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { throw new Error(`cannot read JSON ${file}: ${error instanceof Error ? error.message : "unknown error"}`); }
}

function readSnapshot(bundle) {
  const runs = filesUnder(path.join(bundle, "pending-data", "runs")).filter(file => file.endsWith(".json")).sort();
  if (!runs.length) throw new Error("publish bundle has no RunReport");
  const manifest = readJson(path.join(bundle, "manifest.json"));
  if (manifest?.schema_version !== "1" || typeof manifest.run_id !== "string" || !manifest.run_id) {
    throw new Error("invalid publish bundle manifest");
  }
  const matchingRuns = runs.filter(file => path.basename(file, ".json") === manifest.run_id);
  if (matchingRuns.length !== 1) throw new Error("publish bundle must contain exactly one RunReport for its manifest run_id");
  const report = readJson(matchingRuns[0]);
  const snapshot = report.config_snapshot;
  const fields = [
    "graph_initial_content_nodes", "minimum_final_score", "minimum_metadata_relevance_score", "target_item_bytes",
    "max_item_bytes", "max_blog_excerpt_chars", "warn_repository_data_mb", "warn_pages_artifact_mb", "fail_pages_artifact_mb",
  ];
  if (!snapshot || fields.some(field => typeof snapshot[field] !== "number" || !Number.isFinite(snapshot[field]) || snapshot[field] <= 0)) {
    throw new Error("invalid RunReport config_snapshot");
  }
  if (!Number.isInteger(snapshot.graph_initial_content_nodes)) throw new Error("invalid RunReport graph_initial_content_nodes");
  if (!Number.isInteger(snapshot.graph_shard_target_bytes) || snapshot.graph_shard_target_bytes < 65_536 || snapshot.graph_shard_target_bytes > 131_072) {
    throw new Error("invalid RunReport graph_shard_target_bytes");
  }
  if (report.run_id !== manifest.run_id) throw new Error("RunReport does not match publish bundle manifest");
  return { report, snapshot };
}

function latestDigest(bundle) {
  const files = filesUnder(path.join(bundle, "pending-data", "digests")).filter(file => file.endsWith(".json")).sort();
  return files.length ? readJson(files.at(-1)) : { papers: [], blogs: [] };
}

function bytesUnder(root) {
  return filesUnder(root).reduce((total, file) => total + fs.statSync(file).size, 0);
}

function assertNoRawContent(root) {
  const forbidden = /\.(pdf|html?|txt|md|zip)$/i;
  for (const file of filesUnder(root)) {
    if (forbidden.test(file)) throw new Error(`raw source content leaked into artifact: ${path.relative(root, file)}`);
  }
}

function sameSet(left, right) {
  return left.size === right.size && [...left].every(value => right.has(value));
}

function assetFile(dist, value) {
  if (typeof value !== "string") throw new Error("graph asset URL must be a string");
  let url;
  try { url = new URL(value, "https://example.invalid"); }
  catch { throw new Error(`invalid graph asset URL: ${String(value)}`); }
  if (url.origin !== "https://example.invalid" || url.search || url.hash || !url.pathname.startsWith(projectBase)) {
    throw new Error(`graph asset URL escapes project base: ${value}`);
  }
  let relative;
  try { relative = decodeURIComponent(url.pathname.slice(projectBase.length)); }
  catch { throw new Error(`graph asset URL has invalid encoding: ${value}`); }
  if (!relative || relative.includes("\\") || relative.split("/").includes("..")) throw new Error(`invalid graph asset path: ${value}`);
  const root = path.resolve(dist);
  const file = path.resolve(root, ...relative.split("/"));
  if (file !== root && !file.startsWith(`${root}${path.sep}`)) throw new Error(`graph asset path escapes dist: ${value}`);
  if (!fs.existsSync(file)) throw new Error(`missing graph asset: ${relative}`);
  return file;
}

function assertGraphNavigation(graph) {
  if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) throw new Error("invalid graph document");
  const contentTypes = new Set(["paper", "blog"]);
  const nodeIds = new Set();
  for (const node of graph.nodes) {
    if (!node?.data || typeof node.data.id !== "string" || nodeIds.has(node.data.id)) throw new Error("graph document contains an invalid or duplicate node");
    nodeIds.add(node.data.id);
    if (!Number.isFinite(node.data.weight) || node.data.weight <= 0) throw new Error(`graph node has invalid positive weight: ${node.data.id}`);
    if (contentTypes.has(node.data.type)) {
      const expected = node.data.type === "paper" ? "papers" : "articles";
      if (typeof node.data.href !== "string" || !new RegExp(`^${projectBase}${expected}/[A-Za-z0-9._~-]+/$`).test(node.data.href)) {
        throw new Error(`graph content node has invalid detail href: ${node.data.id}`);
      }
    }
  }
  const edgeIds = new Set();
  for (const edge of graph.edges) {
    if (!edge?.data || typeof edge.data.id !== "string" || edgeIds.has(edge.data.id)) throw new Error("graph document contains an invalid or duplicate edge");
    edgeIds.add(edge.data.id);
    if (!nodeIds.has(edge.data.source) || !nodeIds.has(edge.data.target)) throw new Error(`graph edge has an unloaded endpoint: ${edge.data.id}`);
    if (edge.data.type !== "taxonomy" && edge.data.type !== "similarity") throw new Error(`graph edge has invalid type: ${edge.data.id}`);
    if (edge.data.type === "similarity" && edge.data.source >= edge.data.target) throw new Error(`similarity edge is not in stable endpoint order: ${edge.data.id}`);
  }
  const contentIds = new Set(graph.nodes.filter(node => contentTypes.has(node.data.type)).map(node => node.data.id));
  for (const node of graph.nodes.filter(node => !contentTypes.has(node.data.type))) {
    const adjacent = graph.edges.some(edge =>
      (edge.data.source === node.data.id && contentIds.has(edge.data.target))
      || (edge.data.target === node.data.id && contentIds.has(edge.data.source))
    );
    if (!adjacent) throw new Error(`graph taxonomy node has no adjacent content: ${node.data.id}`);
  }
}

function mergeGraphDocuments(shards) {
  const nodes = new Map();
  const edges = new Map();
  for (const shard of shards) {
    for (const node of shard.document.nodes) nodes.set(node.data.id, node);
    for (const edge of shard.document.edges) {
      if (edges.has(edge.data.id)) throw new Error(`graph edge is duplicated across shards: ${edge.data.id}`);
      edges.set(edge.data.id, edge);
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

function readGraphShard(dist, url, expectedId, targetBytes) {
  const file = assetFile(dist, url);
  if (fs.statSync(file).size > targetBytes) throw new Error(`graph shard exceeds configured target: ${path.relative(dist, file)}`);
  const shard = readJson(file);
  if (shard?.id !== expectedId || !Array.isArray(shard.content_ids) || !shard.document) throw new Error(`invalid graph shard: ${expectedId}`);
  return shard;
}

function assertSearchMetadata(dist) {
  const detailPages = ["papers", "articles"]
    .flatMap(kind => filesUnder(path.join(dist, kind)))
    .filter(file => file.endsWith("index.html"));
  for (const file of detailPages) {
    const html = fs.readFileSync(file, "utf8");
    for (const name of ["kind", "published_at", "taxonomy", "summary_zh"]) {
      const tag = html.match(new RegExp(`<[^>]*data-pagefind-meta=["']${name}["'][^>]*>`))?.[0];
      if (!tag) throw new Error(`detail page missing Pagefind metadata ${name}: ${path.relative(dist, file)}`);
      if (!/\bdata-pagefind-ignore(?:\s|=|>)/.test(tag)) throw new Error(`Pagefind metadata must be excluded from body text ${name}: ${path.relative(dist, file)}`);
    }
  }
}

function verifyGraphAssets(dist, bundle, report, snapshot) {
  const manifest = readJson(path.join(dist, "graph-manifest.json"));
  if (manifest?.schema_version !== "1" || manifest.run_id !== report.run_id) throw new Error("graph manifest does not match RunReport");
  if (manifest.initial?.max_content_nodes !== snapshot.graph_initial_content_nodes) throw new Error("graph manifest initial limit does not match RunReport");
  if (!Array.isArray(manifest.initial?.d0_urls) || !Array.isArray(manifest.initial?.d1_urls)) throw new Error("graph manifest has invalid initial shard lists");
  if (!manifest.node_shards || typeof manifest.node_shards !== "object" || !manifest.adjacency_shards || typeof manifest.adjacency_shards !== "object") throw new Error("graph manifest has invalid shard maps");

  const index = readJson(assetFile(dist, manifest.index_url));
  if (index?.schema_version !== "1" || !Array.isArray(index.nodes)) throw new Error("invalid graph index");
  if (manifest.total_content_nodes !== index.nodes.length) throw new Error("graph manifest content count does not match index");
  const indexById = new Map();
  for (const node of index.nodes) {
    if (!node || typeof node.id !== "string" || indexById.has(node.id) || !["paper", "blog"].includes(node.type)) throw new Error("graph index contains an invalid or duplicate node");
    if (!manifest.node_shards[node.node_shard] || !manifest.adjacency_shards[node.adjacency_shard]) throw new Error(`graph index references an unknown shard: ${node.id}`);
    const expected = node.type === "paper" ? "papers" : "articles";
    if (typeof node.href !== "string" || !new RegExp(`^${projectBase}${expected}/[A-Za-z0-9._~-]+/$`).test(node.href)) throw new Error(`graph index has invalid detail href: ${node.id}`);
    indexById.set(node.id, node);
  }

  const nodeCoverage = new Set();
  for (const [id, url] of Object.entries(manifest.node_shards)) {
    const shard = readGraphShard(dist, url, id, snapshot.graph_shard_target_bytes);
    assertGraphNavigation(shard.document);
    if (shard.document.edges.some(edge => edge.data?.type === "similarity")) throw new Error(`node shard copies similarity edges: ${id}`);
    const contentIds = new Set(shard.document.nodes.filter(node => ["paper", "blog"].includes(node.data?.type)).map(node => node.data.id));
    if (!sameSet(contentIds, new Set(shard.content_ids))) throw new Error(`node shard content_ids do not match document: ${id}`);
    for (const itemId of shard.content_ids) {
      if (!indexById.has(itemId) || nodeCoverage.has(itemId)) throw new Error(`node shard coverage is invalid: ${itemId}`);
      nodeCoverage.add(itemId);
      if (indexById.get(itemId).node_shard !== id) throw new Error(`graph index node shard mapping is invalid: ${itemId}`);
    }
  }
  if (!sameSet(nodeCoverage, new Set(indexById.keys()))) throw new Error("node shards do not cover the graph index");

  const adjacencyById = new Map();
  const edgeOccurrences = new Map();
  for (const [id, url] of Object.entries(manifest.adjacency_shards)) {
    const file = assetFile(dist, url);
    if (fs.statSync(file).size > snapshot.graph_shard_target_bytes) throw new Error(`graph shard exceeds configured target: ${path.relative(dist, file)}`);
    const shard = readJson(file);
    if (shard?.id !== id || !Array.isArray(shard.entries)) throw new Error(`invalid adjacency shard: ${id}`);
    for (const entry of shard.entries) {
      if (!entry || typeof entry.id !== "string" || adjacencyById.has(entry.id) || !indexById.has(entry.id) || !Array.isArray(entry.neighbors)) throw new Error(`invalid adjacency entry in shard ${id}`);
      if (indexById.get(entry.id).adjacency_shard !== id) throw new Error(`graph index adjacency mapping is invalid: ${entry.id}`);
      adjacencyById.set(entry.id, entry.neighbors);
      for (const edge of entry.neighbors) {
        if (!edge || edge.source_id >= edge.target_id || !indexById.has(edge.source_id) || !indexById.has(edge.target_id) || (edge.source_id !== entry.id && edge.target_id !== entry.id)) throw new Error(`invalid adjacency edge for ${entry.id}`);
        const key = `${edge.source_id}|${edge.target_id}`;
        const values = edgeOccurrences.get(key) ?? [];
        values.push(JSON.stringify(edge));
        edgeOccurrences.set(key, values);
      }
    }
  }
  if (!sameSet(new Set(adjacencyById.keys()), new Set(indexById.keys()))) throw new Error("adjacency shards do not cover the graph index");
  if (edgeOccurrences.size !== manifest.total_similarity_edges) throw new Error("graph manifest similarity edge count is invalid");
  for (const [key, values] of edgeOccurrences) {
    if (values.length !== 2 || values[0] !== values[1]) throw new Error(`adjacency edge is not duplicated symmetrically: ${key}`);
  }

  const initialEntries = [
    ...manifest.initial.d0_urls.map(url => ["d0", url]),
    ...manifest.initial.d1_urls.map(url => ["d1", url]),
  ];
  const initialShards = initialEntries.map(([kind, url]) => {
    const expectedId = path.basename(new URL(url, "https://example.invalid").pathname, ".json");
    return { kind, shard: readGraphShard(dist, url, expectedId, snapshot.graph_shard_target_bytes) };
  });
  const d0Ids = new Set(initialShards.filter(value => value.kind === "d0").flatMap(value => value.shard.content_ids));
  const d1Ids = new Set(initialShards.filter(value => value.kind === "d1").flatMap(value => value.shard.content_ids));
  if (!sameSet(d0Ids, new Set(manifest.initial.d0_content_ids)) || !sameSet(d1Ids, new Set(manifest.initial.d1_content_ids))) throw new Error("initial graph shard contents do not match manifest");
  if ([...d0Ids].some(id => d1Ids.has(id))) throw new Error("d0 and d1 graph nodes overlap");
  const digest = latestDigest(bundle);
  const digestIds = new Set([...(digest.papers ?? []), ...(digest.blogs ?? [])].map(entry => entry.item_id));
  if (!sameSet(d0Ids, digestIds)) throw new Error("d0 graph nodes do not match latest digest");
  const expectedD1 = new Set();
  for (const id of d0Ids) {
    for (const edge of adjacencyById.get(id) ?? []) {
      const neighbor = edge.source_id === id ? edge.target_id : edge.source_id;
      if (!d0Ids.has(neighbor)) expectedD1.add(neighbor);
    }
  }
  if (!sameSet(d1Ids, expectedD1)) throw new Error("d1 graph nodes are not the similarity one-hop of d0");
  const initialGraph = mergeGraphDocuments(initialShards.map(value => value.shard));
  assertGraphNavigation(initialGraph);
  const initialIds = new Set([...d0Ids, ...d1Ids]);
  const expectedInitialEdges = new Set([...edgeOccurrences.keys()].filter(key => key.split("|").every(id => initialIds.has(id))));
  const actualInitialEdges = new Set(initialGraph.edges.filter(edge => edge.data.type === "similarity").map(edge => `${edge.data.source}|${edge.data.target}`));
  if (!sameSet(actualInitialEdges, expectedInitialEdges)) throw new Error("initial graph similarity edges are incomplete");
  return { index, shardCount: initialShards.length + Object.keys(manifest.node_shards).length + Object.keys(manifest.adjacency_shards).length };
}

export function verifyBuild({ dist, bundle }) {
  const { report, snapshot } = readSnapshot(bundle);
  for (const relative of required) {
    if (!fs.existsSync(path.join(dist, relative))) throw new Error(`missing build output: ${relative}`);
  }
  const graph = verifyGraphAssets(dist, bundle, report, snapshot);
  assertSearchMetadata(dist);
  const pagefindFiles = fs.readdirSync(path.join(dist, "pagefind"));
  if (!pagefindFiles.some(file => file.includes("filter"))) throw new Error("Pagefind filters missing");
  const digestFiles = filesUnder(path.join(bundle, "pending-data", "digests")).filter(file => file.endsWith(".json"));
  for (const file of digestFiles) {
    const digest = readJson(file);
    if (!fs.existsSync(path.join(dist, "archive", String(digest.date), "index.html"))) throw new Error(`missing archive date page: ${digest.date}`);
  }
  for (const file of filesUnder(dist).filter(file => file.endsWith(".html"))) {
    const html = fs.readFileSync(file, "utf8");
    if (/\b(?:href|src)=["']\/(?!rec-sys-daily\/)/.test(html)) throw new Error(`root-relative link escapes project base: ${path.relative(dist, file)}`);
  }
  assertNoRawContent(path.join(bundle, "pending-data"));
  for (const file of filesUnder(dist)) {
    if (/\.(pdf|txt|md|zip)$/i.test(file)) throw new Error(`raw source content leaked into build output: ${path.relative(dist, file)}`);
  }

  const distBytes = bytesUnder(dist);
  const pendingBytes = bytesUnder(path.join(bundle, "pending-data"));
  const warnPages = snapshot.warn_pages_artifact_mb * 1024 * 1024;
  const warnRepository = snapshot.warn_repository_data_mb * 1024 * 1024;
  if (distBytes > warnPages) console.warn(`Pages artifact exceeds warning threshold: ${distBytes} bytes`);
  if (pendingBytes > warnRepository) console.warn(`Repository pending-data exceeds warning threshold: ${pendingBytes} bytes`);
  if (distBytes > snapshot.fail_pages_artifact_mb * 1024 * 1024) throw new Error("Pages artifact exceeds configured hard limit");
  console.log(`verified ${required.length} build outputs; ${graph.index.nodes.length} graph index nodes; ${graph.shardCount} graph shards; ${pagefindFiles.length} Pagefind files`);
  return { snapshot, distBytes, pendingBytes };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  verifyBuild({
    dist: path.resolve("dist"),
    bundle: process.env.PUBLISH_BUNDLE_DIR ?? "/workspace/publish-bundle",
  });
}

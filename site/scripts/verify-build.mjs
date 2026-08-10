import fs from "node:fs";
import path from "node:path";

const required = ["index.html", "search/index.html", "graph/index.html", "graph.json", "pagefind/pagefind.js"];

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

function readSnapshot(bundle) {
  const runs = filesUnder(path.join(bundle, "pending-data", "runs")).filter(file => file.endsWith(".json")).sort();
  if (!runs.length) throw new Error("publish bundle has no RunReport");
  const report = JSON.parse(fs.readFileSync(runs.at(-1), "utf8"));
  const snapshot = report.config_snapshot;
  const fields = [
    "graph_max_content_nodes", "graph_recent_days", "target_item_bytes", "max_item_bytes",
    "max_blog_excerpt_chars", "warn_repository_data_mb", "warn_pages_artifact_mb", "fail_pages_artifact_mb",
  ];
  if (!snapshot || fields.some(field => typeof snapshot[field] !== "number" || !Number.isFinite(snapshot[field]) || snapshot[field] <= 0)) {
    throw new Error("invalid RunReport config_snapshot");
  }
  return { report, snapshot };
}

function bytesUnder(root) {
  return filesUnder(root).reduce((total, file) => total + fs.statSync(file).size, 0);
}

function assertNoRawContent(root) {
  const forbidden = /\.(pdf|html|txt)$/i;
  for (const file of filesUnder(root)) {
    if (forbidden.test(file)) throw new Error(`raw source content leaked into artifact: ${path.relative(root, file)}`);
  }
}

export function verifyBuild({ dist, bundle }) {
  const { snapshot } = readSnapshot(bundle);
  for (const relative of required) {
    if (!fs.existsSync(path.join(dist, relative))) throw new Error(`missing build output: ${relative}`);
  }
  const graph = JSON.parse(fs.readFileSync(path.join(dist, "graph.json"), "utf8"));
  if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) throw new Error("invalid graph.json");
  const contentNodes = graph.nodes.filter(node => ["paper", "article", "blog"].includes(node.data?.type)).length;
  if (contentNodes > snapshot.graph_max_content_nodes) throw new Error("graph content node limit exceeded");
  const pagefindFiles = fs.readdirSync(path.join(dist, "pagefind"));
  if (!pagefindFiles.some(file => file.includes("filter"))) throw new Error("Pagefind filters missing");
  assertNoRawContent(path.join(bundle, "pending-data"));
  for (const file of filesUnder(dist)) {
    if (/\.(pdf|txt)$/i.test(file)) throw new Error(`raw source content leaked into build output: ${path.relative(dist, file)}`);
  }

  const distBytes = bytesUnder(dist);
  const pendingBytes = bytesUnder(path.join(bundle, "pending-data"));
  const warnPages = snapshot.warn_pages_artifact_mb * 1024 * 1024;
  const warnRepository = snapshot.warn_repository_data_mb * 1024 * 1024;
  if (distBytes > warnPages) console.warn(`Pages artifact exceeds warning threshold: ${distBytes} bytes`);
  if (pendingBytes > warnRepository) console.warn(`Repository pending-data exceeds warning threshold: ${pendingBytes} bytes`);
  if (distBytes > snapshot.fail_pages_artifact_mb * 1024 * 1024) throw new Error("Pages artifact exceeds configured hard limit");
  console.log(`verified ${required.length} build outputs; ${graph.nodes.length} graph nodes; ${pagefindFiles.length} Pagefind files`);
  return { snapshot, distBytes, pendingBytes };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  verifyBuild({
    dist: path.resolve("dist"),
    bundle: process.env.PUBLISH_BUNDLE_DIR ?? "/workspace/publish-bundle",
  });
}

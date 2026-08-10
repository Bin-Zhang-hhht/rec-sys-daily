import fs from "node:fs";
import path from "node:path";

const dist = path.resolve("dist");
const required = ["index.html", "search/index.html", "graph/index.html", "graph.json", "pagefind/pagefind.js"];
for (const relative of required) {
  if (!fs.existsSync(path.join(dist, relative))) throw new Error(`missing build output: ${relative}`);
}
const graph = JSON.parse(fs.readFileSync(path.join(dist, "graph.json"), "utf8"));
if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) throw new Error("invalid graph.json");
if (graph.nodes.filter(node => ["paper", "article"].includes(node.data?.type)).length > 80) throw new Error("graph content node limit exceeded");
const pagefindFiles = fs.readdirSync(path.join(dist, "pagefind"));
if (!pagefindFiles.some(file => file.includes("filter"))) throw new Error("Pagefind filters missing");
console.log(`verified ${required.length} build outputs; ${graph.nodes.length} graph nodes; ${pagefindFiles.length} Pagefind files`);

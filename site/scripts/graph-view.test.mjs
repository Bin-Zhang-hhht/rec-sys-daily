import test from "node:test";
import assert from "node:assert/strict";
import {
  adaptGraphDocumentToECharts,
  buildGraphAdjacency,
  centerGraphDocument,
  escapeEChartsRichText,
  filterGraphDocument,
  GRAPH_NODE_STYLES,
  graphEdgeColor,
  graphNodeCanvasLabel,
  graphNodeLabelVisible,
  graphNodeNeighborhood,
  graphNodeOpacity,
  graphNodeSymbolSize,
  searchGraphNodes,
} from "../src/lib/graph-view.ts";

const now = Date.parse("2026-08-15T00:00:00Z");
const nodes = [
  { data: { id: "paper-a", label: "Ｇｒａｐｈ Ranking", type: "paper", published_at: "2026-08-14T00:00:00Z", tags: ["user", "ranking"], search_terms: ["图排序", "Graph Ranking"], weight: 4 } },
  { data: { id: "article-b", label: "Ranking in Production", type: "article", published_at: "2026-07-20T00:00:00Z", tags: ["room", "ranking"], search_terms: ["线上排序"], weight: 2 } },
  { data: { id: "paper-c", label: "Graph Retrieval", type: "paper", published_at: "2025-02-01T00:00:00Z", tags: ["user", "retrieval"], search_terms: ["图召回"], weight: 1 } },
  { data: { id: "target:user", label: "用户推荐 / User Recommendation", type: "target", search_terms: ["user", "用户推荐", "User Recommendation"], weight: 2 } },
  { data: { id: "target:room", label: "房间推荐 / Room Recommendation", type: "target", search_terms: ["room", "房间推荐", "Room Recommendation"], weight: 1 } },
  { data: { id: "task:ranking", label: "排序 / Ranking", type: "task", search_terms: ["ranking", "排序"], weight: 2 } },
  { data: { id: "task:retrieval", label: "召回 / Retrieval", type: "task", search_terms: ["retrieval", "召回"], weight: 1 } },
  { data: { id: "method:isolated", label: "孤立方法 / Isolated", type: "method", weight: 1 } },
];

const edge = (id, source, target, overrides = {}) => ({ data: {
  id,
  source,
  target,
  type: "related",
  confidence: 0.9,
  evidence: "fixture",
  generated_by: "deepseek",
  ...overrides,
} });

const graph = {
  nodes,
  edges: [
    edge("taxonomy:paper-a|target:user", "paper-a", "target:user", { type: "target", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:paper-a|task:ranking", "paper-a", "task:ranking", { type: "task", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:article-b|target:room", "article-b", "target:room", { type: "target", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:article-b|task:ranking", "article-b", "task:ranking", { type: "task", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:paper-c|target:user", "paper-c", "target:user", { type: "target", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:paper-c|task:retrieval", "paper-c", "task:retrieval", { type: "task", confidence: 1, generated_by: "topics.yaml" }),
    edge("relation:paper-a|article-b|supports", "paper-a", "article-b", { type: "supports" }),
  ],
};

test("adjacency is undirected and includes isolated nodes", () => {
  const adjacency = buildGraphAdjacency(graph);
  assert.deepEqual([...adjacency.get("paper-a")], ["target:user", "task:ranking", "article-b"]);
  assert.equal(adjacency.get("target:user").has("paper-a"), true);
  assert.deepEqual([...adjacency.get("method:isolated")], []);
});

test("filters use OR within a group, AND between groups, and prune unrelated taxonomy", () => {
  const filtered = filterGraphDocument(graph, {
    groups: new Map([
      ["targets", new Set(["user", "room"])],
      ["tasks", new Set(["ranking"])],
    ]),
  });
  assert.deepEqual(filtered.nodes.map(node => node.data.id), [
    "paper-a",
    "article-b",
    "target:user",
    "target:room",
    "task:ranking",
  ]);
  assert.equal(filtered.edges.some(value => value.data.target === "task:retrieval"), false);
  assert.equal(filtered.edges.some(value => value.data.id.startsWith("relation:")), true);
});

test("year and cumulative age filters use the supplied clock", () => {
  assert.deepEqual(
    filterGraphDocument(graph, { year: "2026", age: "7d", now }).nodes.map(node => node.data.id),
    ["paper-a", "target:user", "task:ranking"],
  );
  assert.deepEqual(
    filterGraphDocument(graph, { age: "30d", now }).nodes.map(node => node.data.id),
    ["paper-a", "article-b", "target:user", "target:room", "task:ranking"],
  );
});

test("center returns the content node and its induced one-hop neighborhood", () => {
  const centered = centerGraphDocument(graph, "paper-a");
  assert.deepEqual(centered.nodes.map(node => node.data.id), ["paper-a", "article-b", "target:user", "task:ranking"]);
  assert.equal(centered.edges.length, 4);
  assert.equal(centerGraphDocument(graph, "target:user"), null);
  assert.equal(centerGraphDocument(graph, "missing"), null);
});

test("generic neighborhood supports taxonomy search results", () => {
  const centered = graphNodeNeighborhood(graph, "task:ranking");
  assert.deepEqual(centered.nodes.map(node => node.data.id), ["paper-a", "article-b", "task:ranking"]);
  assert.equal(centered.edges.length, 3);
  assert.equal(graphNodeNeighborhood(graph, "missing"), null);
});

test("search applies NFKC lowercase normalization and stable relevance ordering", () => {
  assert.deepEqual(searchGraphNodes(graph, "  GRAPH   ").map(node => node.data.id), ["paper-a", "paper-c"]);
  assert.deepEqual(searchGraphNodes(graph, "ｕｓｅｒ recommendation").map(node => node.data.id), ["target:user"]);
  assert.deepEqual(searchGraphNodes(graph, "ranking").map(node => node.data.id), ["task:ranking", "article-b", "paper-a"]);
  assert.deepEqual(searchGraphNodes(graph, "  "), []);
});

test("node size uses bounded square-root degree weighting from 16 to 56", () => {
  assert.equal(graphNodeSymbolSize(1, 9), 16);
  assert.equal(graphNodeSymbolSize(9, 9), 56);
  assert.equal(graphNodeSymbolSize(3, 9), 36);
  assert.equal(graphNodeSymbolSize(-1, 9), 16);
  assert.equal(graphNodeSymbolSize(20, 9), 56);
});

test("nodes use 50% opacity while selection mutes unrelated nodes", () => {
  assert.equal(graphNodeOpacity(false, false), 0.5);
  assert.equal(graphNodeOpacity(true, true), 0.5);
  assert.equal(graphNodeOpacity(true, false), 0.14);
});

test("content titles stay hidden while taxonomy labels respond to zoom and emphasis", () => {
  assert.equal(graphNodeLabelVisible("paper", 1), false);
  assert.equal(graphNodeLabelVisible("article", 1, true), false);
  assert.equal(graphNodeLabelVisible("target", 1), true);
  assert.equal(graphNodeLabelVisible("task", 0.5), false);
  assert.equal(graphNodeLabelVisible("method", 0.5, true), true);
});

test("canvas labels hide content titles and prefer the taxonomy Chinese short name", () => {
  assert.equal(graphNodeCanvasLabel(nodes[0].data), "");
  assert.equal(graphNodeCanvasLabel(nodes[1].data), "");
  assert.equal(graphNodeCanvasLabel(nodes[3].data), "用户推荐");
  assert.equal(graphNodeCanvasLabel(nodes[7].data), "孤立方法 / Isolated");
});

test("rich-text escaping prevents user text from becoming an ECharts style token", () => {
  const escaped = escapeEChartsRichText("{danger|title}\\path");
  assert.equal(escaped.replaceAll("\u2060", ""), "{danger|title}\\path");
  assert.doesNotMatch(escaped, /\{[a-zA-Z0-9_]+\|[^}]*\}/);
});

test("ECharts adapter preserves article and differentiates taxonomy and model edges", () => {
  const adapted = adaptGraphDocumentToECharts(graph);
  const article = adapted.nodes.find(node => node.id === "article-b");
  assert.equal(article.type, "article");
  assert.equal(article.category, "article");
  assert.equal(article.symbol, "circle");
  assert.equal(adapted.nodes.find(node => node.id === "target:user").symbol, "circle");

  const taxonomy = adapted.links.find(link => link.id.startsWith("taxonomy:"));
  const model = adapted.links.find(link => link.id.startsWith("relation:"));
  assert.equal(taxonomy.edgeKind, "taxonomy");
  assert.equal(taxonomy.lineStyle.type, "solid");
  assert.deepEqual(taxonomy.symbol, ["none", "none"]);
  assert.equal(model.edgeKind, "model");
  assert.equal(model.lineStyle.type, "dashed");
  assert.deepEqual(model.symbol, ["none", "arrow"]);
  assert.deepEqual(model.symbolSize, [0, 5]);
});

test("taxonomy palette matches site chips and edges inherit the intended endpoint", () => {
  assert.deepEqual(
    Object.fromEntries(["target", "scenario", "task", "method"].map(type => [type, GRAPH_NODE_STYLES[type].fill])),
    {
      target: "#0ea5e9",
      scenario: "#10b981",
      task: "#f59e0b",
      method: "#8b5cf6",
    },
  );
  assert.equal(graphEdgeColor("taxonomy"), "target");
  assert.equal(graphEdgeColor("model"), "source");
});

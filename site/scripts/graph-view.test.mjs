import test from "node:test";
import assert from "node:assert/strict";
import {
  adaptGraphDocumentToECharts,
  buildGraphAdjacency,
  centerGraphDocument,
  escapeEChartsRichText,
  filterGraphDocument,
  filterGraphNodeTypes,
  graphExpansionCandidates,
  graphInducedSimilarityEdges,
  GRAPH_NODE_STYLES,
  graphEdgeColor,
  graphEdgeLineType,
  graphNodeCanvasLabel,
  graphNodeLabelVisible,
  graphNodeNeighborhood,
  graphNodeOpacity,
  graphNodeSymbol,
  graphNodeSymbolSize,
  searchGraphNodes,
} from "../src/lib/graph-view.ts";

const now = Date.parse("2026-08-15T00:00:00Z");
const nodes = [
  { data: { id: "paper-a", label: "Ｇｒａｐｈ Ranking", type: "paper", published_at: "2026-08-14T00:00:00Z", tags: ["user", "ranking"], search_terms: ["图排序", "Graph Ranking"], weight: 4 } },
  { data: { id: "blog-b", label: "Ranking in Production", type: "blog", published_at: "2026-07-20T00:00:00Z", tags: ["room", "ranking"], search_terms: ["线上排序"], weight: 2 } },
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
    type: "similarity",
  confidence: 0.9,
  evidence: "fixture",
    generated_by: "fastembed",
  ...overrides,
} });

const graph = {
  nodes,
  edges: [
    edge("taxonomy:paper-a|target:user", "paper-a", "target:user", { type: "taxonomy", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:paper-a|task:ranking", "paper-a", "task:ranking", { type: "taxonomy", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:blog-b|target:room", "blog-b", "target:room", { type: "taxonomy", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:blog-b|task:ranking", "blog-b", "task:ranking", { type: "taxonomy", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:paper-c|target:user", "paper-c", "target:user", { type: "taxonomy", confidence: 1, generated_by: "topics.yaml" }),
    edge("taxonomy:paper-c|task:retrieval", "paper-c", "task:retrieval", { type: "taxonomy", confidence: 1, generated_by: "topics.yaml" }),
    edge("similarity:blog-b|paper-a", "paper-a", "blog-b", { type: "similarity", score: 0.9, source_rank: 1, target_rank: 1 }),
  ],
};

test("adjacency is undirected and includes isolated nodes", () => {
  const adjacency = buildGraphAdjacency(graph);
  assert.deepEqual([...adjacency.get("paper-a")], ["target:user", "task:ranking", "blog-b"]);
  assert.equal(adjacency.get("target:user").has("paper-a"), true);
  assert.deepEqual([...adjacency.get("method:isolated")], []);
});

test("initial expansion is deterministic, breadth-first, and bounded", () => {
  const neighbors = new Map([
    ["paper-a", [
      { source_id: "paper-a", target_id: "paper-d", score: 0.82 },
      { source_id: "paper-a", target_id: "paper-c", score: 0.91 },
    ]],
    ["blog-b", [
      { source_id: "blog-b", target_id: "paper-e", score: 0.95 },
      { source_id: "blog-b", target_id: "paper-c", score: 0.93 },
    ]],
  ]);
  assert.deepEqual(
    graphExpansionCandidates(["paper-a", "blog-b"], neighbors, new Set(["paper-a", "blog-b"]), 3),
    ["paper-e", "paper-c", "paper-d"],
  );
  assert.deepEqual(graphExpansionCandidates(["paper-a"], neighbors, new Set(["paper-a"]), 1), ["paper-c"]);
  assert.deepEqual(graphExpansionCandidates(["paper-a"], neighbors, new Set(["paper-a"]), 0), []);
});

test("initial expansion retains the final layer induced similarity edges", () => {
  const edges = [
    edge("similarity:paper-a|paper-c", "paper-a", "paper-c"),
    edge("similarity:paper-c|paper-d", "paper-c", "paper-d"),
    edge("similarity:paper-d|paper-e", "paper-d", "paper-e"),
    edge("taxonomy:paper-c|target:user", "paper-c", "target:user", { type: "taxonomy" }),
  ];
  assert.deepEqual(
    graphInducedSimilarityEdges(edges, new Set(["paper-a", "paper-c", "paper-d"])).map(value => value.data.id),
    ["similarity:paper-a|paper-c", "similarity:paper-c|paper-d"],
  );
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
    "blog-b",
    "target:user",
    "target:room",
    "task:ranking",
  ]);
  assert.equal(filtered.edges.some(value => value.data.target === "task:retrieval"), false);
  assert.equal(filtered.edges.some(value => value.data.id.startsWith("similarity:")), true);
});

test("year and cumulative age filters use the supplied clock", () => {
  assert.deepEqual(
    filterGraphDocument(graph, { year: "2026", age: "7d", now }).nodes.map(node => node.data.id),
    ["paper-a", "target:user", "task:ranking"],
  );
  assert.deepEqual(
    filterGraphDocument(graph, { age: "30d", now }).nodes.map(node => node.data.id),
    ["paper-a", "blog-b", "target:user", "target:room", "task:ranking"],
  );
});

test("node type toggles hide disabled types, remove their edges, and prune isolated taxonomy", () => {
  const blogAndTask = filterGraphNodeTypes(graph, new Set(["blog", "task"]));
  assert.deepEqual(blogAndTask.nodes.map(node => node.data.id), ["blog-b", "task:ranking"]);
  assert.deepEqual(blogAndTask.edges.map(value => value.data.id), ["taxonomy:blog-b|task:ranking"]);

  const contentWithoutRelationships = filterGraphNodeTypes(graph, new Set(["paper", "method"]));
  assert.deepEqual(contentWithoutRelationships.nodes.map(node => node.data.id), ["paper-a", "paper-c"]);
  assert.deepEqual(contentWithoutRelationships.edges, []);
});

test("center returns the content node and its induced one-hop neighborhood", () => {
  const centered = centerGraphDocument(graph, "paper-a");
  assert.deepEqual(centered.nodes.map(node => node.data.id), ["paper-a", "blog-b", "target:user", "target:room", "task:ranking"]);
  assert.equal(centered.edges.length, 5);
  assert.equal(centerGraphDocument(graph, "target:user"), null);
  assert.equal(centerGraphDocument(graph, "missing"), null);
});

test("generic neighborhood supports taxonomy search results", () => {
  const centered = graphNodeNeighborhood(graph, "task:ranking");
  assert.deepEqual(centered.nodes.map(node => node.data.id), ["paper-a", "blog-b", "task:ranking"]);
  assert.equal(centered.edges.length, 3);
  assert.equal(graphNodeNeighborhood(graph, "missing"), null);
});

test("search applies NFKC lowercase normalization and stable relevance ordering", () => {
  assert.deepEqual(searchGraphNodes(graph, "  GRAPH   ").map(node => node.data.id), ["paper-a", "paper-c"]);
  assert.deepEqual(searchGraphNodes(graph, "ｕｓｅｒ recommendation").map(node => node.data.id), ["target:user"]);
  assert.deepEqual(searchGraphNodes(graph, "ranking").map(node => node.data.id), ["task:ranking", "blog-b", "paper-a"]);
  assert.deepEqual(searchGraphNodes(graph, "  "), []);
});

test("node size uses a fixed bounded logarithmic degree scale from 16 to 56", () => {
  assert.equal(graphNodeSymbolSize(1), 16);
  assert.equal(graphNodeSymbolSize(2), 24);
  assert.equal(graphNodeSymbolSize(4), 32);
  assert.equal(graphNodeSymbolSize(8), 40);
  assert.equal(graphNodeSymbolSize(16), 48);
  assert.equal(graphNodeSymbolSize(32), 56);
  assert.equal(graphNodeSymbolSize(64), 56);
  assert.equal(graphNodeSymbolSize(-1), 16);
  assert.equal(graphNodeSymbolSize(Number.NaN), 16);
});

test("nodes use 50% opacity while selection mutes unrelated nodes", () => {
  assert.equal(graphNodeOpacity(false, false), 0.5);
  assert.equal(graphNodeOpacity(true, true), 0.5);
  assert.equal(graphNodeOpacity(true, false), 0.14);
});

test("content titles stay hidden while taxonomy labels respond to zoom and emphasis", () => {
  assert.equal(graphNodeLabelVisible("paper", 1), false);
  assert.equal(graphNodeLabelVisible("blog", 1, true), false);
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

test("content nodes use distinct local icons while taxonomy remains circular", () => {
  assert.equal(graphNodeSymbol("paper", "/rec-sys-daily/icons/graph/"), "image:///rec-sys-daily/icons/graph/paper.svg");
  assert.equal(graphNodeSymbol("blog", "/rec-sys-daily/icons/graph/"), "image:///rec-sys-daily/icons/graph/blog.svg");
  assert.equal(graphNodeSymbol("target", "/rec-sys-daily/icons/graph/"), "circle");
});

test("rich-text escaping prevents user text from becoming an ECharts style token", () => {
  const escaped = escapeEChartsRichText("{danger|title}\\path");
  assert.equal(escaped.replaceAll("\u2060", ""), "{danger|title}\\path");
  assert.doesNotMatch(escaped, /\{[a-zA-Z0-9_]+\|[^}]*\}/);
});

test("ECharts adapter preserves blog and renders similarity as an undirected neutral edge", () => {
  const adapted = adaptGraphDocumentToECharts(graph);
  const blog = adapted.nodes.find(node => node.id === "blog-b");
  assert.equal(blog.type, "blog");
  assert.equal(blog.category, "blog");
  assert.equal(blog.symbol, "circle");
  assert.equal(adapted.nodes.find(node => node.id === "target:user").symbol, "circle");

  const taxonomy = adapted.links.find(link => link.id.startsWith("taxonomy:"));
  const similarity = adapted.links.find(link => link.id.startsWith("similarity:"));
  assert.equal(taxonomy.edgeKind, "taxonomy");
  assert.equal(taxonomy.lineStyle.color, "#94a3b8");
  assert.equal(taxonomy.lineStyle.type, "solid");
  assert.deepEqual(taxonomy.symbol, ["none", "none"]);
  assert.equal(similarity.edgeKind, "similarity");
  assert.equal(similarity.lineStyle.color, "#94a3b8");
  assert.equal(similarity.lineStyle.type, "dashed");
  assert.deepEqual(similarity.symbol, ["none", "none"]);
  assert.deepEqual(similarity.symbolSize, [0, 0]);
});

test("content nodes are gray, taxonomy matches chips, and every edge is neutral gray", () => {
  assert.deepEqual(
    Object.fromEntries(["target", "scenario", "task", "method"].map(type => [type, GRAPH_NODE_STYLES[type].fill])),
    {
      target: "#0ea5e9",
      scenario: "#10b981",
      task: "#f59e0b",
      method: "#8b5cf6",
    },
  );
  assert.deepEqual(
    Object.fromEntries(["paper", "blog"].map(type => [type, GRAPH_NODE_STYLES[type].fill])),
    { paper: "#d1d5db", blog: "#9ca3af" },
  );
  assert.equal(graphEdgeColor("taxonomy"), "#94a3b8");
  assert.equal(graphEdgeColor("similarity"), "#94a3b8");
  assert.equal(graphEdgeLineType("taxonomy"), "solid");
  assert.equal(graphEdgeLineType("similarity"), "dashed");
});

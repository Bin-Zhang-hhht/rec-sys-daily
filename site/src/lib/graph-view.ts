import type { GraphDocument, GraphEdge, GraphNode } from "./graph";

export type GraphTaxonomyGroup = "targets" | "scenarios" | "tasks" | "methods";
export type GraphAgeFilter = "7d" | "30d" | "365d";

type FilterValues = Iterable<string>;

export type GraphFilterGroups =
  | Partial<Record<GraphTaxonomyGroup, FilterValues>>
  | ReadonlyMap<string, FilterValues>;

export type GraphFilters = {
  groups?: GraphFilterGroups;
  year?: string;
  age?: GraphAgeFilter;
  now?: number;
};

export type EChartsGraphNode = GraphNode["data"] & {
  name: string;
  value: number;
  category: GraphNode["data"]["type"];
  symbol: "circle";
  symbolSize: number;
};

export type GraphSimilarityNeighbor = {
  source_id: string;
  target_id: string;
  score: number;
};

export type EChartsGraphLink = GraphEdge["data"] & {
  value: number;
  edgeKind: "taxonomy" | "similarity";
  lineStyle: {
    type: "solid" | "dashed";
    opacity: number;
  };
  symbol: ["none", "none" | "arrow"];
  symbolSize: [number, number];
};

export type EChartsGraphData = {
  nodes: EChartsGraphNode[];
  links: EChartsGraphLink[];
  categories: Array<{ name: GraphNode["data"]["type"] }>;
};

const CONTENT_TYPES = new Set<GraphNode["data"]["type"]>(["paper", "blog"]);
export const GRAPH_NODE_TYPES = [
  "paper",
  "blog",
  "target",
  "scenario",
  "task",
  "method",
] as const satisfies ReadonlyArray<GraphNode["data"]["type"]>;
const AGE_DAYS: Record<GraphAgeFilter, number> = { "7d": 7, "30d": 30, "365d": 365 };
const DAY_MS = 86_400_000;
const MIN_NODE_SIZE = 16;
const MAX_NODE_SIZE = 56;
export const GRAPH_NODE_OPACITY = 0.5;
const MUTED_GRAPH_NODE_OPACITY = 0.14;

export const GRAPH_NODE_STYLES: Readonly<
  Record<GraphNode["data"]["type"], Readonly<{ fill: string }>>
> = {
  paper: { fill: "#d1d5db" },
  blog: { fill: "#9ca3af" },
  target: { fill: "#0ea5e9" },
  scenario: { fill: "#10b981" },
  task: { fill: "#f59e0b" },
  method: { fill: "#8b5cf6" },
};

export function isContentGraphNode(node: GraphNode): boolean {
  return CONTENT_TYPES.has(node.data.type);
}

export function graphNodeLabelVisible(
  type: GraphNode["data"]["type"],
  zoomLevel: number,
  emphasized = false,
): boolean {
  if (CONTENT_TYPES.has(type)) return false;
  return zoomLevel >= 0.72 || emphasized;
}

export function graphNodeCanvasLabel(data: GraphNode["data"]): string {
  if (CONTENT_TYPES.has(data.type)) return "";
  return data.search_terms?.[1]?.trim() || data.label;
}

export function buildGraphAdjacency(graph: GraphDocument): Map<string, Set<string>> {
  const adjacency = new Map(graph.nodes.map(node => [node.data.id, new Set<string>()]));
  for (const edge of graph.edges) {
    adjacency.get(edge.data.source)?.add(edge.data.target);
    adjacency.get(edge.data.target)?.add(edge.data.source);
  }
  return adjacency;
}

function groupEntries(groups: GraphFilterGroups | undefined): Array<[string, Set<string>]> {
  if (!groups) return [];
  const entries = groups instanceof Map ? [...groups.entries()] : Object.entries(groups);
  return entries
    .map(([group, values]) => [group, new Set(values ?? [])] as [string, Set<string>])
    .filter(([, values]) => values.size > 0);
}

function matchesTime(node: GraphNode, filters: GraphFilters): boolean {
  if (!filters.year && !filters.age) return true;
  const publishedAt = node.data.published_at;
  const publishedTime = publishedAt ? Date.parse(publishedAt) : Number.NaN;
  if (!Number.isFinite(publishedTime)) return false;
  if (filters.year && !publishedAt?.startsWith(`${filters.year}-`)) return false;
  if (!filters.age) return true;
  const now = filters.now ?? Date.now();
  const ageDays = Math.max(0, (now - publishedTime) / DAY_MS);
  return ageDays <= AGE_DAYS[filters.age];
}

function contentIdsMatching(graph: GraphDocument, filters: GraphFilters): Set<string> {
  const selectedGroups = groupEntries(filters.groups);
  return new Set(graph.nodes.filter(node => {
    if (!isContentGraphNode(node)) return false;
    const tags = new Set(node.data.tags ?? []);
    return selectedGroups.every(([, selected]) => [...selected].some(value => tags.has(value)))
      && matchesTime(node, filters);
  }).map(node => node.data.id));
}

/** Keep matching content, its directly connected taxonomy nodes, and induced edges only. */
export function graphForContentIds(graph: GraphDocument, contentIds: ReadonlySet<string>): GraphDocument {
  const nodesById = new Map(graph.nodes.map(node => [node.data.id, node]));
  const visibleIds = new Set<string>();
  for (const id of contentIds) {
    const node = nodesById.get(id);
    if (node && isContentGraphNode(node)) visibleIds.add(id);
  }
  for (const edge of graph.edges) {
    const source = nodesById.get(edge.data.source);
    const target = nodesById.get(edge.data.target);
    if (visibleIds.has(edge.data.source) && target && !isContentGraphNode(target)) {
      visibleIds.add(target.data.id);
    }
    if (visibleIds.has(edge.data.target) && source && !isContentGraphNode(source)) {
      visibleIds.add(source.data.id);
    }
  }
  return {
    nodes: graph.nodes.filter(node => visibleIds.has(node.data.id)),
    edges: graph.edges.filter(edge => visibleIds.has(edge.data.source) && visibleIds.has(edge.data.target)),
  };
}

export function filterGraphDocument(graph: GraphDocument, filters: GraphFilters = {}): GraphDocument {
  return graphForContentIds(graph, contentIdsMatching(graph, filters));
}

/** Hide disabled node types and remove taxonomy nodes left without a visible relationship. */
export function filterGraphNodeTypes(
  graph: GraphDocument,
  visibleTypes: ReadonlySet<GraphNode["data"]["type"]>,
): GraphDocument {
  const typeVisibleIds = new Set(
    graph.nodes
      .filter(node => visibleTypes.has(node.data.type))
      .map(node => node.data.id),
  );
  const visibleEdges = graph.edges.filter(
    edge => typeVisibleIds.has(edge.data.source) && typeVisibleIds.has(edge.data.target),
  );
  const connectedIds = new Set(visibleEdges.flatMap(edge => [edge.data.source, edge.data.target]));
  const visibleNodes = graph.nodes.filter(
    node => typeVisibleIds.has(node.data.id) && (isContentGraphNode(node) || connectedIds.has(node.data.id)),
  );
  return { nodes: visibleNodes, edges: visibleEdges };
}

export function graphNodeNeighborhood(graph: GraphDocument, nodeId: string): GraphDocument | null {
  if (!graph.nodes.some(node => node.data.id === nodeId)) return null;
  const visibleIds = new Set([nodeId, ...(buildGraphAdjacency(graph).get(nodeId) ?? [])]);
  return {
    nodes: graph.nodes.filter(node => visibleIds.has(node.data.id)),
    edges: graph.edges.filter(edge => visibleIds.has(edge.data.source) && visibleIds.has(edge.data.target)),
  };
}

/** Center on similarity distance only, then attach taxonomy navigation for the loaded content. */
export function centerGraphDocument(graph: GraphDocument, centerId: string): GraphDocument | null {
  const center = graph.nodes.find(node => node.data.id === centerId);
  if (!center || !isContentGraphNode(center)) return null;
  const contentIds = new Set([centerId]);
  for (const edge of graph.edges) {
    if (edge.data.type !== "similarity") continue;
    if (edge.data.source === centerId) contentIds.add(edge.data.target);
    if (edge.data.target === centerId) contentIds.add(edge.data.source);
  }
  return graphForContentIds(graph, contentIds);
}

export function normalizeGraphSearchText(value: string): string {
  return value.normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
}

function searchText(node: GraphNode): string {
  return normalizeGraphSearchText([
    node.data.id,
    node.data.label,
    ...(node.data.search_terms ?? node.data.tags ?? []),
  ].join(" "));
}

function searchRank(node: GraphNode, query: string): number {
  const id = normalizeGraphSearchText(node.data.id);
  const label = normalizeGraphSearchText(node.data.label);
  const configuredTerms = (node.data.search_terms ?? node.data.tags ?? [])
    .map(normalizeGraphSearchText);
  if (id === query || label === query || configuredTerms.includes(query)) return 3;
  if (id.startsWith(query) || label.startsWith(query)) return 2;
  return 1;
}

/** Results use relevance buckets and preserve graph order within equal buckets. */
export function searchGraphNodes(graph: GraphDocument, query: string): GraphNode[] {
  const normalizedQuery = normalizeGraphSearchText(query);
  if (!normalizedQuery) return [];
  const terms = normalizedQuery.split(" ");
  return graph.nodes
    .map((node, index) => ({ node, index, haystack: searchText(node) }))
    .filter(result => terms.every(term => result.haystack.includes(term)))
    .map(result => ({ ...result, rank: searchRank(result.node, normalizedQuery) }))
    .sort((left, right) => right.rank - left.rank || left.index - right.index)
    .map(result => result.node);
}

export function graphNodeSymbolSize(degree: number): number {
  const safeDegree = Number.isFinite(degree) ? Math.max(1, degree) : 1;
  return Math.min(MAX_NODE_SIZE, MIN_NODE_SIZE + 8 * Math.log2(safeDegree));
}

export function graphNodeOpacity(selectionActive: boolean, focused: boolean): number {
  return selectionActive && !focused ? MUTED_GRAPH_NODE_OPACITY : GRAPH_NODE_OPACITY;
}

export function graphNodeSymbol(type: GraphNode["data"]["type"], iconBase: string): string {
  if (type === "paper") return `image://${iconBase}paper.svg`;
  if (type === "blog") return `image://${iconBase}blog.svg`;
  return "circle";
}

/** Break ECharts rich-text delimiters with invisible word joiners without changing their appearance. */
export function escapeEChartsRichText(value: string): string {
  const joiner = "\u2060";
  return value
    .replace(/\{/g, `{${joiner}`)
    .replace(/\|/g, `${joiner}|`)
    .replace(/\}/g, `${joiner}}`);
}

export function graphEdgeKind(edge: GraphEdge): "taxonomy" | "similarity" {
  return edge.data.type;
}

export function graphEdgeColor(edgeKind: EChartsGraphLink["edgeKind"]): "#94a3b8" {
  void edgeKind;
  return "#94a3b8";
}

/** Select one deterministic breadth-first expansion layer without revisiting content IDs. */
export function graphExpansionCandidates(
  frontierIds: readonly string[],
  neighborsById: ReadonlyMap<string, readonly GraphSimilarityNeighbor[]>,
  visitedIds: ReadonlySet<string>,
  limit: number,
): string[] {
  if (!Number.isInteger(limit) || limit <= 0) return [];
  const selected: string[] = [];
  const seen = new Set(visitedIds);
  for (const id of [...new Set(frontierIds)].sort()) {
    const neighbors = [...(neighborsById.get(id) ?? [])].sort((left, right) => {
      const leftId = left.source_id === id ? left.target_id : left.source_id;
      const rightId = right.source_id === id ? right.target_id : right.source_id;
      return right.score - left.score || leftId.localeCompare(rightId);
    });
    for (const edge of neighbors) {
      const neighbor = edge.source_id === id ? edge.target_id : edge.source_id;
      if (neighbor === id || seen.has(neighbor)) continue;
      seen.add(neighbor);
      selected.push(neighbor);
      if (selected.length >= limit) return selected;
    }
  }
  return selected;
}

export function graphInducedSimilarityEdges(
  edges: readonly GraphEdge[],
  contentIds: ReadonlySet<string>,
): GraphEdge[] {
  return edges.filter(edge =>
    edge.data.type === "similarity"
    && contentIds.has(edge.data.source)
    && contentIds.has(edge.data.target));
}

export function graphEdgeLineType(edgeKind: EChartsGraphLink["edgeKind"]): "solid" | "dashed" {
  return edgeKind === "taxonomy" ? "solid" : "dashed";
}

export function adaptGraphDocumentToECharts(
  graph: GraphDocument,
): EChartsGraphData {
  return {
    categories: GRAPH_NODE_TYPES.map(name => ({ name })),
    nodes: graph.nodes.map(({ data }) => ({
      ...data,
      name: data.label,
      value: data.weight,
      category: data.type,
      symbol: "circle",
      symbolSize: graphNodeSymbolSize(data.weight),
    })),
    links: graph.edges.map(({ data }) => {
      const edgeKind = graphEdgeKind({ data });
      return {
        ...data,
        value: data.confidence,
        edgeKind,
        lineStyle: {
          color: graphEdgeColor(edgeKind),
          type: graphEdgeLineType(edgeKind),
          opacity: edgeKind === "taxonomy" ? 0.28 : 0.34,
        },
        symbol: ["none", "none"],
        symbolSize: [0, 0],
      };
    }),
  };
}

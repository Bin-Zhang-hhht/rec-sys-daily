import type { BuildConfigSnapshot, Item, SimilarityArtifact, SimilarityEdge, Taxonomy } from "./data";
import { itemPath, sitePath } from "./paths";

export type GraphContentType = "paper" | "blog";
export type GraphTaxonomyType = "target" | "scenario" | "task" | "method";
export type GraphNodeType = GraphContentType | GraphTaxonomyType;

export type GraphNode = {
  data: {
    id: string;
    label: string;
    type: GraphNodeType;
    href?: string;
    summary?: string;
    published_at?: string;
    tags?: string[];
    search_terms?: string[];
    weight: number;
  };
};

export type GraphEdge = {
  data: {
    id: string;
    source: string;
    target: string;
    type: "taxonomy" | "similarity";
    confidence: number;
    evidence: string;
    generated_by: string;
    score?: number;
    source_rank?: number;
    target_rank?: number;
  };
};

export type GraphDocument = { nodes: GraphNode[]; edges: GraphEdge[] };
export type GraphShard = { id: string; document: GraphDocument; content_ids: string[] };

export type GraphIndexRecord = {
  id: string;
  type: GraphContentType;
  label: string;
  href: string;
  summary: string;
  published_at: string;
  tags: string[];
  search_terms: string[];
  node_shard: string;
  adjacency_shard: string;
};

export type GraphManifest = {
  schema_version: "1";
  run_id: string;
  index_url: string;
  initial: {
    d0_urls: string[];
    d1_urls: string[];
    d0_content_ids: string[];
    d1_content_ids: string[];
    max_content_nodes: number;
  };
  node_shards: Record<string, string>;
  adjacency_shards: Record<string, string>;
  total_content_nodes: number;
  total_similarity_edges: number;
};

export type GraphIndexDocument = { schema_version: "1"; nodes: GraphIndexRecord[] };
export type GraphAssets = {
  manifest: GraphManifest;
  index: GraphIndexDocument;
  d0_shards: GraphShard[];
  d1_shards: GraphShard[];
  node_shards: GraphShard[];
  adjacency_shards: Array<{ id: string; entries: Array<{ id: string; neighbors: SimilarityEdge[] }> }>;
};

const groups = ["targets", "scenarios", "tasks", "methods"] as const;

function taxonomyNodeId(group: string, id: string): string {
  return `${group.slice(0, -1)}:${id}`;
}

function contentNode(item: Item): GraphNode {
  const tags = groups.flatMap(group => item[group]);
  return {
    data: {
      id: item.id,
      label: item.title,
      type: item.kind,
      href: itemPath(item),
      summary: item.summary_zh,
      published_at: item.published_at,
      tags,
      search_terms: [item.id, item.title, ...tags],
      weight: 1,
    },
  };
}

function similarityGraphEdge(edge: SimilarityEdge): GraphEdge {
  return {
    data: {
      id: `similarity:${edge.source_id}|${edge.target_id}`,
      source: edge.source_id,
      target: edge.target_id,
      type: "similarity",
      confidence: edge.score,
      evidence: "FastEmbed cosine similarity",
      generated_by: "fastembed",
      score: edge.score,
      source_rank: edge.source_rank,
      target_rank: edge.target_rank,
    },
  };
}

export function graphNodeFromIndex(record: GraphIndexRecord): GraphNode {
  return {
    data: {
      id: record.id,
      label: record.label,
      type: record.type,
      href: record.href,
      summary: record.summary,
      published_at: record.published_at,
      tags: [...record.tags],
      search_terms: [...record.search_terms],
      weight: 1,
    },
  };
}

export function limitGraphContent(
  graph: GraphDocument,
  maxContentNodes: number,
): GraphDocument {
  const content = graph.nodes.filter(node => node.data.type === "paper" || node.data.type === "blog");
  if (content.length <= maxContentNodes) return graph;
  const scores = new Map<string, number>();
  for (const edge of graph.edges) {
    if (edge.data.type !== "similarity" || edge.data.score == null) continue;
    scores.set(edge.data.source, Math.max(scores.get(edge.data.source) ?? 0, edge.data.score));
    scores.set(edge.data.target, Math.max(scores.get(edge.data.target) ?? 0, edge.data.score));
  }
  const selected = new Set(content
    .sort((left, right) => (scores.get(right.data.id) ?? 0) - (scores.get(left.data.id) ?? 0)
      || Date.parse(right.data.published_at ?? "") - Date.parse(left.data.published_at ?? "")
      || left.data.id.localeCompare(right.data.id))
    .slice(0, maxContentNodes)
    .map(node => node.data.id));
  const nodeIds = new Set(graph.nodes.filter(node => selected.has(node.data.id) || !(node.data.type === "paper" || node.data.type === "blog")).map(node => node.data.id));
  const edges = graph.edges.filter(edge => nodeIds.has(edge.data.source) && nodeIds.has(edge.data.target));
  const connectedTaxonomy = new Set(edges.flatMap(edge => [edge.data.source, edge.data.target]));
  return { nodes: graph.nodes.filter(node => selected.has(node.data.id) || connectedTaxonomy.has(node.data.id)), edges };
}

/** Build taxonomy links only for content nodes in the supplied view. */
export function buildGraph(
  items: Item[],
  taxonomy: Taxonomy,
  contentIds: ReadonlySet<string>,
  similarityEdges: readonly SimilarityEdge[] = [],
): GraphDocument {
  const selected = items.filter(item => contentIds.has(item.id));
  const nodes = selected.map(contentNode);
  const edges: GraphEdge[] = [];
  const edgeKeys = new Set<string>();

  for (const group of groups) {
    for (const entry of taxonomy[group]) {
      const connected = selected.filter(item => item[group].includes(entry.id));
      if (!connected.length) continue;
      const nodeId = taxonomyNodeId(group, entry.id);
      nodes.push({
        data: {
          id: nodeId,
          label: `${entry.name_zh} / ${entry.name_en}`,
          type: group.slice(0, -1) as GraphTaxonomyType,
          search_terms: [entry.id, entry.name_zh, entry.name_en],
          weight: 1,
        },
      });
      for (const item of connected) {
        const key = `${item.id}|${nodeId}`;
        if (edgeKeys.has(key)) continue;
        edgeKeys.add(key);
        edges.push({
          data: {
            id: `taxonomy:${key}`,
            source: item.id,
            target: nodeId,
            type: "taxonomy",
            confidence: 1,
            evidence: "canonical taxonomy label",
            generated_by: "topics.yaml",
          },
        });
      }
    }
  }

  for (const edge of similarityEdges) edges.push(similarityGraphEdge(edge));

  const neighbors = new Map(nodes.map(node => [node.data.id, new Set<string>()]));
  for (const edge of edges) {
    neighbors.get(edge.data.source)?.add(edge.data.target);
    neighbors.get(edge.data.target)?.add(edge.data.source);
  }
  for (const node of nodes) node.data.weight = Math.max(1, neighbors.get(node.data.id)?.size ?? 0);
  return { nodes, edges };
}

function fnv1a(value: string): number {
  let hash = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}

function byteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function shardId(values: Array<{ id: string }>): string {
  const first = values[0]?.id ?? "empty";
  const last = values.at(-1)?.id ?? "empty";
  return `${fnv1a(`${first}|${last}`).toString(16).padStart(8, "0")}-${values.length}`;
}

function partitionPayload<T extends { id: string }, P>(
  values: T[],
  targetBytes: number,
  build: (chunk: T[]) => P,
): P[] {
  const ordered = [...values].sort((left, right) => fnv1a(left.id) - fnv1a(right.id) || left.id.localeCompare(right.id));
  const shards: P[] = [];
  let current: T[] = [];
  for (const value of ordered) {
    const candidate = [...current, value];
    if (current.length && byteLength(build(candidate)) > targetBytes) {
      shards.push(build(current));
      current = [value];
    } else {
      current = candidate;
    }
  }
  if (current.length) shards.push(build(current));
  return shards;
}

function similarityNeighbors(similarity: SimilarityArtifact): Map<string, SimilarityEdge[]> {
  const neighbors = new Map<string, SimilarityEdge[]>();
  for (const edge of similarity.edges) {
    const left = neighbors.get(edge.source_id) ?? [];
    left.push(edge);
    neighbors.set(edge.source_id, left);
    const right = neighbors.get(edge.target_id) ?? [];
    right.push(edge);
    neighbors.set(edge.target_id, right);
  }
  for (const [id, values] of neighbors) {
    values.sort((left, right) => {
      const leftNeighbor = left.source_id === id ? left.target_id : left.source_id;
      const rightNeighbor = right.source_id === id ? right.target_id : right.source_id;
      return right.score - left.score || leftNeighbor.localeCompare(rightNeighbor);
    });
  }
  return neighbors;
}

function makeShards(
  ids: string[],
  items: Item[],
  taxonomy: Taxonomy,
  targetBytes: number,
  ownedEdges: ReadonlyMap<string, readonly SimilarityEdge[]> = new Map(),
): GraphShard[] {
  const itemById = new Map(items.map(item => [item.id, item]));
  const records = [...new Set(ids)].filter(id => itemById.has(id)).map(id => ({ id }));
  return partitionPayload(records, targetBytes, chunk => {
    const contentIds = new Set(chunk.map(value => value.id));
    const chunkItems = chunk.map(value => itemById.get(value.id)!);
    const edges = chunk.flatMap(value => [...(ownedEdges.get(value.id) ?? [])]);
    return {
      id: shardId(chunk),
      content_ids: [...contentIds].sort(),
      document: buildGraph(chunkItems, taxonomy, contentIds, edges),
    };
  });
}

function makeAdjacencyShards(
  items: Item[],
  similarity: SimilarityArtifact,
  targetBytes: number,
): Array<{ id: string; entries: Array<{ id: string; neighbors: SimilarityEdge[] }> }> {
  const neighbors = similarityNeighbors(similarity);
  const entries = items
    .map(item => ({ id: item.id, neighbors: neighbors.get(item.id) ?? [] }))
    .sort((left, right) => left.id.localeCompare(right.id));
  return partitionPayload(entries, targetBytes, chunk => ({ id: shardId(chunk), entries: chunk }));
}

function shardUrls(kind: "nodes" | "adjacency", values: Array<{ id: string }>): Record<string, string> {
  return Object.fromEntries(values.map(value => [value.id, sitePath(`graph-shards/${kind}/${value.id}.json`)]));
}

export function buildGraphAssets(
  items: Item[],
  taxonomy: Taxonomy,
  similarity: SimilarityArtifact,
  snapshot: BuildConfigSnapshot,
  latestDigest: { papers: Array<{ item_id: string }>; blogs: Array<{ item_id: string }> },
  runId: string,
): GraphAssets {
  const itemIds = new Set(items.map(item => item.id));
  const d0Ids = [...latestDigest.papers, ...latestDigest.blogs].map(entry => entry.item_id).filter(id => itemIds.has(id));
  const neighbors = similarityNeighbors(similarity);
  const d1Ids = [...new Set(d0Ids.flatMap(id => (neighbors.get(id) ?? []).map(edge => edge.source_id === id ? edge.target_id : edge.source_id)))]
    .filter(id => !d0Ids.includes(id) && itemIds.has(id))
    .sort();
  const allIds = [...itemIds].sort();
  const targetBytes = snapshot.graph_shard_target_bytes;
  const initialContentIds = new Set([...d0Ids, ...d1Ids]);
  const d0Set = new Set(d0Ids);
  const d1Set = new Set(d1Ids);
  const d0Edges = new Map<string, SimilarityEdge[]>();
  const d1Edges = new Map<string, SimilarityEdge[]>();
  for (const edge of similarity.edges) {
    if (!initialContentIds.has(edge.source_id) || !initialContentIds.has(edge.target_id)) continue;
    const owner = d1Set.has(edge.source_id)
      ? edge.source_id
      : d1Set.has(edge.target_id)
        ? edge.target_id
        : edge.source_id;
    const destination = d1Set.has(owner) ? d1Edges : d0Edges;
    const owned = destination.get(owner) ?? [];
    owned.push(edge);
    destination.set(owner, owned);
  }
  const nodeShards = makeShards(allIds, items, taxonomy, targetBytes);
  const d0Shards = makeShards([...d0Set], items, taxonomy, targetBytes, d0Edges);
  const d1Shards = makeShards([...d1Set], items, taxonomy, targetBytes, d1Edges);
  const adjacencyShards = makeAdjacencyShards(items, similarity, targetBytes);
  const nodeShardById = new Map(nodeShards.flatMap(shard => shard.content_ids.map(id => [id, shard.id] as const)));
  const adjacencyShardById = new Map(adjacencyShards.flatMap(shard => shard.entries.map(entry => [entry.id, shard.id] as const)));
  const index: GraphIndexDocument = {
    schema_version: "1",
    nodes: items.map(contentNode).map(node => ({
      id: node.data.id,
      type: node.data.type as GraphContentType,
      label: node.data.label,
      href: node.data.href!,
      summary: node.data.summary!,
      published_at: node.data.published_at!,
      tags: node.data.tags ?? [],
      search_terms: node.data.search_terms ?? [],
      node_shard: nodeShardById.get(node.data.id)!,
      adjacency_shard: adjacencyShardById.get(node.data.id)!,
    })).sort((left, right) => left.id.localeCompare(right.id)),
  };
  return {
    index,
    d0_shards: d0Shards,
    d1_shards: d1Shards,
    node_shards: nodeShards,
    adjacency_shards: adjacencyShards,
    manifest: {
      schema_version: "1",
      run_id: runId,
      index_url: sitePath("graph-index.json"),
      initial: {
        d0_urls: d0Shards.map(shard => sitePath(`graph-shards/d0/${shard.id}.json`)),
        d1_urls: d1Shards.map(shard => sitePath(`graph-shards/d1/${shard.id}.json`)),
        d0_content_ids: [...d0Set].sort(),
        d1_content_ids: d1Ids,
        max_content_nodes: snapshot.graph_initial_content_nodes,
      },
      node_shards: shardUrls("nodes", nodeShards),
      adjacency_shards: shardUrls("adjacency", adjacencyShards),
      total_content_nodes: items.length,
      total_similarity_edges: similarity.edges.length,
    },
  };
}

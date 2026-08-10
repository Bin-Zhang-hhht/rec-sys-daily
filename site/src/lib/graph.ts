import type { Item, Taxonomy } from "./data";

export type GraphNode = {
  data: {
    id: string;
    label: string;
    type: "paper" | "article" | "target" | "scenario" | "task" | "method";
    href?: string;
    summary?: string;
    published_at?: string;
    tags?: string[];
  };
};

export type GraphEdge = {
  data: {
    id: string;
    source: string;
    target: string;
    type: string;
    confidence: number;
    evidence: string;
    generated_by: string;
  };
};

export type GraphDocument = { nodes: GraphNode[]; edges: GraphEdge[] };

const groups = ["targets", "scenarios", "tasks", "methods"] as const;

function taxonomyNodeId(group: string, id: string) {
  return `${group.slice(0, -1)}:${id}`;
}

function contentNodeId(item: Item) {
  return item.id;
}

function contentScore(item: Item, now: number) {
  const ageDays = Math.max(0, (now - Date.parse(item.published_at)) / 86_400_000);
  const recentBoost = ageDays <= 90 ? 1 : 0;
  return recentBoost * 2 + item.relevance_score + (item.graph_relations?.length ?? 0) * 0.03;
}

export function buildGraph(items: Item[], taxonomy: Taxonomy, now = Date.now()): GraphDocument {
  const visible = [...items]
    .sort((a, b) => contentScore(b, now) - contentScore(a, now) || Date.parse(b.published_at) - Date.parse(a.published_at) || a.id.localeCompare(b.id))
    .slice(0, 80);
  const visibleIds = new Set(visible.map(contentNodeId));
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const edgeKeys = new Set<string>();

  for (const item of visible) {
    const tags = groups.flatMap(group => item[group] ?? []);
    nodes.push({ data: {
      id: contentNodeId(item), label: item.title, type: item.kind === "paper" ? "paper" : "article",
      href: item.kind === "paper" ? `/papers/${item.id}/` : `/articles/${item.id}/`, summary: item.summary_zh,
      published_at: item.published_at, tags,
    } });
  }

  for (const group of groups) {
    const entries = taxonomy[group];
    const used = new Set(visible.flatMap(item => item[group] ?? []));
    for (const entry of entries) {
      if (!used.has(entry.id)) continue;
      const nodeId = taxonomyNodeId(group, entry.id);
      nodes.push({ data: { id: nodeId, label: `${entry.name_zh} / ${entry.name_en}`, type: group.slice(0, -1) as GraphNode["data"]["type"] } });
      for (const item of visible.filter(value => (value[group] ?? []).includes(entry.id))) {
        const key = `${item.id}|${nodeId}`;
        if (edgeKeys.has(key)) continue;
        edgeKeys.add(key);
        edges.push({ data: { id: `taxonomy:${key}`, source: item.id, target: nodeId, type: group.slice(0, -1), confidence: 1, evidence: "canonical taxonomy label", generated_by: "topics.yaml" } });
      }
    }
  }

  const taxonomyIds = new Map<string, string>();
  for (const group of groups) for (const entry of taxonomy[group]) taxonomyIds.set(entry.id, taxonomyNodeId(group, entry.id));
  for (const item of visible) {
    for (const relation of item.graph_relations ?? []) {
      if (relation.confidence < 0.5) continue;
      const target = visibleIds.has(relation.target_id) ? relation.target_id : taxonomyIds.get(relation.target_id);
      if (!target || target === item.id) continue;
      const key = `${item.id}|${target}|${relation.type}`;
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({ data: { id: `relation:${key}`, source: item.id, target, type: relation.type, confidence: relation.confidence, evidence: relation.evidence, generated_by: relation.generated_by } });
    }
  }
  return { nodes, edges };
}

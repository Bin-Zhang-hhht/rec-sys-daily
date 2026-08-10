import fs from "node:fs";
import path from "node:path";

export type TaxonomyEntry = { id: string; name_zh: string; name_en: string };
export type Taxonomy = Record<"targets" | "scenarios" | "tasks" | "methods", TaxonomyEntry[]>;
export type Item = {
  id: string; kind: "paper" | "blog"; title: string; summary_zh: string; source: string; url: string;
  published_at: string; authors: string[]; targets: string[]; scenarios: string[]; tasks: string[]; methods: string[];
  relevance_score: number; deep_reading: Record<string, any>; excerpt?: string;
  graph_relations?: { type: string; target_id: string; confidence: number; evidence: string; generated_by: string }[];
};
export type DigestEntry = { item_id: string; recommendation_reason_zh: string; rank: number };
export type Digest = { date: string; papers: DigestEntry[]; blogs: DigestEntry[] };

const defaultRoot = "/workspace/publish-bundle";

function bundleRoot(root?: string) {
  return root ?? process.env.PUBLISH_BUNDLE_DIR ?? defaultRoot;
}

function readJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

function itemFiles(root: string): string[] {
  const output: string[] = [];
  for (const kind of ["papers", "blogs"]) {
    const base = path.join(root, "pending-data", "items", kind);
    if (!fs.existsSync(base)) continue;
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const file = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(file); else if (entry.name.endsWith(".json")) output.push(file);
      }
    };
    walk(base);
  }
  return output;
}

export function loadBundle(root?: string) {
  const base = bundleRoot(root);
  const taxonomy = readJson<Taxonomy>(path.join(base, "taxonomy.json"));
  const items = itemFiles(base).map(file => readJson<Item>(file));
  const digestFiles: string[] = [];
  const digestBase = path.join(base, "pending-data", "digests");
  if (fs.existsSync(digestBase)) {
    for (const year of fs.readdirSync(digestBase)) {
      const yearDir = path.join(digestBase, year);
      for (const month of fs.readdirSync(yearDir)) {
        const monthDir = path.join(yearDir, month);
        for (const file of fs.readdirSync(monthDir)) if (file.endsWith(".json")) digestFiles.push(path.join(monthDir, file));
      }
    }
  }
  const digests = digestFiles.sort().map(file => readJson<Digest>(file));
  const latestDigest = digests.at(-1) ?? { date: "", papers: [], blogs: [] };
  const byId = new Map(items.map(item => [item.id, item]));
  return { root: base, taxonomy, items, byId, digests, latestDigest };
}

export function taxonomyName(taxonomy: Taxonomy, group: keyof Taxonomy, id: string) {
  return taxonomy[group].find(entry => entry.id === id);
}

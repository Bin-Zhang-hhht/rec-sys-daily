import fs from "node:fs";
import path from "node:path";

export const taxonomyGroups = ["targets", "scenarios", "tasks", "methods"] as const;
export type TaxonomyGroup = (typeof taxonomyGroups)[number];
export type TaxonomyEntry = { id: string; name_zh: string; name_en: string };
export type Taxonomy = Record<TaxonomyGroup, TaxonomyEntry[]>;
export type LlmMetadata = { model: string; generated_at: string; degraded: boolean };
export type PaperReading = {
  analysis_basis: "mineru_full_text" | "abstract_fallback";
  problem_zh: string | null; contributions_zh: string[]; method_zh: string | null;
  experiments: { datasets: string[]; baselines: string[]; metrics: string[]; findings_zh: string[] };
  limitations_zh: string[]; business_implications_zh: string[];
  evidence_refs: { section: string; page: number }[];
};
export type BlogReading = {
  analysis_basis: "rss_full_content" | "article_html" | "excerpt_fallback";
  system_context_zh: string | null; architecture_zh: string | null; implementation_zh: string | null;
  production_constraints_zh: string[]; tradeoffs_zh: string[]; results_zh: string[];
  lessons_zh: string[]; limitations_zh: string[]; business_implications_zh: string[];
  evidence_refs: { heading?: string | null; section?: string | null }[];
};
type ItemBase = {
  id: string; title: string; summary_zh: string; source: string; url: string;
  published_at: string; authors: string[]; targets: string[]; scenarios: string[]; tasks: string[]; methods: string[];
  relevance_score: number; final_score: number; llm: LlmMetadata | null;
};
export type PaperItem = ItemBase & { kind: "paper"; abstract: string; arxiv_id: string; doi: string | null; deep_reading: PaperReading };
export type BlogItem = ItemBase & { kind: "blog"; deep_reading: BlogReading };
export type Item = PaperItem | BlogItem;
export type DigestEntry = { item_id: string; recommendation_reason_zh: string; rank: number };
export type Digest = { date: string; papers: DigestEntry[]; blogs: DigestEntry[] };
export type BuildConfigSnapshot = {
  graph_initial_content_nodes: number;
  graph_shard_target_bytes: number;
  minimum_final_score: number;
  minimum_metadata_relevance_score: number;
  target_item_bytes: number;
  max_item_bytes: number;
  max_blog_excerpt_chars: number;
  warn_repository_data_mb: number;
  warn_pages_artifact_mb: number;
  fail_pages_artifact_mb: number;
};
export type RunSourceStatus = { source_id: string; success: boolean; warning: string | null };
export type RunStageReport = {
  sources: RunSourceStatus[];
  collected_paper_candidates: number;
  collected_blog_candidates: number;
  prefilter_paper_candidates: number;
  prefilter_blog_candidates: number;
  shortlist_paper_candidates: number;
  shortlist_blog_candidates: number;
  metadata_llm_calls: number;
  metadata_llm_success_rate: number;
  metadata_degraded_count: number;
  metadata_label_rejections: number;
  metadata_relevance_rejections: number;
};
export type RunReport = { run_id: string; config_snapshot: BuildConfigSnapshot; stage_report: RunStageReport };
export type SimilarityModel = { library: "fastembed"; version: "0.8.0"; name: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"; dimension: 384; normalized: true };
export type SimilarityParameters = {
  max_input_tokens: 128; title_tokens: 32; abstract_tokens: 64; summary_tokens: 24; separator_tokens: 8;
  top_k: number; min_cosine: number; mutual_top_k: true;
};
export type SimilarityTokenCounts = { id: string; title: number; abstract: number; summary_zh: number; total: number };
export type SimilarityEdge = { source_id: string; target_id: string; score: number; source_rank: number; target_rank: number };
export type SimilarityArtifact = {
  run_id: string; schema_version: "1"; model: SimilarityModel; parameters: SimilarityParameters;
  items_considered: number; encoded_items: number; token_counts: SimilarityTokenCounts[]; edges: SimilarityEdge[];
};

const defaultRoot = "/workspace/publish-bundle";
const routeIdPattern = /^[A-Za-z0-9._~-]+$/;

function bundleRoot(root?: string) {
  return root ?? process.env.PUBLISH_BUNDLE_DIR ?? defaultRoot;
}

function fail(context: string, message: string): never {
  throw new Error(`${context}: ${message}`);
}

function record(value: unknown, context: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(context, "expected an object");
  return value as Record<string, unknown>;
}

function text(value: unknown, context: string): string {
  if (typeof value !== "string" || !value.trim()) fail(context, "expected a non-empty string");
  return value;
}

function optionalText(value: unknown, context: string): string | null {
  return value == null ? null : text(value, context);
}

function strings(value: unknown, context: string, allowEmpty = true): string[] {
  if (!Array.isArray(value) || value.some(entry => typeof entry !== "string" || !entry.trim())) fail(context, "expected an array of non-empty strings");
  if (!allowEmpty && value.length === 0) fail(context, "must not be empty");
  if (new Set(value).size !== value.length) fail(context, "contains duplicate values");
  return value as string[];
}

function optionalStrings(value: unknown, context: string): string[] {
  return value == null ? [] : strings(value, context);
}

function score(value: unknown, context: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) fail(context, "expected a number between 0 and 1");
  return value;
}

function nonNegativeInteger(value: unknown, context: string): number {
  if (!Number.isInteger(value) || (value as number) < 0) fail(context, "expected a non-negative integer");
  return value as number;
}

function dateTime(value: unknown, context: string): string {
  const output = text(value, context);
  if (!Number.isFinite(Date.parse(output))) fail(context, "expected a valid timestamp");
  return output;
}

function url(value: unknown, context: string): string {
  const output = text(value, context);
  let parsed: URL;
  try { parsed = new URL(output); } catch { fail(context, "expected an absolute URL"); }
  if (!(["http:", "https:"] as string[]).includes(parsed!.protocol)) fail(context, "expected an http(s) URL");
  return output;
}

function readJson(file: string): unknown {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { throw new Error(`cannot read JSON ${file}: ${error instanceof Error ? error.message : "unknown error"}`); }
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

function runReportFiles(root: string): string[] {
  const base = path.join(root, "pending-data", "runs");
  if (!fs.existsSync(base)) return [];
  const output: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(file); else if (entry.name.endsWith(".json")) output.push(file);
    }
  };
  walk(base);
  return output.sort();
}

function validateTaxonomy(value: unknown): Taxonomy {
  const raw = record(value, "taxonomy");
  const output = {} as Taxonomy;
  const allIds = new Set<string>();
  for (const group of taxonomyGroups) {
    const entries = raw[group];
    if (!Array.isArray(entries)) fail(`taxonomy.${group}`, "expected an array");
    output[group] = entries.map((entry, index) => {
      const data = record(entry, `taxonomy.${group}[${index}]`);
      const id = text(data.id, `taxonomy.${group}[${index}].id`);
      if (!routeIdPattern.test(id)) fail(`taxonomy.${group}[${index}].id`, "contains unsafe characters");
      if (allIds.has(id)) fail("taxonomy", `duplicate id ${id}`);
      allIds.add(id);
      return { id, name_zh: text(data.name_zh, `taxonomy.${group}[${index}].name_zh`), name_en: text(data.name_en, `taxonomy.${group}[${index}].name_en`) };
    });
  }
  return output;
}

function validateReading(value: unknown, kind: "paper" | "blog", context: string): PaperReading | BlogReading {
  const raw = record(value, context);
  const basis = text(raw.analysis_basis, `${context}.analysis_basis`);
  if (kind === "paper") {
    if (basis !== "mineru_full_text" && basis !== "abstract_fallback") fail(`${context}.analysis_basis`, "invalid paper basis");
    const experiments = raw.experiments == null ? {} : record(raw.experiments, `${context}.experiments`);
    const evidenceRefs = raw.evidence_refs == null ? [] : raw.evidence_refs;
    if (!Array.isArray(evidenceRefs)) fail(`${context}.evidence_refs`, "expected an array");
    const refs = evidenceRefs.map((value, index) => {
      const ref = record(value, `${context}.evidence_refs[${index}]`);
      if (!Number.isInteger(ref.page) || (ref.page as number) < 1) fail(`${context}.evidence_refs[${index}].page`, "expected a positive integer");
      return { section: text(ref.section, `${context}.evidence_refs[${index}].section`), page: ref.page as number };
    });
    return {
      analysis_basis: basis,
      problem_zh: optionalText(raw.problem_zh, `${context}.problem_zh`),
      contributions_zh: optionalStrings(raw.contributions_zh, `${context}.contributions_zh`),
      method_zh: optionalText(raw.method_zh, `${context}.method_zh`),
      experiments: {
        datasets: optionalStrings(experiments.datasets, `${context}.experiments.datasets`),
        baselines: optionalStrings(experiments.baselines, `${context}.experiments.baselines`),
        metrics: optionalStrings(experiments.metrics, `${context}.experiments.metrics`),
        findings_zh: optionalStrings(experiments.findings_zh, `${context}.experiments.findings_zh`),
      },
      limitations_zh: optionalStrings(raw.limitations_zh, `${context}.limitations_zh`),
      business_implications_zh: optionalStrings(raw.business_implications_zh, `${context}.business_implications_zh`),
      evidence_refs: refs,
    };
  }
  if (!["rss_full_content", "article_html", "excerpt_fallback"].includes(basis)) fail(`${context}.analysis_basis`, "invalid blog basis");
  const evidenceRefs = raw.evidence_refs == null ? [] : raw.evidence_refs;
  if (!Array.isArray(evidenceRefs)) fail(`${context}.evidence_refs`, "expected an array");
  const refs = evidenceRefs.map((value, index) => {
    const ref = record(value, `${context}.evidence_refs[${index}]`);
    const heading = ref.heading == null ? null : text(ref.heading, `${context}.evidence_refs[${index}].heading`);
    const section = ref.section == null ? null : text(ref.section, `${context}.evidence_refs[${index}].section`);
    if (!heading && !section) fail(`${context}.evidence_refs[${index}]`, "heading or section is required");
    return { heading, section };
  });
  return {
    analysis_basis: basis as BlogReading["analysis_basis"],
    system_context_zh: optionalText(raw.system_context_zh, `${context}.system_context_zh`),
    architecture_zh: optionalText(raw.architecture_zh, `${context}.architecture_zh`),
    implementation_zh: optionalText(raw.implementation_zh, `${context}.implementation_zh`),
    production_constraints_zh: optionalStrings(raw.production_constraints_zh, `${context}.production_constraints_zh`),
    tradeoffs_zh: optionalStrings(raw.tradeoffs_zh, `${context}.tradeoffs_zh`),
    results_zh: optionalStrings(raw.results_zh, `${context}.results_zh`),
    lessons_zh: optionalStrings(raw.lessons_zh, `${context}.lessons_zh`),
    limitations_zh: optionalStrings(raw.limitations_zh, `${context}.limitations_zh`),
    business_implications_zh: optionalStrings(raw.business_implications_zh, `${context}.business_implications_zh`),
    evidence_refs: refs,
  };
}

function validateItem(value: unknown, taxonomy: Taxonomy, context: string): Item {
  const raw = record(value, context);
  if (raw.kind !== "paper" && raw.kind !== "blog") fail(`${context}.kind`, "expected paper or blog");
  const kind = raw.kind;
  const id = text(raw.id, `${context}.id`);
  if (!routeIdPattern.test(id)) fail(`${context}.id`, "contains unsafe route characters");
  const tagValues = {} as Record<TaxonomyGroup, string[]>;
  for (const group of taxonomyGroups) {
    const values = strings(raw[group], `${context}.${group}`);
    const allowed = new Set(taxonomy[group].map(entry => entry.id));
    const unknown = values.find(value => !allowed.has(value));
    if (unknown) fail(`${context}.${group}`, `unknown taxonomy id ${unknown}`);
    tagValues[group] = values;
  }
  const llm = raw.llm == null ? null : record(raw.llm, `${context}.llm`);
  const common = {
    id, title: text(raw.title, `${context}.title`), summary_zh: text(raw.summary_zh, `${context}.summary_zh`),
    source: text(raw.source, `${context}.source`), url: url(raw.url, `${context}.url`), published_at: dateTime(raw.published_at, `${context}.published_at`),
    authors: strings(raw.authors, `${context}.authors`), ...tagValues,
    relevance_score: score(raw.relevance_score, `${context}.relevance_score`), final_score: score(raw.final_score, `${context}.final_score`),
    llm: llm ? { model: text(llm.model, `${context}.llm.model`), generated_at: dateTime(llm.generated_at, `${context}.llm.generated_at`), degraded: typeof llm.degraded === "boolean" ? llm.degraded : fail(`${context}.llm.degraded`, "expected a boolean") } : null,
  };
  if (kind === "paper") return {
    ...common, kind, abstract: text(raw.abstract, `${context}.abstract`), arxiv_id: text(raw.arxiv_id, `${context}.arxiv_id`),
    doi: raw.doi == null ? null : text(raw.doi, `${context}.doi`), deep_reading: validateReading(raw.deep_reading, kind, `${context}.deep_reading`) as PaperReading,
  };
  return { ...common, kind, deep_reading: validateReading(raw.deep_reading, kind, `${context}.deep_reading`) as BlogReading };
}

function validateDigest(value: unknown, context: string): Digest {
  const raw = record(value, context);
  const date = text(raw.date, `${context}.date`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(`${date}T00:00:00Z`))) fail(`${context}.date`, "invalid date");
  const parseEntries = (value: unknown, group: "papers" | "blogs") => {
    if (!Array.isArray(value)) fail(`${context}.${group}`, "expected an array");
    const ids = new Set<string>();
    return value.map((entry, index) => {
      const data = record(entry, `${context}.${group}[${index}]`);
      const itemId = text(data.item_id, `${context}.${group}[${index}].item_id`);
      if (ids.has(itemId)) fail(`${context}.${group}`, `duplicate item ${itemId}`);
      ids.add(itemId);
      if (!Number.isInteger(data.rank) || (data.rank as number) < 1) fail(`${context}.${group}[${index}].rank`, "expected a positive integer");
      return { item_id: itemId, recommendation_reason_zh: text(data.recommendation_reason_zh, `${context}.${group}[${index}].recommendation_reason_zh`), rank: data.rank as number };
    });
  };
  return { date, papers: parseEntries(raw.papers, "papers"), blogs: parseEntries(raw.blogs, "blogs") };
}

function validateSnapshot(value: unknown): BuildConfigSnapshot {
  const snapshot = record(value, "run report config_snapshot");
  const fields: (keyof BuildConfigSnapshot)[] = [
    "graph_initial_content_nodes", "graph_shard_target_bytes", "target_item_bytes", "max_item_bytes",
    "max_blog_excerpt_chars", "warn_repository_data_mb", "warn_pages_artifact_mb", "fail_pages_artifact_mb",
  ];
  for (const field of fields) {
    if (typeof snapshot[field] !== "number" || !Number.isFinite(snapshot[field]) || snapshot[field] <= 0) {
      throw new Error(`run report config_snapshot.${field} is invalid`);
    }
  }
  if (!Number.isInteger(snapshot.graph_initial_content_nodes) || snapshot.graph_initial_content_nodes > 180) {
    throw new Error("run report config_snapshot.graph_initial_content_nodes must be between 1 and 180");
  }
  const graphShardTargetBytes = snapshot.graph_shard_target_bytes as number;
  if (!Number.isInteger(graphShardTargetBytes) || graphShardTargetBytes < 65_536 || graphShardTargetBytes > 131_072) {
    throw new Error("run report config_snapshot.graph_shard_target_bytes must be between 64 KiB and 128 KiB");
  }
  score(snapshot.minimum_final_score, "run report config_snapshot.minimum_final_score");
  score(snapshot.minimum_metadata_relevance_score, "run report config_snapshot.minimum_metadata_relevance_score");
  return snapshot as BuildConfigSnapshot;
}

function validateStageReport(value: unknown): RunStageReport {
  const stageReport = record(value, "RunReport.stage_report");
  if (!Array.isArray(stageReport.sources)) fail("RunReport.stage_report.sources", "expected an array");
  const sourceIds = new Set<string>();
  const sources = stageReport.sources.map((value, index) => {
    const context = `RunReport.stage_report.sources[${index}]`;
    const source = record(value, context);
    const sourceId = text(source.source_id, `${context}.source_id`);
    if (sourceIds.has(sourceId)) fail("RunReport.stage_report.sources", `duplicate source_id ${sourceId}`);
    sourceIds.add(sourceId);
    if (typeof source.success !== "boolean") fail(`${context}.success`, "expected a boolean");
    if (source.warning !== null && typeof source.warning !== "string") fail(`${context}.warning`, "expected a string or null");
    return { source_id: sourceId, success: source.success, warning: source.warning };
  });
  return {
    sources,
    collected_paper_candidates: nonNegativeInteger(stageReport.collected_paper_candidates, "RunReport.stage_report.collected_paper_candidates"),
    collected_blog_candidates: nonNegativeInteger(stageReport.collected_blog_candidates, "RunReport.stage_report.collected_blog_candidates"),
    prefilter_paper_candidates: nonNegativeInteger(stageReport.prefilter_paper_candidates, "RunReport.stage_report.prefilter_paper_candidates"),
    prefilter_blog_candidates: nonNegativeInteger(stageReport.prefilter_blog_candidates, "RunReport.stage_report.prefilter_blog_candidates"),
    shortlist_paper_candidates: nonNegativeInteger(stageReport.shortlist_paper_candidates, "RunReport.stage_report.shortlist_paper_candidates"),
    shortlist_blog_candidates: nonNegativeInteger(stageReport.shortlist_blog_candidates, "RunReport.stage_report.shortlist_blog_candidates"),
    metadata_llm_calls: nonNegativeInteger(stageReport.metadata_llm_calls, "RunReport.stage_report.metadata_llm_calls"),
    metadata_llm_success_rate: score(stageReport.metadata_llm_success_rate, "RunReport.stage_report.metadata_llm_success_rate"),
    metadata_degraded_count: nonNegativeInteger(stageReport.metadata_degraded_count, "RunReport.stage_report.metadata_degraded_count"),
    metadata_label_rejections: nonNegativeInteger(stageReport.metadata_label_rejections, "RunReport.stage_report.metadata_label_rejections"),
    metadata_relevance_rejections: nonNegativeInteger(stageReport.metadata_relevance_rejections, "RunReport.stage_report.metadata_relevance_rejections"),
  };
}

export function loadBundle(root?: string) {
  const base = bundleRoot(root);
  const bundleManifest = record(readJson(path.join(base, "manifest.json")), "publish bundle manifest");
  const manifestRunId = text(bundleManifest.run_id, "publish bundle manifest.run_id");
  if (bundleManifest.schema_version !== "1") fail("publish bundle manifest.schema_version", "expected schema version 1");
  const taxonomy = validateTaxonomy(readJson(path.join(base, "taxonomy.json")));
  const items = itemFiles(base).map(file => validateItem(readJson(file), taxonomy, path.relative(base, file)));
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
  const digests = digestFiles.sort().map(file => validateDigest(readJson(file), path.relative(base, file)));
  const latestDigest = digests.at(-1) ?? { date: "", papers: [], blogs: [] };
  const reportFiles = runReportFiles(base);
  if (!reportFiles.length) throw new Error("publish bundle has no RunReport");
  const matchingReportFiles = reportFiles.filter(file => path.basename(file, ".json") === manifestRunId);
  if (matchingReportFiles.length !== 1) fail("RunReport", "publish bundle must contain exactly one report for its manifest run_id");
  const reportValue = record(readJson(matchingReportFiles[0]), "RunReport");
  const runReport: RunReport = {
    run_id: text(reportValue.run_id, "RunReport.run_id"),
    config_snapshot: validateSnapshot(reportValue.config_snapshot),
    stage_report: validateStageReport(reportValue.stage_report),
  };
  if (runReport.run_id !== manifestRunId) fail("RunReport.run_id", "does not match publish bundle manifest");
  const buildConfig = validateSnapshot(runReport.config_snapshot);
  const byId = new Map<string, Item>();
  for (const item of items) {
    if (byId.has(item.id)) fail("items", `duplicate id ${item.id}`);
    byId.set(item.id, item);
  }
  for (const digest of digests) {
    for (const [entries, kind] of [[digest.papers, "paper"], [digest.blogs, "blog"]] as const) {
      for (const entry of entries) {
        const item = byId.get(entry.item_id);
        if (!item) fail(`digest ${digest.date}`, `unknown item ${entry.item_id}`);
        if (item.kind !== kind) fail(`digest ${digest.date}`, `${entry.item_id} is not a ${kind}`);
      }
    }
  }
  return { root: base, taxonomy, items, byId, digests, latestDigest, runReport, buildConfig };
}

function similarityArtifactPath(pathOverride?: string): string {
  return pathOverride ?? process.env.SIMILARITY_ARTIFACT_PATH ?? "/workspace/work/similarity/similarity.json";
}

export function loadSimilarityArtifact(items: Item[], runId: string, pathOverride?: string): SimilarityArtifact {
  const file = similarityArtifactPath(pathOverride);
  const artifactManifest = record(readJson(path.join(path.dirname(file), "manifest.json")), "similarity artifact manifest");
  if (artifactManifest.run_id !== runId || artifactManifest.schema_version !== "1") {
    fail("similarity artifact manifest", "run_id or schema_version does not match publish bundle");
  }
  let raw: unknown;
  try { raw = JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { throw new Error(`cannot read similarity artifact ${file}: ${error instanceof Error ? error.message : "unknown error"}`); }
  const value = record(raw, "similarity artifact");
  if (value.run_id !== runId || value.schema_version !== "1") fail("similarity artifact", "run_id or schema_version does not match publish bundle");
  const model = record(value.model, "similarity artifact.model");
  if (model.library !== "fastembed" || model.version !== "0.8.0" || model.name !== "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" || model.dimension !== 384 || model.normalized !== true) fail("similarity artifact.model", "unsupported model contract");
  const parameters = record(value.parameters, "similarity artifact.parameters");
  if (
    parameters.max_input_tokens !== 128
    || parameters.title_tokens !== 32
    || parameters.abstract_tokens !== 64
    || parameters.summary_tokens !== 24
    || parameters.separator_tokens !== 8
    || parameters.top_k !== 5
    || parameters.min_cosine !== 0.72
    || parameters.mutual_top_k !== true
  ) fail("similarity artifact.parameters", "unsupported similarity contract");
  const expectedIds = new Set(items.map(item => item.id));
  if (value.items_considered !== expectedIds.size || value.encoded_items !== expectedIds.size) fail("similarity artifact", "does not cover all canonical items");
  if (!Array.isArray(value.token_counts) || value.token_counts.length !== expectedIds.size) fail("similarity artifact.token_counts", "does not cover all canonical items");
  const tokenIds = new Set<string>();
  const tokenCounts = value.token_counts.map((entry, index) => {
    const data = record(entry, `similarity artifact.token_counts[${index}]`);
    const id = text(data.id, `similarity artifact.token_counts[${index}].id`);
    if (tokenIds.has(id) || !expectedIds.has(id)) fail("similarity artifact.token_counts", `unknown or duplicate item ${id}`);
    tokenIds.add(id);
    for (const name of ["title", "abstract", "summary_zh", "total"]) {
      if (!Number.isInteger(data[name]) || (data[name] as number) < 0) fail(`similarity artifact.token_counts[${index}].${name}`, "expected a non-negative integer");
    }
    if ((data.title as number) > 32 || (data.abstract as number) > 64 || (data.summary_zh as number) > 24 || (data.total as number) > 128) fail("similarity artifact.token_counts", `token budget exceeded for ${id}`);
    return { id, title: data.title as number, abstract: data.abstract as number, summary_zh: data.summary_zh as number, total: data.total as number };
  });
  if (tokenIds.size !== expectedIds.size) fail("similarity artifact.token_counts", "does not cover all canonical items");
  for (let index = 1; index < tokenCounts.length; index += 1) {
    if (tokenCounts[index - 1].id >= tokenCounts[index].id) fail("similarity artifact.token_counts", "must use stable item ID ordering");
  }
  if (!Array.isArray(value.edges)) fail("similarity artifact.edges", "expected an array");
  const ranksByItem = new Map<string, Set<number>>();
  const scoresByItem = new Map<string, Array<{ rank: number; score: number }>>();
  const edges = value.edges.map((entry, index) => {
    const data = record(entry, `similarity artifact.edges[${index}]`);
    const source_id = text(data.source_id, `similarity artifact.edges[${index}].source_id`);
    const target_id = text(data.target_id, `similarity artifact.edges[${index}].target_id`);
    if (source_id >= target_id || !expectedIds.has(source_id) || !expectedIds.has(target_id)) fail("similarity artifact.edges", "invalid endpoint ordering or endpoint");
    if (
      typeof data.score !== "number"
      || !Number.isFinite(data.score)
      || data.score < 0.72
      || data.score > 1
      || Number(data.score.toFixed(6)) !== data.score
      || !Number.isInteger(data.source_rank)
      || !Number.isInteger(data.target_rank)
      || (data.source_rank as number) < 1
      || (data.source_rank as number) > 5
      || (data.target_rank as number) < 1
      || (data.target_rank as number) > 5
    ) fail(`similarity artifact.edges[${index}]`, "violates score, rounding, or mutual Top-K contract");
    const sourceRank = data.source_rank as number;
    const targetRank = data.target_rank as number;
    for (const [id, rank] of [[source_id, sourceRank], [target_id, targetRank]] as const) {
      const ranks = ranksByItem.get(id) ?? new Set<number>();
      if (ranks.has(rank)) fail("similarity artifact.edges", `duplicate rank ${rank} for ${id}`);
      ranks.add(rank);
      ranksByItem.set(id, ranks);
      const rankedScores = scoresByItem.get(id) ?? [];
      rankedScores.push({ rank, score: data.score });
      scoresByItem.set(id, rankedScores);
    }
    return { source_id, target_id, score: data.score, source_rank: sourceRank, target_rank: targetRank };
  });
  const edgeKeys = new Set(edges.map(edge => `${edge.source_id}|${edge.target_id}`));
  if (edgeKeys.size !== edges.length) fail("similarity artifact.edges", "contains duplicate edges");
  for (let index = 1; index < edges.length; index += 1) {
    const previous = edges[index - 1];
    const current = edges[index];
    if (previous.source_id > current.source_id || (previous.source_id === current.source_id && previous.target_id >= current.target_id)) {
      fail("similarity artifact.edges", "must use stable source_id/target_id ordering");
    }
  }
  for (const [id, values] of scoresByItem) {
    const ranked = [...values].sort((left, right) => left.rank - right.rank);
    for (let index = 1; index < ranked.length; index += 1) {
      if (ranked[index - 1].score < ranked[index].score) fail("similarity artifact.edges", `rank/score order is invalid for ${id}`);
    }
  }
  return {
    run_id: value.run_id as string,
    schema_version: "1",
    model: { library: "fastembed", version: "0.8.0", name: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", dimension: 384, normalized: true },
    parameters: {
      max_input_tokens: 128, title_tokens: 32, abstract_tokens: 64, summary_tokens: 24, separator_tokens: 8,
      top_k: 5,
      min_cosine: 0.72,
      mutual_top_k: true,
    },
    items_considered: value.items_considered as number,
    encoded_items: value.encoded_items as number,
    token_counts: tokenCounts,
    edges,
  };
}

export function taxonomyName(taxonomy: Taxonomy, group: TaxonomyGroup, id: string): TaxonomyEntry {
  const value = taxonomy[group].find(entry => entry.id === id);
  if (!value) fail(`taxonomy.${group}`, `unknown id ${id}`);
  return value;
}

export function resolveDigestEntries(entries: DigestEntry[], byId: Map<string, Item>): { entry: DigestEntry; item: Item }[] {
  return entries.map(entry => {
    const item = byId.get(entry.item_id);
    if (!item) fail("digest", `unknown item ${entry.item_id}`);
    return { entry, item };
  });
}

export function relatedItems(item: Item, items: Item[], artifact: SimilarityArtifact, limit = 4): Item[] {
  const byId = new Map(items.map(value => [value.id, value]));
  return artifact.edges
    .filter(edge => edge.source_id === item.id || edge.target_id === item.id)
    .map(edge => ({ edge, candidate: byId.get(edge.source_id === item.id ? edge.target_id : edge.source_id) }))
    .filter((value): value is { edge: SimilarityEdge; candidate: Item } => Boolean(value.candidate))
    .sort((a, b) => b.edge.score - a.edge.score || Date.parse(b.candidate.published_at) - Date.parse(a.candidate.published_at) || a.candidate.id.localeCompare(b.candidate.id))
    .slice(0, limit)
    .map(value => value.candidate);
}

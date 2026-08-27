import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadBundle } from "../src/lib/data.ts";

const snapshot = {
  graph_initial_content_nodes: 48,
  graph_shard_target_bytes: 98_304,
  minimum_final_score: 0.5,
  minimum_metadata_relevance_score: 0.65,
  target_item_bytes: 16_384,
  max_item_bytes: 32_768,
  max_blog_excerpt_chars: 4_000,
  warn_repository_data_mb: 500,
  warn_pages_artifact_mb: 500,
  fail_pages_artifact_mb: 900,
};

const stageReport = {
  sources: [],
  collected_paper_candidates: 0,
  collected_blog_candidates: 0,
  prefilter_paper_candidates: 0,
  prefilter_blog_candidates: 0,
  shortlist_paper_candidates: 0,
  shortlist_blog_candidates: 0,
  metadata_llm_calls: 0,
  metadata_llm_success_rate: 1,
  metadata_degraded_count: 0,
  metadata_label_rejections: 0,
  metadata_relevance_rejections: 0,
};

function writeJson(file, value) {
  writeFileSync(file, JSON.stringify(value), "utf8");
}

test("loadBundle selects the RunReport named by the bundle manifest", t => {
  const root = mkdtempSync(join(tmpdir(), "recsys-site-data-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const reports = join(root, "pending-data", "runs", "2026", "08");
  mkdirSync(reports, { recursive: true });
  writeJson(join(root, "manifest.json"), { run_id: "run-a", schema_version: "1" });
  writeJson(join(root, "taxonomy.json"), { targets: [], scenarios: [], tasks: [], methods: [] });
  writeJson(join(reports, "run-a.json"), { run_id: "run-a", config_snapshot: snapshot, stage_report: stageReport });
  writeJson(join(reports, "zzz-historical.json"), { run_id: "zzz-historical", config_snapshot: snapshot, stage_report: stageReport });

  assert.equal(loadBundle(root).runReport.run_id, "run-a");
});

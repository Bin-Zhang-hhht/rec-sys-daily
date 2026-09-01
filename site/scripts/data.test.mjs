import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadBundle, relatedItems } from "../src/lib/data.ts";

const snapshot = {
  graph_initial_content_nodes: 180,
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
  writeJson(join(reports, "run-a.json"), { run_id: "run-a", config_snapshot: snapshot, stage_report: stageReport, paper_recommendations: 0, blog_recommendations: 0 });
  writeJson(join(reports, "zzz-historical.json"), { run_id: "zzz-historical", config_snapshot: snapshot, stage_report: stageReport, paper_recommendations: 2, blog_recommendations: 1 });

  const bundle = loadBundle(root);
  assert.equal(bundle.runReport.run_id, "run-a");
  assert.deepEqual(
    [bundle.runReport.paper_recommendations, bundle.runReport.blog_recommendations],
    [0, 0],
  );
});

test("relatedItems preserves score and sorts by score, date, then stable id", () => {
  const item = (id, published_at) => ({ id, kind: "blog", published_at });
  const current = item("current", "2026-08-28T00:00:00Z");
  const newer = item("newer", "2026-08-27T00:00:00Z");
  const older = item("older", "2026-08-26T00:00:00Z");
  const sameDateA = item("same-a", "2026-08-25T00:00:00Z");
  const sameDateB = item("same-b", "2026-08-25T00:00:00Z");
  const artifact = {
    edges: [
      { source_id: "current", target_id: "older", score: 0.8 },
      { source_id: "newer", target_id: "current", score: 0.9 },
      { source_id: "same-b", target_id: "current", score: 0.7 },
      { source_id: "current", target_id: "same-a", score: 0.7 },
    ],
  };
  assert.deepEqual(
    relatedItems(current, [current, newer, older, sameDateA, sameDateB], artifact).map(value => ({ id: value.item.id, score: value.score })),
    [
      { id: "newer", score: 0.9 },
      { id: "older", score: 0.8 },
      { id: "same-a", score: 0.7 },
      { id: "same-b", score: 0.7 },
    ],
  );
});

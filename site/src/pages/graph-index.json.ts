import { buildGraphAssets } from "../lib/graph";
import { loadBundle, loadSimilarityArtifact } from "../lib/data";

export function GET() {
  const bundle = loadBundle();
  const similarity = loadSimilarityArtifact(bundle.items, bundle.runReport.run_id);
  const assets = buildGraphAssets(bundle.items, bundle.taxonomy, similarity, bundle.buildConfig, bundle.latestDigest, bundle.runReport.run_id);
  return new Response(JSON.stringify(assets.index), { headers: { "content-type": "application/json; charset=utf-8" } });
}

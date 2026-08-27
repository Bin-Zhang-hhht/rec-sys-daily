import { buildGraphAssets } from "../../../lib/graph";
import { loadBundle, loadSimilarityArtifact } from "../../../lib/data";

export function getStaticPaths() {
  const bundle = loadBundle();
  const similarity = loadSimilarityArtifact(bundle.items, bundle.runReport.run_id);
  const assets = buildGraphAssets(bundle.items, bundle.taxonomy, similarity, bundle.buildConfig, bundle.latestDigest, bundle.runReport.run_id);
  return [
    ...assets.d0_shards.map(shard => ({ params: { kind: "d0", id: shard.id }, props: { payload: shard } })),
    ...assets.d1_shards.map(shard => ({ params: { kind: "d1", id: shard.id }, props: { payload: shard } })),
    ...assets.node_shards.map(shard => ({ params: { kind: "nodes", id: shard.id }, props: { payload: shard } })),
    ...assets.adjacency_shards.map(shard => ({ params: { kind: "adjacency", id: shard.id }, props: { payload: shard } })),
  ];
}

export function GET({ props }: { props: { payload: unknown } }) {
  return new Response(JSON.stringify(props.payload), { headers: { "content-type": "application/json; charset=utf-8" } });
}

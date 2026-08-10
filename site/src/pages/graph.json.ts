import { buildGraph } from "../lib/graph";
import { loadBundle } from "../lib/data";

export function GET() {
  const bundle = loadBundle();
  return new Response(JSON.stringify(buildGraph(bundle.items, bundle.taxonomy)), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

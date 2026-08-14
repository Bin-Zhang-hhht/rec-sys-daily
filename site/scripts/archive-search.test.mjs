import test from "node:test";
import assert from "node:assert/strict";
import {
  archiveSearchTerms,
  createArchiveSearchText,
  matchesArchiveSearch,
  normalizeArchiveSearchText,
} from "../src/lib/archive-search.ts";

const document = {
  title: "Graph Ranking for Large-Scale Recommendation",
  summaryZh: "面向直播间的图排序方法。",
  kind: "paper",
  digestDate: "2026-08-14",
  publishedDate: "2026-08-12T00:00:00Z",
  taxonomy: [
    { id: "room", nameZh: "房间推荐", nameEn: "Room Recommendation" },
    { id: "graph_neural_network", nameZh: "图神经网络", nameEn: "Graph Neural Network" },
  ],
};

test("archive search normalizes Unicode width, whitespace, and English case", () => {
  assert.equal(normalizeArchiveSearchText("  ＧＲＡＰＨ\n Ranking  "), "graph ranking");
  assert.deepEqual(archiveSearchTerms("  图排序   ＲＯＯＭ  "), ["图排序", "room"]);
});

test("archive search uses AND semantics for multiple terms", () => {
  const searchText = createArchiveSearchText(document);
  assert.equal(matchesArchiveSearch(searchText, "GRAPH 直播间"), true);
  assert.equal(matchesArchiveSearch(searchText, "graph 博客"), false);
  assert.equal(matchesArchiveSearch(searchText, "   "), true);
});

test("archive search text includes kind, digest and item dates", () => {
  const searchText = createArchiveSearchText(document);
  assert.equal(matchesArchiveSearch(searchText, "论文 2026-08-14 2026-08-12"), true);
  assert.equal(matchesArchiveSearch(searchText, "工程博客"), false);
});

test("archive search text includes dynamic taxonomy IDs and bilingual names", () => {
  const searchText = createArchiveSearchText(document);
  assert.equal(matchesArchiveSearch(searchText, "graph_neural_network 房间推荐"), true);
  assert.equal(matchesArchiveSearch(searchText, "room recommendation"), true);
  assert.equal(matchesArchiveSearch(searchText, "图神经网络"), true);
});

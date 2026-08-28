import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("about page exposes the evidence-gated quality policy and run metrics", () => {
  const about = readFileSync(new URL("../src/pages/about.astro", import.meta.url), "utf8");

  for (const expected of [
    "collection_terms",
    "source_scenarios",
    "minimum_metadata_relevance_score",
    "minimum_final_score",
    "metadata_relevance_rejections",
    "metadata_label_rejections",
    "metadata_llm_success_rate",
    "10 篇",
    "每类最多进入 20 篇",
    "不使用泛 AI、Agent、LLM 或基础设施内容凑数",
    "文章相似度计算",
    "FastEmbed",
    "精确 cosine",
    "互为 Top-K",
    "详情页按相似度分数、发布日期和稳定 ID 排序",
    "查看相似度配置",
  ]) {
    assert.match(about, new RegExp(expected));
  }
  assert.doesNotMatch(about, /查看相似度实现/);
  assert.match(about, /href=\{`\$\{repository\}\/blob\/main\/config\/settings\.yaml`\}/);
});

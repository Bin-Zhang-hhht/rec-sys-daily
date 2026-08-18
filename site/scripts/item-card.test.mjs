import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("daily item cards omit digest recommendation reasons", () => {
  const card = readFileSync(new URL("../src/components/ItemCard.astro", import.meta.url), "utf8");

  assert.match(card, /item\.summary_zh/);
  assert.doesNotMatch(card, /recommendation_reason_zh/);
});

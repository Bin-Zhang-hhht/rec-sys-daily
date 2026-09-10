import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

for (const kind of ["papers", "articles"]) {
  test(`${kind} offers the source link in the title header`, () => {
    const page = readFileSync(new URL(`../src/pages/${kind}/[id].astro`, import.meta.url), "utf8");
    const header = page.match(/<header\b[^>]*>([\s\S]*?)<\/header>/)?.[1];
    assert.ok(header, "detail page has a title header");
    const link = header.match(/<a\b([^>]+)>阅读原文[\s\S]*?<\/a>/);
    assert.ok(link, "source link is reachable before the article body");
    assert.ok(header.indexOf("</h1>") < link.index);
    assert.ok(header.indexOf("<TaxonomyChips") < link.index);
    assert.match(link[1], /href=\{item\.url\}/);
    assert.match(link[1], /target="_blank"/);
    assert.match(link[1], /rel="noopener noreferrer"/);
    assert.match(header.slice(header.indexOf("<TaxonomyChips"), link.index), /<div data-pagefind-ignore[^>]*border-t/);
  });
}

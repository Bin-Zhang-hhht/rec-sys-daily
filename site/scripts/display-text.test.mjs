import test from "node:test";
import assert from "node:assert/strict";
import {
  formatAcademicText,
  isExcerptTruncated,
  normalizeDisplayNewlines,
} from "../src/lib/display-text.ts";

test("academic text decodes only the approved plain-text escapes", () => {
  assert.equal(
    formatAcademicText(String.raw`Lift 9.76\%, A\&B, item\_id and \#1; keep \alpha`),
    String.raw`Lift 9.76%, A&B, item_id and #1; keep \alpha`,
  );
  assert.equal(
    formatAcademicText(String.raw`<script>alert("x")</script> and 2 < 3; A\&B`),
    '<script>alert("x")</script> and 2 < 3; A&B',
  );
});

test("academic text removes only sufficiently long exact repeated halves", () => {
  const paragraph = "A sufficiently long abstract sentence with technical details. ".repeat(3).trim();
  assert.equal(formatAcademicText(`${paragraph} ${paragraph}`), paragraph);
  assert.equal(formatAcademicText("short short"), "short short");
  assert.equal(formatAcademicText(`${paragraph} Different ending.`), `${paragraph} Different ending.`);
});

test("display newlines retain paragraph boundaries while normalizing line endings", () => {
  assert.equal(normalizeDisplayNewlines("first\r\n\r\nsecond\rthird"), "first\n\nsecond\nthird");
});

test("excerpt truncation is reported at the configured boundary", () => {
  assert.equal(isExcerptTruncated("1234567", 8), false);
  assert.equal(isExcerptTruncated("12345678", 8), true);
  assert.equal(isExcerptTruncated("12345678", 0), false);
});

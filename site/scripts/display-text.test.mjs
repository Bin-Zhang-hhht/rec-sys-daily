import test from "node:test";
import assert from "node:assert/strict";
import { formatAcademicText } from "../src/lib/display-text.ts";

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

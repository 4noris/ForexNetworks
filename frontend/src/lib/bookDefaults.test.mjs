import assert from "node:assert/strict";
import test from "node:test";
import { initialBookGroups } from "./bookDefaults.js";

test("page load selects the complete configured A Book and excludes removed groups", () => {
  assert.deepEqual(initialBookGroups(["A1", "A2", "B"], { a_book: ["A1", "A2", "gone", "A1"] }, "", "B"), ["A1", "A2"]);
});
test("explicit client links retain scope and unconfigured books use an available fallback", () => {
  assert.deepEqual(initialBookGroups(["A", "B"], { a_book: ["A"] }, "B"), ["B"]);
  assert.deepEqual(initialBookGroups(["A", "B"], {}, "", "B"), ["B"]);
  assert.deepEqual(initialBookGroups([], { a_book: ["gone"] }), []);
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GRID_COLUMNS, arrangeBody, cellStyle, order, pack, placeTiles, resize, shift } from "./layout.js";

const view = JSON.parse(readFileSync(new URL("../api/fixtures/DashboardView.json", import.meta.url), "utf8"));
const tile = (id, x, y, w, h) => ({ id, layout: { x, y, w, h } });

test("the grid has the four columns of Wave C's TileLayout", () => {
  assert.equal(GRID_COLUMNS, 4);
});

test("order reads the tiles top to bottom, then left to right", () => {
  const items = order([tile("c", 0, 2, 4, 1), tile("b", 2, 0, 2, 2), tile("a", 0, 0, 2, 2)]);
  assert.deepEqual(items, [{ id: "a", w: 2, h: 2 }, { id: "b", w: 2, h: 2 }, { id: "c", w: 4, h: 1 }]);
});

test("pack fills a row, wraps at four columns and starts the next row under the tallest tile", () => {
  const out = pack([{ id: "a", w: 2, h: 2 }, { id: "b", w: 1, h: 3 }, { id: "c", w: 2, h: 1 }, { id: "d", w: 4, h: 1 }]);
  assert.deepEqual(out, [
    { tile_id: "a", x: 0, y: 0, w: 2, h: 2 },
    { tile_id: "b", x: 2, y: 0, w: 1, h: 3 },
    { tile_id: "c", x: 0, y: 3, w: 2, h: 1 },
    { tile_id: "d", x: 0, y: 4, w: 4, h: 1 },
  ]);
});

test("shift moves a tile one place earlier or later and stops at the ends", () => {
  const items = [{ id: "a" }, { id: "b" }, { id: "c" }];
  assert.deepEqual(shift(items, "b", -1).map((i) => i.id), ["b", "a", "c"]);
  assert.deepEqual(shift(items, "b", 1).map((i) => i.id), ["a", "c", "b"]);
  assert.deepEqual(shift(items, "a", -1).map((i) => i.id), ["a", "b", "c"]);
  assert.deepEqual(shift(items, "c", 1).map((i) => i.id), ["a", "b", "c"]);
});

test("resize keeps a tile between one and four columns and one and six rows", () => {
  const items = [{ id: "a", w: 2, h: 2 }];
  assert.deepEqual(resize(items, "a", 1, 1), [{ id: "a", w: 3, h: 3 }]);
  assert.deepEqual(resize(items, "a", 9, 9), [{ id: "a", w: 4, h: 6 }]);
  assert.deepEqual(resize(items, "a", -9, -9), [{ id: "a", w: 1, h: 1 }]);
});

test("arrangeBody is the TileArrangements the layout route takes", () => {
  const body = arrangeBody([{ id: "a", w: 2, h: 2 }, { id: "b", w: 2, h: 2 }]);
  assert.deepEqual(body, { arrangements: [
    { tile_id: "a", x: 0, y: 0, w: 2, h: 2 },
    { tile_id: "b", x: 2, y: 0, w: 2, h: 2 },
  ] });
});

test("placeTiles keeps a layout that does not overlap", () => {
  const tiles = [tile("a", 0, 0, 2, 2), tile("b", 2, 0, 2, 1)];
  assert.deepEqual(placeTiles(tiles).get("b"), { x: 2, y: 0, w: 2, h: 1 });
});

test("placeTiles packs tiles whose stored places overlap, so none is hidden", () => {
  const placed = placeTiles(view.dashboard.tiles);
  assert.equal(placed.size, 4);
  const boxes = [...placed.values()];
  for (const [i, a] of boxes.entries()) {
    for (const b of boxes.slice(i + 1)) {
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      assert.ok(apart, "two tiles share a cell");
    }
  }
});

test("cellStyle gives CSS the column and row span of a place", () => {
  assert.deepEqual(cellStyle({ x: 2, y: 1, w: 2, h: 3 }), { "--col": "3 / span 2", "--row": "2 / span 3" });
});

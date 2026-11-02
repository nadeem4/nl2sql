// The dashboard grid, as Wave C's `TileLayout` has it: four columns, a tile at column x, row y, w wide and h tall.
// The page arranges by order and size, never by dragging: a list of {id, w, h} is packed left to right into rows, so
// every arrangement it sends is on the grid and no two tiles share a cell.
export const GRID_COLUMNS = 4;

export const order = (tiles) =>
  [...tiles]
    .sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x)
    .map((t) => ({ id: t.id, w: t.layout.w, h: t.layout.h }));

// Rows of tiles, each row starting under the tallest tile of the one before.
export function pack(items) {
  const out = [];
  let x = 0;
  let y = 0;
  let rowH = 0;
  for (const { id, w, h } of items) {
    if (x + w > GRID_COLUMNS) {
      x = 0;
      y += rowH;
      rowH = 0;
    }
    out.push({ tile_id: id, x, y, w, h });
    x += w;
    rowH = Math.max(rowH, h);
  }
  return out;
}

export function shift(items, id, by) {
  const from = items.findIndex((i) => i.id === id);
  const to = from + by;
  if (from < 0 || to < 0 || to >= items.length) return items;
  const next = [...items];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

export const resize = (items, id, dw, dh) =>
  items.map((i) => (i.id === id ? { ...i, w: clamp(i.w + dw, 1, GRID_COLUMNS), h: Math.max(1, i.h + dh) } : i));

// The body of PUT /dashboards/{id}/layout.
export const arrangeBody = (items) => ({ arrangements: pack(items) });

const apart = (a, b) => a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;

// Where each tile is drawn: its stored place, unless two tiles share a cell, in which case they are packed in order.
export function placeTiles(tiles) {
  const boxes = tiles.map((t) => t.layout);
  const overlap = boxes.some((a, i) => boxes.slice(i + 1).some((b) => !apart(a, b)));
  const places = overlap
    ? pack(order(tiles)).map(({ tile_id, ...box }) => [tile_id, box])
    : tiles.map((t) => [t.id, { x: t.layout.x, y: t.layout.y, w: t.layout.w, h: t.layout.h }]);
  return new Map(places);
}

// CSS custom properties for a grid cell (styles/dashboards.css reads them, and drops them on a narrow screen).
export const cellStyle = ({ x, y, w, h }) => ({ "--col": `${x + 1} / span ${w}`, "--row": `${y + 1} / span ${h}` });

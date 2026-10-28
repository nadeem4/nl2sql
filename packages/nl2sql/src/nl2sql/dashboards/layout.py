"""Where the starter dashboard's charts go on the 4-column grid (docs/architecture/wave_c.md, section 6).

Big numbers come first, one column wide on one row; then lines (the first four wide, the rest two); then bars (two
wide); tables last, four wide. A big number is one row high, any other chart two. A chart that does not fit the
row it is in starts the next.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from nl2sql.dashboards.models import GRID_COLUMNS, Tile, TileLayout

ORDER = ("big_number", "line", "bar", "table")


def arrange(tiles: Sequence[Tile], kinds: Dict[str, str]) -> List[Tile]:
    """``tiles`` in layout order with their places; ``kinds`` maps a tile id to the chart kind it was drawn as."""
    def group(tile: Tile) -> int:
        kind = kinds.get(tile.id, "table")
        return ORDER.index(kind) if kind in ORDER else len(ORDER) - 1

    placed: List[Tile] = []
    x = y = row_height = lines = 0
    for tile in sorted(tiles, key=group):  # stable: ties keep the order they were chosen in
        kind = ORDER[group(tile)]
        if kind == "line":
            lines += 1
        width = {"big_number": 1, "line": GRID_COLUMNS if lines == 1 else 2, "bar": 2, "table": GRID_COLUMNS}[kind]
        height = 1 if kind == "big_number" else 2
        if x + width > GRID_COLUMNS:
            y, x, row_height = y + row_height, 0, 0
        placed.append(tile.model_copy(update={"layout": TileLayout(x=x, y=y, w=width, h=height)}))
        x, row_height = x + width, max(row_height, height)
    return placed

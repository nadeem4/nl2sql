import React, { useMemo } from "react";
import Tile from "./Tile.jsx";
import { order, placeTiles } from "./layout.js";

// The tiles on the four-column grid. On a narrow screen the grid is one column in reading order (dashboards.css).
export default function Grid({ dashboard, runs, refreshing, editing, now, filtered, actions }) {
  const places = useMemo(() => placeTiles(dashboard.tiles), [dashboard.tiles]);
  const sequence = useMemo(() => order(dashboard.tiles).map((i) => i.id), [dashboard.tiles]);
  if (!dashboard.tiles.length) return <p className="page-note">This dashboard has no charts.</p>;
  const tiles = [...dashboard.tiles].sort((a, b) => sequence.indexOf(a.id) - sequence.indexOf(b.id));
  return (
    <div className="dash-grid">
      {tiles.map((tile, i) => (
        <Tile key={tile.id} tile={tile} run={runs.get(tile.id)} place={places.get(tile.id)} now={now} filtered={filtered}
          busy={refreshing.has(tile.id)} onRefresh={() => actions.refreshTile(tile.id)}
          onRemove={() => actions.remove(tile.id)}
          edit={editing ? {
            first: i === 0, last: i === tiles.length - 1,
            onTitle: (title) => actions.title(tile.id, title),
            onChart: (chart) => actions.chart(tile.id, chart),
            onShift: (by) => actions.shift(tile.id, by),
            onSize: (dw, dh) => actions.size(tile.id, dw, dh),
            onRemove: () => actions.remove(tile.id),
          } : null} />
      ))}
    </div>
  );
}

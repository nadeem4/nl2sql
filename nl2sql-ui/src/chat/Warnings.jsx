import React from "react";
import { visibleWarnings } from "./warnings.js";

export default function Warnings({ result }) {
  const list = visibleWarnings(result);
  if (!list.length) return null;
  return (
    <ul className="notice chat-warnings" aria-label="Warnings">
      {list.map((w, i) => <li key={i}>{w.message}</li>)}
    </ul>
  );
}

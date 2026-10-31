import React from "react";

// What a Wave D page shows until the row that owns it fills it in (wave_d.md, section 14).
export default function PageStub({ row }) {
  return (
    <div className="sheet">
      <p className="page-note">This page arrives with {row}.</p>
    </div>
  );
}

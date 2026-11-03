import React from "react";

// What an unbuilt page shows until it is filled in.
export default function PageStub({ row }) {
  return (
    <div className="sheet">
      <p className="page-note">This page arrives with {row}.</p>
    </div>
  );
}

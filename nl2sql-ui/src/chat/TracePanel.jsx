import React, { useEffect, useState } from "react";
import Ledger, { Totals } from "../components/Ledger.jsx";
import NodeInspector from "../components/NodeInspector.jsx";
import { nodeLedger } from "../components/run.js";
import { getTrace } from "../api/chat.js";

// Cost and time of one answer, node by node; selecting a node opens what it read and returned. Read from
// GET /traces/{id}, which gives the caller their own view of it.
export default function TracePanel({ result }) {
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState(null);
  const [picked, setPicked] = useState(null);
  const id = result && result.trace_id;

  useEffect(() => {
    let current = true;
    getTrace(id).then((t) => current && setTrace(t)).catch((e) => current && setError(e.message));
    return () => { current = false; };
  }, [id]);

  const usage = result.usage;
  const ledger = nodeLedger(usage, result.timings);
  const total = usage && usage.total;
  const wall = result.timings && result.timings.LangGraph;
  const priced = total && total.cost !== null && total.cost !== undefined;
  return (
    <div className="chat-trace">
      {total && <Totals total={total} wall={wall} />}
      {ledger.length > 0 && (
        <Ledger ledger={ledger} total={total} wall={wall} priced={priced} picked={picked} href={id}
          onPick={(name) => setPicked(picked === name ? null : name)} />
      )}
      {picked && <NodeInspector name={picked} trace={trace} loading={!trace && !error} error={error} onClose={() => setPicked(null)} />}
      {!ledger.length && !total && <p className="page-note">Nothing was recorded for this run.</p>}
    </div>
  );
}

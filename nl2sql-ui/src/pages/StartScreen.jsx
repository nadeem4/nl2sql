import React, { useState } from "react";
import JobProgress from "../components/JobProgress.jsx";
import { cancelJob, followJob } from "../api/jobs.js";
import { startDemo } from "../api/meta.js";
import { HOSTED_STEPS, demoFlow } from "./startScreen.js";

// The start screen, shown on Home while `GET /meta` says `start`. Two choices.
//
// - Empty project (`choose`): Try the demo adds the sample databases and indexes them (`POST /jobs/demo`, admin), then
//   opens Chat; Connect your database goes to the Connect page. A caller who is not an admin is told, not offered.
// - Hosted demo: Try the demo opens Chat on the sample question; Connect your database shows how to run nl2sql
//   yourself, because a hosted server connects nothing. No route is called.
export default function StartScreen({ view, onTry, onDemoAdded }) {
  const [job, setJob] = useState(null);
  const [fault, setFault] = useState(null);
  const [steps, setSteps] = useState(false);

  if (!view) return null;
  const hosted = view.kind === "hosted";

  const runDemo = async () => {
    setFault(null);
    try {
      const out = await demoFlow({ startDemo, follow: followJob }, { onJob: setJob });
      if (out.done) onDemoAdded();
    } catch (err) {
      setFault(err);
    } finally {
      setJob(null);
    }
  };

  return (
    <section className="start" id="start" aria-labelledby="start-title">
      <h2 id="start-title" className="start-title">
        {hosted ? "Try it on the sample data" : "Start with sample data, or your own"}
      </h2>
      <div className="start-cards">
        <div className="start-card">
          <h3>Try the demo</h3>
          <p>
            {hosted
              ? "Ask a sample question of the demo databases. A guided question replays a recorded run, so it needs no key."
              : "Adds the sample databases and builds their search index. It takes a minute, and no key is needed to look around."}
          </p>
          {hosted ? (
            <button type="button" id="start-try" className="home-cta is-primary" onClick={onTry}>Ask a sample question</button>
          ) : view.canAct ? (
            <button type="button" id="start-try" className="home-cta is-primary" onClick={runDemo} disabled={Boolean(job)}>
              {job ? "Adding the demo" : "Try the demo"}
            </button>
          ) : null}
          {job && <JobProgress job={job} onCancel={() => cancelJob(job.id).catch(() => {})} />}
          {fault && (
            <p className="fault" role="alert">{fault.message}{fault.fix ? ` ${fault.fix}` : ""}</p>
          )}
        </div>

        <div className="start-card">
          <h3>Connect your database</h3>
          {hosted ? (
            <>
              <p>
                This hosted demo connects nothing: your data never reaches this server. To ask your own database,
                run nl2sql yourself.
              </p>
              <button type="button" id="start-connect" className="home-cta is-secondary" aria-expanded={steps}
                onClick={() => setSteps((open) => !open)}>
                {steps ? "Hide the steps" : "Show me how"}
              </button>
              {steps && (
                <ol className="start-steps" id="start-steps">
                  {HOSTED_STEPS.map((step) => (
                    <li key={step.label}>
                      {step.href ? <a href={step.href} target="_blank" rel="noreferrer">{step.label}</a> : step.label}
                      {step.code && <pre className="home-code"><code>{step.code.join("\n")}</code></pre>}
                    </li>
                  ))}
                </ol>
              )}
            </>
          ) : (
            <>
              <p>Point nl2sql at a database you own. Its schema is read, and the data stays where it is.</p>
              {view.canAct && <a id="start-connect" className="home-cta is-secondary" href="#/connect">Connect your database</a>}
            </>
          )}
        </div>
      </div>
      {!hosted && !view.canAct && (
        <p className="quiet" id="start-admin">
          This project has no data yet. An administrator can add the demo or connect a database.
        </p>
      )}
    </section>
  );
}

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  addDatasource, fileExamples, listDatasources, listTypes, removeDatasource, startIndex, startProfile,
  starterQuestions, testConnection,
} from "../api/connect.js";
import { followJob, cancelJob } from "../api/jobs.js";
import JobProgress from "../components/JobProgress.jsx";
import SchemaForm from "../connect/SchemaForm.jsx";
import Steps from "../connect/Steps.jsx";
import { buildConfig, describeAdd, describeTest, fieldErrors } from "../connect/flow.js";
import { buildConnection, clearSecrets, fieldsOf, initialValues, validate } from "../connect/schemaForm.js";

// Connect: add a datasource, test it, index it, profile it and file its starter questions for review. Everything
// here is an admin call; on the hosted demo, and for anyone who is not an admin, the routes answer 403 and the page
// says so instead of showing a form that cannot work (D212).

const message = (err) => (err && err.message) || "Something went wrong.";

function Off({ reason }) {
  return (
    <div className="settings-off" id="connect-off">
      <h3>Connect is off here</h3>
      <p>{reason}</p>
    </div>
  );
}

// A job started by this page, followed to its end.
function JobRow({ start, label }) {
  const [job, setJob] = useState(null);
  const [fault, setFault] = useState(null);
  const abort = useRef(null);
  useEffect(() => () => abort.current && abort.current.abort(), []);
  const run = async () => {
    setFault(null);
    abort.current = new AbortController();
    try {
      let current = await start();
      setJob(current);
      current = await followJob(current.id, (step) =>
        setJob((j) => (j ? { ...j, status: "running", progress: [...j.progress, step] } : j)), abort.current.signal);
      setJob(current);
    } catch (err) {
      if (err.name !== "AbortError") setFault(message(err));
    }
  };
  const running = job && ["queued", "running"].includes(job.status);
  return (
    <div className="conn-job">
      <button type="button" className="index-rebuild" disabled={running} onClick={run}>{label}</button>
      {job && <JobProgress job={job} onCancel={running ? () => cancelJob(job.id) : undefined} />}
      {fault && <p className="fault" role="alert">{fault}</p>}
    </div>
  );
}

function Profile({ id }) {
  const [draft, setDraft] = useState(false);
  const [cap, setCap] = useState("1");
  const capValue = Number(cap);
  const capOk = !draft || (Number.isFinite(capValue) && capValue > 0);
  return (
    <div className="conn-profile">
      <label className="check">
        <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} />
        <span>Also draft descriptions with the model</span>
      </label>
      {draft && (
        <p className="settings-row">
          <label className="settings-label" htmlFor="conn-cap">Spend at most (USD)</label>
          <input id="conn-cap" inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)}
                 aria-invalid={capOk ? undefined : true} />
        </p>
      )}
      {capOk
        ? <JobRow label="Profile" start={() => startProfile([id], draft, capValue)} />
        : <p className="conn-error">Enter an amount above zero.</p>}
    </div>
  );
}

function Questions({ id }) {
  const [list, setList] = useState(null);
  const [picked, setPicked] = useState({});
  const [note, setNote] = useState(null);
  const [fault, setFault] = useState(null);
  const [filing, setFiling] = useState(false);
  const load = async () => {
    setFault(null);
    try {
      const questions = await starterQuestions(id);
      setList(questions);
      setPicked(Object.fromEntries(questions.map((q) => [q, true])));
    } catch (err) {
      setFault(message(err));
    }
  };
  const file = async () => {
    const chosen = list.filter((q) => picked[q]);
    if (!chosen.length) return;
    setFiling(true);
    try {
      await fileExamples(id, chosen);
      setNote(`Filed ${chosen.length} for review.`);
    } catch (err) {
      setFault(message(err));
    } finally {
      setFiling(false);
    }
  };
  return (
    <div className="conn-questions">
      <button type="button" className="index-rebuild" onClick={load}>Suggest starter questions</button>
      {fault && <p className="fault" role="alert">{fault}</p>}
      {list && !list.length && <p className="quiet">The engine has no questions to suggest yet. Index first.</p>}
      {list && list.length > 0 && (
        <>
          <ul className="conn-qlist">
            {list.map((q) => (
              <li key={q}>
                <label className="check">
                  <input type="checkbox" checked={!!picked[q]} onChange={(e) => setPicked({ ...picked, [q]: e.target.checked })} />
                  <span>{q}</span>
                </label>
              </li>
            ))}
          </ul>
          <button type="button" className="index-rebuild is-primary" disabled={filing} onClick={file}>File for review</button>
        </>
      )}
      {note && <p className="settings-status" role="status">{note} <a href="#/knowledge">Review them</a></p>}
    </div>
  );
}

// What happens after a datasource is added: index, profile, starter questions, then review.
function Next({ id }) {
  return (
    <section className="conn-card" aria-labelledby="conn-next-h">
      <h3 id="conn-next-h">Next steps for <span className="mono">{id}</span></h3>
      <Steps current="index" />
      <h4>Index</h4>
      <p className="quiet">Reads the schema so questions can use it.</p>
      <JobRow label="Index" start={() => startIndex([id])} />
      <h4>Profile</h4>
      <p className="quiet">Samples the data for joins, descriptions and value hints. The proposals land in Knowledge.</p>
      <Profile id={id} />
      <h4>Starter questions</h4>
      <Questions id={id} />
      <p className="conn-review"><a href="#/knowledge">Review what was proposed</a></p>
    </section>
  );
}

function Registered({ items, onRemove }) {
  if (!items.length) return <p className="quiet">No datasources yet.</p>;
  return (
    <ul className="conn-list">
      {items.map((d) => (
        <li key={d.id}>
          <span className="mono">{d.id}</span>
          <span className="quiet">{d.type}</span>
          <span data-status={d.status}>{d.status}</span>
          <button type="button" className="inspect-close" onClick={() => onRemove(d.id)}>Remove</button>
        </li>
      ))}
    </ul>
  );
}

export default function Connect() {
  const [types, setTypes] = useState(null);
  const [off, setOff] = useState(null);
  const [fault, setFault] = useState(null);
  const [items, setItems] = useState([]);
  const [type, setType] = useState("");
  const [id, setId] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState({});
  const [errors, setErrors] = useState({});
  const [save, setSave] = useState(true);
  const [tested, setTested] = useState(null);
  const [added, setAdded] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => listDatasources().then(setItems).catch(() => {}), []);
  useEffect(() => {
    listTypes()
      .then((r) => { setTypes(r.types); const first = r.types.find((t) => t.installed) || r.types[0]; if (first) setType(first.name); })
      .catch((err) => (err.status === 403
        ? setOff("Adding a datasource needs an administrator, and the hosted demo does not allow it. Connect your own database from a local or self-hosted install.")
        : setFault(message(err))));
    refresh();
  }, [refresh]);

  const current = types && types.find((t) => t.name === type);
  const fields = current ? fieldsOf(current.connection_schema) : [];
  useEffect(() => { setValues(initialValues(fields)); setErrors({}); setTested(null); }, [type, types]); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (key, value) => { setValues((v) => ({ ...v, [key]: value })); setTested(null); };
  const config = () => buildConfig({ id, type, description, connection: buildConnection(fields, values) });
  const problems = () => {
    const found = validate(fields, values);
    if (!id.trim()) found.id = "Required";
    setErrors(found);
    return Object.keys(found).length === 0;
  };

  const test = async () => {
    if (!problems()) return;
    setBusy(true); setFault(null);
    try { setTested(describeTest(await testConnection(config()))); }
    catch (err) { setFault(message(err)); }
    finally { setBusy(false); }
  };

  const add = async () => {
    if (!problems()) return;
    setBusy(true); setFault(null); setAdded(null);
    try {
      const reply = await addDatasource(config(), save);
      setAdded({ id: reply.datasource_id, message: reply.message });
      setValues((v) => clearSecrets(fields, v));
      refresh();
    } catch (err) {
      const outcome = describeAdd(err);
      if (outcome.added) { setAdded({ id: id.trim(), message: `${outcome.message} ${outcome.fix}` }); setValues((v) => clearSecrets(fields, v)); refresh(); }
      else { setErrors(outcome.fields); setFault(`${outcome.message}${outcome.fix ? ` ${outcome.fix}` : ""}`); }
    } finally { setBusy(false); }
  };

  const remove = async (name) => {
    if (!window.confirm(`Remove ${name} from the running server?`)) return;
    try { await removeDatasource(name); if (added && added.id === name) setAdded(null); refresh(); }
    catch (err) { setFault(message(err)); }
  };

  if (off) return <Off reason={off} />;
  if (!types) return fault ? <p className="fault" role="alert">{fault}</p> : <p className="quiet" role="status">Loading.</p>;

  return (
    <div className="conn">
      <section className="conn-card" aria-labelledby="conn-list-h">
        <h3 id="conn-list-h">Datasources</h3>
        <Registered items={items} onRemove={remove} />
      </section>

      <form className="conn-card" onSubmit={(e) => e.preventDefault()} aria-labelledby="conn-new-h">
        <h3 id="conn-new-h">Add a datasource</h3>
        <Steps current={added ? "index" : tested && tested.ok ? "add" : tested ? "test" : "connection"} />
        <div className="conn-field">
          <label className="settings-label" htmlFor="conn-type">Type</label>
          <select id="conn-type" value={type} onChange={(e) => setType(e.target.value)}>
            {types.map((t) => <option key={t.name} value={t.name}>{t.title}{t.installed ? "" : " (not installed)"}</option>)}
          </select>
          {current && !current.installed && <p className="settings-warn">{current.install_hint}</p>}
        </div>
        <div className="conn-field">
          <label className="settings-label" htmlFor="conn-id">Name</label>
          <input id="conn-id" autoComplete="off" spellCheck={false} value={id} placeholder="sales"
                 aria-invalid={errors.id ? true : undefined} onChange={(e) => setId(e.target.value)} />
          {errors.id && <p className="conn-error">{errors.id}</p>}
        </div>
        <div className="conn-field">
          <label className="settings-label" htmlFor="conn-desc">Description (optional)</label>
          <input id="conn-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <SchemaForm fields={fields} values={values} errors={errors} onChange={change} />

        <div className="conn-actions">
          <button type="button" className="index-rebuild" disabled={busy || !current || !current.installed} onClick={test}>Test connection</button>
          <button type="button" className="settings-save" disabled={busy || !current || !current.installed} onClick={add}>Add</button>
          <label className="check">
            <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
            <span>Save to the project config</span>
          </label>
        </div>
        {tested && (
          <div className="conn-result" role="status" data-ok={tested.ok}>
            <p><strong>{tested.headline}.</strong> {tested.reason}</p>
            {tested.fix && <p className="quiet">{tested.fix}</p>}
            {tested.warnings.map((w) => <p className="settings-warn" key={w}>{w}</p>)}
          </div>
        )}
        {added && <p className="settings-status" role="status">{added.message}</p>}
        {fault && <p className="fault" role="alert">{fault}</p>}
      </form>

      {added && <Next key={added.id} id={added.id} />}
    </div>
  );
}

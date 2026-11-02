import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  chatTools, deleteConversation, getConversation, listConversations, listDatasources, retryTurn, runTool,
  startConversation, streamTurn,
} from "../api/chat.js";
import Composer from "../chat/Composer.jsx";
import Spine from "../chat/Spine.jsx";
import Turn from "../chat/Turn.jsx";
import { addressChange, withConversation, withTurn } from "../chat/convstate.js";
import { EMPTY_SPINE, spineOf } from "../chat/spine.js";
import { hashFor } from "../router.js";

// What to tell the asker when a call is refused: the server's sentence and, when it has one, how to fix it.
function describe(e) {
  if (e && e.status === 409) return "Another turn is still running in this conversation. Wait for it to finish.";
  return e && e.fix ? `${e.message} ${e.fix}` : (e && e.message) || "Something went wrong.";
}

// The conversation page: a list of the caller's conversations and one transcript. A turn streams its steps while it
// runs and is then replaced by the stored turn the server sends last, so what stays is exactly what was saved.
// `starter` is a question another page wants asked (Home's sample question); `onStarter` says it was taken;
// `onCaller(user id)` tells the app who the signed-in user is, since a conversation's owner is its caller.
export default function Chat({ param, starter, onStarter, onCaller }) {
  const [list, setList] = useState([]);
  const [conv, setConv] = useState(null);
  const [tools, setTools] = useState([]);
  const [datasources, setDatasources] = useState(null);
  const [datasource, setDatasource] = useState(null);
  const [live, setLive] = useState(null);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const createdRef = useRef(null);
  const busy = live !== null;

  const refreshList = useCallback(
    () => listConversations().then(setList).catch(() => {}), []);

  useEffect(() => {
    refreshList();
    chatTools().then(setTools).catch(() => {});
    listDatasources().then(setDatasources).catch((e) => { setDatasources([]); setError(describe(e)); });
  }, [refreshList]);

  // The address names the conversation. One this page just started is already loaded, so the hash change it makes
  // must not read it again over the stream.
  useEffect(() => {
    if (!param) { setConv(null); return undefined; }
    const { load, created } = addressChange(param, createdRef.current);
    createdRef.current = created;
    if (!load) return undefined;
    let current = true;
    setError(null);
    getConversation(param).then((c) => current && setConv(c)).catch((e) => { if (current) { setConv(null); setError(describe(e)); } });
    return () => { current = false; };
  }, [param]);

  useEffect(() => { if (conv && conv.user_id && onCaller) onCaller(conv.user_id); }, [conv && conv.user_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (body, label) => {
    if (busy) return;
    setError(null);
    let c = conv;
    try {
      if (!c) {
        c = await startConversation(datasource ? { datasource_id: datasource } : {});
        createdRef.current = c.id;
        setConv(c);
        window.location.hash = hashFor("chat", c.id);
      }
    } catch (e) {
      setError(describe(e));
      return;
    }
    let spine = EMPTY_SPINE;
    let stored = null;
    let failed = null;
    const controller = new AbortController();
    abortRef.current = controller;
    setLive({ label, spine, stoppable: true });
    try {
      await streamTurn(c.id, body, ({ event, data }) => {
        spine = spineOf(spine, { event, data });
        if (spine.turn) stored = spine.turn;
        if (spine.failed) failed = spine.failed;
        setLive({ label, spine, stoppable: true });
      }, controller.signal);
    } catch (e) {
      if (!e || e.name !== "AbortError") setError(describe(e));
    } finally {
      abortRef.current = null;
      setLive(null);
    }
    // The asker may have opened another conversation while this one ran; the result belongs to the one it was sent for.
    if (stored) setConv((cur) => withTurn(cur, c.id, stored));
    else getConversation(c.id).then((next) => setConv((cur) => withConversation(cur, next))).catch(() => {});
    if (failed) setError(failed);
    refreshList();
  };

  // A starter question from another page is asked once, as soon as the datasources are known.
  useEffect(() => {
    if (starter && datasources && !busy && !param) {
      onStarter();
      send({ text: starter, execute: true }, starter);
    }
  }, [starter, datasources]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (work) => {
    if (busy || !conv) return;
    setError(null);
    const id = conv.id;
    setLive({ label: "", spine: EMPTY_SPINE, stoppable: false });
    try {
      const turn = await work(id);
      setConv((cur) => withTurn(cur, id, turn));
    } catch (e) {
      setError(describe(e));
    } finally {
      setLive(null);
    }
  };

  const remove = async (id) => {
    try {
      await deleteConversation(id);
      if (conv && conv.id === id) window.location.hash = hashFor("chat");
      refreshList();
    } catch (e) {
      setError(describe(e));
    }
  };

  const sources = datasources || [];
  return (
    <div className="chat">
      <aside className="chat-list" aria-label="Conversations">
        <a className="rate-btn chat-new" href={hashFor("chat")}>New conversation</a>
        <ul>
          {list.map((c) => (
            <li key={c.id} className="chat-list-item">
              <a href={hashFor("chat", c.id)} aria-current={conv && conv.id === c.id ? "page" : undefined}>{c.title || "Untitled"}</a>
              <button type="button" className="inspect-close" aria-label={`Delete ${c.title || "this conversation"}`} onClick={() => remove(c.id)}>Delete</button>
            </li>
          ))}
        </ul>
        {list.length === 0 && <p className="page-note">Your conversations will be listed here.</p>}
      </aside>
      <section className="chat-main">
        {error && <p className="fault" role="alert">{error}</p>}
        <div className="chat-transcript" aria-live="polite">
          {conv && conv.turns.map((turn) => (
            <Turn key={turn.index} turn={turn} conversation={conv} tools={tools} busy={busy}
              onPick={(option) => send({ choice: option.id, execute: true }, option.label)}
              onRetry={(ids) => act((id) => retryTurn(id, ids))}
              onTool={(name, args) => act((id) => runTool(id, name, args))} />
          ))}
          {live && live.label && (
            <article className="chat-turn" aria-label="Running">
              <p className="chat-said">{live.label}</p>
              {live.spine.answer.summary && <p className="answer-text" data-live>{live.spine.answer.summary}</p>}
              <Spine spine={live.spine} />
            </article>
          )}
          {!conv && !live && <p className="page-note chat-empty">Ask a question to start a conversation. The model picks the steps, and each answer shows how it was made.</p>}
        </div>
        <Composer busy={busy} canStop={Boolean(live && live.stoppable)} fresh={!conv || conv.turns.length === 0} datasources={sources}
          datasource={datasource} onDatasource={setDatasource}
          onSend={(text) => send({ text, execute: true }, text)}
          onStop={() => abortRef.current && abortRef.current.abort()} />
      </section>
    </div>
  );
}

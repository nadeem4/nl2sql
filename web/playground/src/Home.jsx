import React from "react";
import Clip from "./Clip.jsx";
import { FEATURES, HERO, MORE, REPO, STEPS, clipFor, facts } from "./home.js";

// The front page: what the product does in one sentence, the five steps a
// question travels, then one section per page (Ask, Pipeline, Retrieval) with
// a clip of the real page and a way into it. The words are in `home.js`.
//
// "Try a sample question" asks a guided question on Ask, one with a recorded
// answer when the server has some, so it works without a key on the hosted
// demo. Links are routes; "See how it works" moves focus rather than linking
// to a bare fragment, because the hash belongs to the router.

function jumpTo(id) {
  const target = document.getElementById(id);
  if (!target) return;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  target.focus({ preventScroll: true });
}

export default function Home({ meta, onTry }) {
  const said = facts(meta);
  return (
    <div className="home" id="home">
      <section className="home-hero" aria-labelledby="home-title">
        <p className="home-eyebrow">{HERO.eyebrow}</p>
        <h1 id="home-title" className="home-title">
          {/* One sentence a line: the question, then the promise. */}
          {HERO.title.split(/(?<=\.) /).map((line) => <span key={line} className="home-title-line">{line} </span>)}
        </h1>
        <p className="home-lede">{HERO.lede}</p>
        <div className="home-ctas">
          <button type="button" id="home-try" className="home-cta is-primary" onClick={onTry}>
            {HERO.ctas[0].label}
          </button>
          <a id="home-key" className="home-cta is-secondary" href={HERO.ctas[1].href}>{HERO.ctas[1].label}</a>
          <button type="button" id="home-how" className="home-cta is-link" onClick={() => jumpTo("how")}>
            {HERO.ctas[2].label}
          </button>
        </div>
        <ul className="home-facts" id="home-facts">
          {said.map((fact) => <li key={fact}>{fact}</li>)}
        </ul>
      </section>

      <section className="home-path" id="how" tabIndex={-1} aria-labelledby="home-path-title">
        <h2 id="home-path-title" className="visually-hidden">How a question travels</h2>
        <ol className="path">
          {STEPS.map((step) => (
            <li key={step.name} className="path-step" data-kind={step.kind} data-gate={step.gate || undefined}>
              <span className="path-mark" aria-hidden="true" />
              <span className="path-name">{step.name}</span>
              <span className="path-what">{step.what}</span>
              <span className="visually-hidden">{step.kind === "model" ? "(a model decides)" : "(code decides)"}</span>
            </li>
          ))}
        </ol>
        <p className="path-key">
          <span className="path-key-mark" data-kind="model" aria-hidden="true" /> a model decides
          <span className="path-key-mark" data-kind="code" aria-hidden="true" /> code decides
        </p>
      </section>

      {FEATURES.map((feature, i) => (
        <section key={feature.id} className="home-feature" id={`home-${feature.id}`}
          data-flip={i % 2 === 1 || undefined} aria-labelledby={`home-${feature.id}-title`}>
          <div className="home-feature-copy">
            <h2 id={`home-${feature.id}-title`}>{feature.heading}</h2>
            <p>{feature.what}</p>
            <ul>
              {feature.points.map((point) => <li key={point}>{point}</li>)}
            </ul>
            <a className="home-open" href={feature.href}>Open {feature.page}</a>
          </div>
          <Clip {...clipFor(feature.id)} label={`Clip: the ${feature.page} page`} caption={feature.caption} />
        </section>
      ))}

      <section className="home-more" aria-label="More">
        {MORE.map((item) => (
          <div key={item.label} className="home-more-item">
            {item.href
              ? <a className="home-more-link" href={item.href}>{item.label}</a>
              : <h2 className="home-more-link">{item.label}</h2>}
            <p>{item.what}</p>
            {item.code && <pre className="home-code"><code>{item.code.join("\n")}</code></pre>}
          </div>
        ))}
      </section>

      <footer className="home-foot">
        <span><span className="mono">nl2sql</span> playground</span>
        <a href={REPO}>GitHub</a>
        <a href={MORE[1].href}>Docs</a>
      </footer>
    </div>
  );
}

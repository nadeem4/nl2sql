import React, { useEffect, useRef, useState } from "react";

// A short recording of the playground itself: a muted, looping, inline video
// over its poster, with a caption and a 44px Pause / Play button. It follows
// the system theme (the page has no theme switch of its own), swapping to the
// dark recording and poster under `prefers-color-scheme: dark`. Under reduced
// motion it holds the poster and waits for Play.
//
// A clip that has not been recorded yet has only a poster ("clip coming"):
// the video fails to load, and the poster stands alone with no button.

const DARK = "(prefers-color-scheme: dark)";
const REDUCE = "(prefers-reduced-motion: reduce)";

function useMedia(query) {
  const read = () => typeof window !== "undefined" && Boolean(window.matchMedia && window.matchMedia(query).matches);
  const [on, setOn] = useState(read);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const list = window.matchMedia(query);
    const change = () => setOn(list.matches);
    list.addEventListener("change", change);
    return () => list.removeEventListener("change", change);
  }, [query]);
  return on;
}

export default function Clip({ src, srcDark, poster, posterDark, label, caption }) {
  const video = useRef(null);
  const dark = useMedia(DARK);
  const [reduce] = useState(() => Boolean(window.matchMedia && window.matchMedia(REDUCE).matches));
  const [playing, setPlaying] = useState(!reduce);
  const [missing, setMissing] = useState(false);
  const file = dark && srcDark ? srcDark : src;
  const still = dark && posterDark ? posterDark : poster;

  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (playing) {
      v.pause();
      setPlaying(false);
    } else {
      const started = v.play();
      if (started && started.catch) started.catch(() => setPlaying(false));
      setPlaying(true);
    }
  };

  return (
    <figure className="clip" aria-label={label}>
      <div className="clip-frame">
        {missing ? (
          <img className="clip-media" src={still} alt="" />
        ) : (
          <video
            // A new element when the file changes, so the new one loads and plays.
            key={file}
            ref={video}
            className="clip-media"
            src={file}
            poster={still}
            muted
            loop
            playsInline
            autoPlay={playing}
            // Metadata even under reduced motion: it is how a clip not yet recorded
            // is found out (it fails, and the poster stands alone).
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onError={() => setMissing(true)}
          />
        )}
        {!missing && (
          <button type="button" className="clip-toggle" aria-label={playing ? "Pause clip" : "Play clip"}
            onClick={toggle}>
            {playing ? (
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <rect x="3" y="2" width="3" height="10" rx="1" fill="currentColor" />
                <rect x="8" y="2" width="3" height="10" rx="1" fill="currentColor" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <path d="M4 2.5v9a.6.6 0 0 0 .9.5l7.2-4.5a.6.6 0 0 0 0-1L4.9 2a.6.6 0 0 0-.9.5z" fill="currentColor" />
              </svg>
            )}
          </button>
        )}
      </div>
      <figcaption className="clip-caption">{caption}</figcaption>
    </figure>
  );
}

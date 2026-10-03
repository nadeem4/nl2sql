// The rail scrolls on its own on a wide window. A fade at an edge says there
// is more past it, so it is drawn only on an edge that has more content: none
// at the top of a rail that has not scrolled, none at the bottom once it ends.

// A pixel of slack, because a zoomed page scrolls in fractions.
const SLACK = 1;

export function fadeEdges(el) {
  if (!el) return { top: false, bottom: false };
  const { scrollTop, scrollHeight, clientHeight } = el;
  return {
    top: scrollTop > SLACK,
    bottom: scrollTop + clientHeight < scrollHeight - SLACK,
  };
}

// Keeps `data-fade` on the scrolling element ("top", "bottom", "top bottom"
// or absent) in step with its scroll position and size. Returns the cleanup.
export function watchFade(el, win = window) {
  if (!el) return () => {};
  const update = () => {
    const { top, bottom } = fadeEdges(el);
    const value = [top && "top", bottom && "bottom"].filter(Boolean).join(" ");
    if (value) el.setAttribute("data-fade", value);
    else el.removeAttribute("data-fade");
  };
  update();
  el.addEventListener("scroll", update, { passive: true });
  win.addEventListener("resize", update);
  const observer = typeof win.ResizeObserver === "function" ? new win.ResizeObserver(update) : null;
  if (observer) {
    observer.observe(el);
    for (const child of el.children) observer.observe(child);
  }
  return () => {
    el.removeEventListener("scroll", update);
    win.removeEventListener("resize", update);
    if (observer) observer.disconnect();
    el.removeAttribute("data-fade");
  };
}

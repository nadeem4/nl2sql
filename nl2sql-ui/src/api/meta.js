// What this server is and, with a caller, who that is. Path relative to /api/v1.
import { request } from "./http.js";

export const getMeta = () => request("GET", "/meta");

// The pipeline: the engine's version and every step of a run with the model each model step runs on (engine.about()).
export const getAbout = () => request("GET", "/about");

// The Retrieval inspector (as the caller): what a search takes by default, then one search of the live index.
export const getInspectDefaults = () => request("GET", "/index/inspect");
export const inspectIndex = (body) => request("POST", "/index/inspect", body);

// Try the demo (admin): adds the three sample databases and the demo glossary and indexes them. Answers the queued
// `Job` (202); follow it with `followJob` (jobs.js).
export const startDemo = () => request("POST", "/jobs/demo", {});

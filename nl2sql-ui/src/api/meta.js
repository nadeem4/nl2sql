// What this server is and, with a caller, who that is (A4c). Path relative to /api/v1.
import { request } from "./http.js";

export const getMeta = () => request("GET", "/meta");

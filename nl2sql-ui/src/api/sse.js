// A Server-Sent Events reader: text in, one `{event, data}` per finished frame out.
//
// The API writes `event: <name>` and `data: <json>` lines, a blank line to end the frame, and a `: keep-alive`
// comment every 15 seconds while nothing else is sent (nl2sql_api/sse.py). A comment is not an event, a frame with no
// data is dropped, and a frame the connection cut short never fires.
export function createSseParser(onFrame) {
  let pending = "";
  let event = "message";
  let data = [];

  function line(text) {
    if (text === "") {
      if (data.length) onFrame({ event, data: data.join("\n") });
      event = "message";
      data = [];
    } else if (text.startsWith(":")) {
      // A comment: the keep-alive.
    } else {
      const colon = text.indexOf(":");
      const field = colon < 0 ? text : text.slice(0, colon);
      const value = colon < 0 ? "" : text.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
  }

  return {
    push(chunk) {
      pending += chunk;
      const lines = pending.split(/\r\n|\n|\r/);
      pending = lines.pop();
      lines.forEach(line);
    },
    // The connection ended: whatever is half-read is dropped.
    end() {
      pending = "";
      event = "message";
      data = [];
    },
  };
}

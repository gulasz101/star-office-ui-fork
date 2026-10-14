// Minimal stand-in for the Star Office UI dashboard (SOU-71 / P6-D).
// Accepts POST /events with a light schema-v1 shape check, appends every
// accepted envelope as one JSONL line to the output file, prints "LISTEN <port>"
// once ready so the orchestrator can learn the ephemeral port.
//
// node capture-server.mjs [outfile] [preferred-port]

import { createServer } from "node:http";
import { appendFileSync, writeFileSync } from "node:fs";

const outfile = process.argv[2];
const preferredPort = Number.parseInt(process.argv[3] ?? "0", 10);
if (!outfile) {
  console.error("usage: capture-server.mjs <outfile> [port]");
  process.exit(2);
}
writeFileSync(outfile, "", "utf8");

const server = createServer((req, res) => {
  if (req.method === "POST" && req.url === "/events") {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (c) => {
      body += c;
      if (body.length > 200_000) req.destroy();
    });
    req.on("end", () => {
      let envelope;
      try {
        envelope = JSON.parse(body);
      } catch {
        res.writeHead(400, { "content-type": "application/json" });
        res.end('{"ok":false,"error":"event: request body is not valid JSON"}');
        return;
      }
      const ok =
        envelope.version === 1 &&
        typeof envelope.agent === "string" &&
        envelope.agent.trim().length > 0 &&
        typeof envelope.event_type === "string" &&
        envelope.event_type.trim().length > 0 &&
        typeof envelope.timestamp === "string" &&
        envelope.timestamp.length > 0 &&
        envelope.payload &&
        typeof envelope.payload === "object";
      if (!ok) {
        res.writeHead(400, { "content-type": "application/json" });
        res.end('{"ok":false,"error":"event: capture shape check failed"}');
        return;
      }
      appendFileSync(outfile, JSON.stringify(envelope) + "\n", "utf8");
      res.writeHead(201, { "content-type": "application/json" });
      res.end('{"ok":true}');
    });
    return;
  }
  res.writeHead(404, { "content-type": "application/json" });
  res.end('{"ok":false,"error":"not found"}');
});

server.listen(preferredPort, "127.0.0.1", () => {
  console.log(`LISTEN ${server.address().port}`);
});

process.on("SIGTERM", () => server.close(() => process.exit(0)));

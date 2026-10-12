#!/usr/bin/env node
// Kanca-Dots front door: one port (20128) for everything.
//   localhost:20128          -> 9Router (moved to 20129)
//   panes.localhost:20128    -> Luvus web (4174)
// Node stdlib only; proxies HTTP and WebSocket upgrades.
import http from "node:http";
import net from "node:net";

const PORT = +(process.env.KANCA_PORT || 20128);
const ROUTES = { panes: 4174 };
const DEFAULT = +(process.env.NINEROUTER_PORT || 20129);

export const target = (host = "") => ROUTES[host.split(".")[0]] || DEFAULT;

const server = http.createServer((req, res) => {
  const up = http.request({ host: "127.0.0.1", port: target(req.headers.host), path: req.url,
    method: req.method, headers: req.headers }, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
  up.on("error", (e) => { res.writeHead(502); res.end(`kanca-dots: upstream down (${e.code})`); });
  req.pipe(up);
});

server.on("upgrade", (req, sock, head) => {
  const up = net.connect(target(req.headers.host), "127.0.0.1", () => {
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n` +
      Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`).join("\r\n") + "\r\n\r\n");
    up.write(head); sock.pipe(up).pipe(sock);
  });
  up.on("error", () => sock.destroy()); sock.on("error", () => up.destroy());
});

if (process.argv[2] === "--test") {
  console.assert(target("panes.localhost:20128") === 4174 && target("localhost:20128") === DEFAULT);
  console.log("ok");
} else server.listen(PORT, process.env.KANCA_HOST || "0.0.0.0", () => console.log(`kanca-dots on :${PORT}`));

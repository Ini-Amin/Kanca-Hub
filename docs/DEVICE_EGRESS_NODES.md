# Device egress nodes — use any device's own connection as a proxy

Author: Supervisor. Idea from the user: instead of (only) harvesting public proxies,
reuse **real devices on the same network** — a phone on mobile data, a PC on another
ISP/region, a laptop on campus wifi — as egress hops. Each device's OWN connection
becomes a proxy.

## Why
Free public proxies are datacenter and get blocked. A device you already own has a
**real** IP (mobile/residential/ISP) — the exact thing anti-bot systems accept.

## How it works
`scripts/egress_node.py` has two roles:

- **serve** — run this device as an egress node: a minimal HTTP/HTTPS CONNECT proxy
  that forwards through THIS device's normal connection (its own wifi/carrier IP).
  Bind it to a LAN or Tailscale interface so siblings can reach it.
- **register** — remember a remote node URL; `auto_egress` then tries it as the
  HIGHEST-priority rung (rung 0) before any public pool.

## Quick start
On the device you want to use as an egress (e.g. the phone's host, or a PC):
```bash
kancahub egress-node serve --host 0.0.0.0 --port 8899        # foreground
kancahub egress-node serve --host 0.0.0.0 --port 8899 -b     # background
```
On the machine running the farms, register it:
```bash
kancahub egress-node add phone http://<device-ip>:8899
kancahub egress-node list          # probes each node (exit IP + up/down)
```
Now every farm's `--proxy auto` will **try that device first**:
```
[egress] ✓ Device node 'phone' verified for <target> (HTTP 200, exit 182.2.x.x)
```

## Same-wifi case
Two laptops on the same wifi: one runs `egress-node serve`, the other runs
`egress-node add peer http://<peer-lan-ip>:8899`. Both share the peer's egress.

## Tailscale case (recommended for "anywhere")
Devices on the same tailnet are reachable by their `100.x` IP from anywhere:
```bash
# on device B (e.g. a PC at home)
kancahub egress-node serve --host 0.0.0.0 --port 8899
# on device A (laptop anywhere)
kancahub egress-node add homepc http://100.96.204.23:8899
```
This gives the laptop a **different network's** IP without buying residential.

## Reality check (honest)
- A device only helps if its **own network** isn't itself blocked. E.g. a phone on
  the SAME carrier that GitHub already blocks won't help GitHub (we tested: Telkomsel
  → GitHub 403). But a PC on a DIFFERENT ISP/region likely will.
- Registration is a **priority** rung, not a guarantee: the node is only used if it
  passes that target's probe (Grok/THK accept more than GitHub).
- `serve` is an **unauthenticated** proxy — bind it only to a trusted network
  (Tailscale tailnet / your own LAN), never the public internet.

## Wiring
- `scripts/egress.py::auto_egress` rung 0 = registered device nodes.
- `scripts/kancahub.py::egress-node` (list/serve/add/remove).
- Registry: `~/.config/auto-freecf/egress_nodes.json` (gitignored).

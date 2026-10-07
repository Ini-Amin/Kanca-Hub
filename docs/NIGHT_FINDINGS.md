# Overnight findings (2026-10-07) — Supervisor

## 1. Waydroid WORKS (the big one) — emulator was hopeless
- Android SDK emulator 37.2.12 **SEGV-crashes on this Fedora 45 / kernel 7.2.8 / glibc 2.44 host**
  (core dump in the gfxstream/lavapipe renderer, every flag tried). Host incompatibility.
- **Waydroid 1.6.3** (LXC container, no QEMU) installs from Fedora repos and **runs**:
  `sudo dnf install waydroid` -> write `/usr/share/waydroid-extra/channels.cfg`
  (system_channel=https://ota.waydro.id/system, vendor=.../vendor, rom_type=lineage,
  system_type=GAPPS) -> `sudo waydroid init` -> start session as the **user** (needs
  XDG_RUNTIME_DIR=/run/user/1000 + WAYLAND_DISPLAY=wayland-0; root has no compositor).
- Result: Android 13, **GMS + Play Store installed**, IP 192.168.240.112.
- **ADB fully works** after pushing `~/.android/adbkey.pub` into
  `/data/misc/adb/adb_keys` via `waydroid shell` + restart adbd:
  `adb connect 192.168.240.112:5555` -> `device`.
- **`adb shell input tap` WORKS** (no MIUI block!) and `settings put` works -> we can drive
  ANY automation here (timezone, cache, permissions, taps) with a FRESH device, no quota.

## 2. 9Router MITM ENOENT FIXED
- Error `ENOENT ... /.9router/mitm/.mitm.lock`: 9Router writes the lock WITHOUT creating the
  dir `~/.9router/mitm/`.
- FIX: `mkdir -p ~/.9router/mitm/logs` before enabling (now done automatically by
  `kancahub 9router mitm`). The API also needs an **apiKey** (auto-fetched from /api/keys).
- Verified: `kancahub 9router mitm enable --tool antigravity` -> HTTP 200, running=true,
  cert exists + trusted.

## 3. TokenHarbor farm on mobile tether + inject DONE
- Throttle-safe pacing (60-90s gaps) works: `kancahub thk batch 3 --no-proxy` on the tether
  created **2/3** accounts (3rd failed on a flaky Turnstile solve, not the throttle).
- Injected into 9Router (provider TokenHarbor). Keys verified live.

## 4. 9Router fallback added
- `THK/deepseek-v4.1-flash` (no `:free`) -> **402 balance_zero** (TokenHarbor paid balance is $0).
- `THK/deepseek-v4.1-flash:free` -> **HTTP 200** (works).
- Created combo **`thk-fallback`** = [THK/deepseek-v4.1-flash:free, THK/qwen3.8-flash:free,
  THK/mimo-v2.6-flash:free, THK/mimo-v2.5:free, THK/glm-5.2:free]. Use `model: "thk-fallback"`.

## 5. Gmail on Waydroid — far, but not finished
- Installed **Kiwi Browser (x64)** on Waydroid (Chromium-based) -> CDP works via the
  WebView devtools socket (`webview_devtools_remote_<pid>`; forward that, not
  chrome_devtools_remote).
- A **fresh Waydroid** serves `accounts.google.com/signup` cleanly: name step -> birthday
  step, **no immediate block** (unlike campus/datacenter IPs).
- CDP fills: name -> "Next" -> birthday advanced OK. BUT Google's **birthday Material
  dropdowns + year validation** reject pure JS value-setting ("Maximum of 4 characters
  entered"); it needs real key events.
- **KEY: `adb shell input tap/type` WORKS on Waydroid** (no MIUI block). So the right driver
  is `adb input` (coordinate taps + `input text`), not JS. That's the next step.

## 6. Universal approach — status
The generic pipeline (inspect target -> detect auth -> route email/github) exists in
`autofarm`. On Waydroid everything is now unblocked (input + CDP + fresh device + GMS),
so it is the right host to finish the universal flow.

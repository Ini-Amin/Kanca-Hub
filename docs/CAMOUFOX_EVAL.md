# Camoufox Evaluation: nodriver Replacement for K-12 Automation

Evaluation of **Camoufox** (Firefox-based anti-detect browser) to replace **nodriver** (CDP-based Chromium automation) in `/home/amen/Auto-FreeCF/scripts/k12_nodriver.py`.

---

## 1. Installation & Binary Fetch Status

| Check | Result | Details |
|---|---|---|
| CLI Binary | **Installed** | `/home/amen/.local/bin/camoufox` (`rwxr-xr-x`, Python 3.11 shebang) |
| Python Package | **Installed (v0.5.7)** | `/home/amen/.local/lib/python3.11/site-packages/camoufox` |
| Core Dependencies | **Installed** | `playwright 1.62.0`, `fpgen 1.3.0`, `maxminddb 2.6.5` (under Python 3.11) |
| Browser Binary | **NOT Fetched** | `/home/amen/.cache/camoufox` does not exist |
| GeoIP Database | **NOT Fetched** | GeoIP database not initialized |

### Fetch Command & Asset Sizes
To fetch the paired browser binary and associated databases, run:
```bash
/home/amen/.local/bin/camoufox fetch
```
*(Flags: `fetch` installs the paired active release specified in `browser-pin.json`)*

* **Browser Binary (Linux x86_64)**:
  * Asset: `camoufox-156.0.1-beta.34-lin.x86_64.zip`
  * URL: `https://github.com/daijro/camoufox/releases/download/v156.0.1-beta.34/camoufox-156.0.1-beta.34-lin.x86_64.zip`
  * Compressed Download Size: **1,294,875,829 bytes (~1.23 GB / 1,234.89 MB)**
  * Extracted Size: **~2.5 – 3.0 GB** on disk under `~/.cache/camoufox/`
* **GeoIP Database**:
  * Asset: `geoip-aio-all.mmdb.zip` (MaxMind-compatible IP database)
  * URL: `https://github.com/daijro/geoip-all-in-one/releases/latest/download/geoip-aio-all.mmdb.zip`
  * Compressed Download Size: **52,597,734 bytes (~50.16 MB)**
* **fpgen Model**:
  * Asset: `model-release.zip`
  * Size: **1,564,571 bytes (~1.49 MB)**
* **Default Addons**:
  * uBlock Origin XPI: **~3.5 MB**

**Total Download Size**: **~1.29 GB**

---

## 2. Python & Playwright Constraints

### Version Requirements
* **Python**: Camoufox requires `>=3.10, <4.0`.
* **Playwright**: Camoufox 0.5.7 strictly pins `playwright (<1.63)` due to breaking internal Juggler schema changes in Playwright 1.63 (`Browser.setDefaultViewport`).

### Environment Discrepancy
* **Host / Python 3.11**:
  * Python: `3.11.11`
  * Camoufox `0.5.7` and `playwright 1.62.0` are installed here.
* **Auto-FreeCF Venv (`/home/amen/.local/share/auto-freecf/venv`)**:
  * Python: `3.13.15`
  * Camoufox is **not** installed.
  * `patchright 1.63.0` (stealth fork of Playwright 1.63) is installed.
  * Installing Camoufox in this venv would install standard `playwright 1.62.0`, which causes dependency/namespace tensions with `patchright 1.63.0`.
  * Recommendation: Run Camoufox flows either under Python 3.11 directly, or create an isolated sub-venv for Camoufox with `python3.11 -m venv`.

---

## 3. Geolocation Spoofing & Fingerprint Controls

Camoufox provides native, C++-level anti-fingerprinting. The relevant kwargs for `AsyncCamoufox(...)` / `launch_options(...)` are:

| Kwarg | Type / Example | What It Does & Why It Matters for ChatGPT/OpenAI |
|---|---|---|
| `geoip` | `bool \| str` (`True` or `"1.2.3.4"`) | Calculates latitude, longitude, timezone, country, and locale from IP (or proxy exit IP). Automatically sets `permissions.default.geo = 1` so `navigator.geolocation` works without browser prompts, and synchronizes WebRTC candidate IPs to match the external IP. |
| `geoip_db` | `str` (optional) | Specifies the local MaxMind database source (defaults to "GeoIP AIO by daijro"). |
| `locale` | `str \| list[str]` (`"en-US"`) | Configures `navigator.language`, `navigator.languages`, `Accept-Language` headers, and the JavaScript `Intl` API formatting. |
| `timezone` | `str` (`"America/New_York"`) | Sets the browser's internal timezone (inferred automatically if `geoip=True`). Prevents timezone/IP mismatch leaks. |
| `humanize` | `bool \| float` (`True` or `1.5`) | Patches mouse movement at the browser engine level using natural Bezier curve trajectories, acceleration/deceleration, and micro-jitter (max duration in seconds). Crucial for bypassing OpenAI / SheerID behavioral bot scoring. |
| `proxy` | `dict` (`{"server": "http://..."}`) | Routes traffic through an HTTP/SOCKS5 proxy. When paired with `geoip=True`, Camoufox queries the proxy's exit IP and auto-spoofs geolocation/timezone to match the proxy. |
| `os` | `str` (`"windows"`, `"macos"`, `"linux"`) | Sets target OS fingerprint (User-Agent, `navigator.platform`, `oscpu`, system fonts, client hints, audio buffer characteristics). Default randomly samples desktop OSes. |
| `fonts` | `list[str]` | Custom font list exposed to font enumeration detection. Defaults to an OS-coherent random font subset. |
| `custom_fonts_only` | `bool` (`False`) | When `True`, suppresses all OS bundled fonts so only explicitly passed fonts are detected. |
| `block_images` | `bool` (`False`) | Prevents loading image assets to conserve bandwidth and speed up automation. |
| `block_webrtc` | `bool` (`False`) | Completely disables WebRTC. *(Note: Keeping WebRTC enabled with `geoip=True` is safer, as Camoufox replaces WebRTC ICE candidates with the spoofed IP).* |
| `disable_coop` | `bool` (`True`) | Disables Cross-Origin-Opener-Policy. Allows cross-origin iframe interaction (e.g. Turnstile checkboxes or embedded verification frames). |
| `screen` | `camoufox.fingerprints.Screen` | Constrains generated screen dimensions. |
| `window` | `tuple[int, int]` (`(1280, 800)`) | Sets a fixed browser window dimension instead of randomized dimensions. |
| `fingerprint_preset` | `bool \| dict` | Uses pre-recorded real browser fingerprints instead of synthetic `fpgen` outputs. |
| `firefox_user_prefs` | `dict` | Custom Firefox `about:config` dictionary overrides. |

---

## 4. nodriver -> Camoufox Porting Mapping

Camoufox wraps the asynchronous Playwright API (`AsyncCamoufox` returns Playwright `Browser` / `BrowserContext`).

| Action | nodriver (`k12_nodriver.py`) | Camoufox (Playwright Async) |
|---|---|---|
| **Launch** | `browser = await uc.start(headless=headless, lang="en-US", browser_args=args)` | `async with AsyncCamoufox(headless=False, geoip=True, humanize=True, os="windows", proxy=p) as browser:` |
| **New Page** | `tab = browser.main_tab` | `page = await browser.new_page()` |
| **Goto** | `await tab.get(url)` | `await page.goto(url, wait_until="domcontentloaded")` |
| **Element Query** | `el = await tab.select(selector)` or `_query_all(tab, selector)` | `locator = page.locator(selector)` |
| **Element Click** | `_cdp_click_xy(tab, x, y)` (custom CDP `Input.dispatchMouseEvent` sequence) | `await locator.click()` or `await page.mouse.click(x, y)` |
| **Typing into React Inputs** | `cdp_type(tab, selector, text)` (CDP `dispatchKeyEvent` Ctrl+A + Delete, then `Input.insertText`) | `await locator.fill(text)` *(auto-clears and dispatches trusted input/change events)* or `await page.keyboard.insert_text(text)` |
| **Sequential Typing** | Per-character loop with CDP `insert_text` | `await locator.press_sequentially(text, delay=50)` |
| **Current URL** | `await js(tab, "location.href", "")` | `page.url` *(direct synchronous property)* |
| **JS Evaluation** | `await tab.evaluate(expr, return_by_value=True)` *(required unwrapping `RemoteObject`)* | `val = await page.evaluate("() => { return ... }")` *(returns native Python dict/str/bool directly, resolves Promises automatically)* |
| **Cookies** | `await browser.cookies.get_all()` | `cookies = await page.context.cookies()` |
| **Popups / New Tabs** | Hooked `window.open` via JS monkey-patch (`install_popup_hook`) and scanned `browser.targets` | Native `async with page.expect_popup() as pinfo: await locator.click()`; `popup = await pinfo.value; url = popup.url` |
| **Close Extra Tabs** | `keep_only(browser, keep_tab)` looping over `browser.targets` | `for p in page.context.pages: if p != page: await p.close()` |

---

## 5. Mouse Event Mechanism: CDP vs Playwright `page.mouse`

### Does Camoufox support CDP `Input.dispatchMouseEvent`?
* **No.** CDP (*Chrome DevTools Protocol*) is proprietary to Chromium-based browsers.
* Camoufox is a heavily patched fork of **Mozilla Firefox (Gecko)**. Playwright communicates with Firefox via Mozilla's internal **Juggler** protocol, not CDP.
* Calling `context.new_cdp_session(page)` on Camoufox raises an error: *"CDP sessions are only supported on Chromium-based browsers."*

### Why Playwright's `page.mouse` and `locator.click()` Work for React:
1. **The Synthetic Event Problem**: In browser JavaScript, calling `element.click()` or `dispatchEvent(new MouseEvent(...))` produces an untrusted event where `event.isTrusted === false`. React 17+ event delegates and anti-bot systems (Cloudflare, SheerID, Arkose) check `event.isTrusted` and reject synthetic clicks.
2. **Playwright Dispatches Real Protocol Events**: Playwright's `page.mouse.move()`, `page.mouse.down()`, `page.mouse.up()`, and `locator.click()` do **not** run JavaScript synthetic clicks. Instead, Juggler injects native widget events directly into Gecko's OS-level event pump (`nsIWidget`).
3. **`event.isTrusted === true`**: The Firefox rendering engine processes these as genuine physical inputs, marking `event.isTrusted = true`. React captures and processes them identically to CDP mouse events.
4. **Camoufox C++ Humanize**: In nodriver, `_cdp_click_xy` jumps instantaneously to coordinates. Camoufox implements `humanize=True` inside its Gecko C++ core, moving the cursor along randomized Bezier curves with natural acceleration, which is significantly more stealthy than nodriver's raw clicks.

---

## 6. Effort Estimate & Risks

### Effort Estimate
* **Scope**: Refactoring `/home/amen/Auto-FreeCF/scripts/k12_nodriver.py` (~913 lines) into `k12_camoufox.py`.
* **Development Time**: **1 – 2 engineering days**.
  * Simplifying code: ~200 lines of brittle nodriver boilerplate (`install_popup_hook`, `_query_all`, `_cdp_click_xy`, `RemoteObject` unwrappers) can be deleted in favor of native Playwright calls (`expect_popup()`, `locator.click()`, `page.evaluate()`).
* **Testing & Tuning**: **1 day** (testing SheerID redirection, OpenAI OTP reception, and token capture).

### Operational Risks & Considerations
1. **Disk and Bandwidth Footprint**:
   * Initial download is **~1.29 GB** compressed.
   * Extracted Firefox binary consumes **~2.5 – 3 GB** in `~/.cache/camoufox`. On low-disk VPS environments, this can exhaust storage.
2. **Playwright Version Pin (`playwright <1.63`)**:
   * The project venv (`/home/amen/.local/share/auto-freecf/venv`) currently has `patchright 1.63.0` under Python 3.13.
   * Camoufox 0.5.7 breaks on Playwright >= 1.63 due to Juggler schema changes. Running Camoufox requires pinning `playwright==1.62.0`. It should be run in a separate environment or python3.11 to avoid clobbering patchright.
3. **Headless Execution on Linux**:
   * Running pure headless (`headless=True`) on Firefox leaks headless canvas/CSS properties.
   * Camoufox requires `headless="virtual"` on Linux, which spins up `Xvfb` (X virtual framebuffer). `Xvfb` is verified installed on this system (`/usr/bin/Xvfb`), but headless container deployments must ensure `xorg-x11-server-Xvfb` is installed.
4. **Site Behavioral Variances (Gecko vs Blink)**:
   * ChatGPT/OpenAI and SheerID serve slightly different bundles or login paths depending on User-Agent. Testing is required to verify that SheerID's iframe/redirect flows complete smoothly on Gecko.

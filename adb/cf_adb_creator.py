"""
CF Auto-Account Creator via ADB + CDP (Kiwi Browser)
=====================================================
Jalankan di laptop Windows yang terhubung ke HP Android via USB.
Requirements: adb, python 3.10+, websocket-client

Setup awal:
  1. pip install websocket-client
  2. Install adb (Android SDK Platform Tools)
  3. HP: Enable USB Debugging di Developer Options
  4. HP: Install Kiwi Browser dari Play Store
  5. HP: Di Kiwi, login ke akun Google yang akan dipakai

Usage:
  python cf_adb_creator.py
"""

import subprocess
import json
import time
import sys
import os
import re
from websocket import create_connection

# ═══════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════

# Daftar akun Google untuk login CF.
# JANGAN hardcode kredensial di sini — isi dari env atau file lokal yang di-gitignore.
#   export CF_ADB_ACCOUNTS="a@x.com,b@x.com"
#   atau taruh satu email per baris di ~/.config/auto-freecf/adb_accounts.txt
def _load_accounts() -> list[str]:
    env = os.environ.get("CF_ADB_ACCOUNTS", "").strip()
    if env:
        return [e.strip() for e in env.split(",") if e.strip()]

    path = os.environ.get(
        "CF_ADB_ACCOUNTS_FILE",
        os.path.join(os.path.expanduser("~"), ".config", "auto-freecf", "adb_accounts.txt"),
    )
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        return []


ACCOUNTS = _load_accounts()

CDP_PORT = 9222
DEVTOOLS_SOCKET = "chrome_devtools_remote"
OUTPUT_FILE = "cf_accounts.json"
SCREENSHOT_DIR = "screenshots"
TIMEOUT = 30

# ═══════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════

def run(cmd, shell=True):
    """Run shell command, return output"""
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=10)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1

def adb(cmd):
    """Wrapper for adb commands"""
    out, err, code = run(f"adb {cmd}")
    if code != 0 and err:
        print(f"  [ADB ERROR] {err}")
    return out

def setup_cdp():
    """Forward ADB port to Kiwi's DevTools and connect"""
    print("[*] Setting up ADB → CDP bridge...")

    # Kill existing forwards
    adb("forward --remove-all")
    
    # Check devices
    devices = adb("devices")
    if "device" not in devices:
        print("[!] No device connected! Pastikan:")
        print("    1. HP terkoneksi via USB")
        print("    2. USB Debugging enabled")
        print("    3. Accept RSA fingerprint di HP")
        sys.exit(1)
    
    print(f"  Device: {devices.strip()}")

    # Force-start Kiwi browser if not running
    adb("shell am start -n com.kiwibrowser.browser/com.google.android.apps.chrome.Main")
    time.sleep(3)

    # Forward DevTools socket
    out = adb(f"forward tcp:{CDP_PORT} localabstract:{DEVTOOLS_SOCKET}")
    time.sleep(1)

    # Verify CDP is accessible
    for attempt in range(5):
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json/version", timeout=3)
            data = json.loads(resp.read())
            print(f"  CDP connected! Browser: {data.get('Browser', 'unknown')}")
            return True
        except Exception as e:
            print(f"  Retry {attempt+1}/5: {e}")
            time.sleep(2)
    
    print("[!] Gagal konek ke Kiwi DevTools. Coba:")
    print("    1. Buka Kiwi Browser di HP")
    print("    2. Tutup & buka lagi")
    sys.exit(1)

def get_page_ws_url():
    """Get WebSocket URL for the active page"""
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5)
        pages = json.loads(resp.read())
        for p in pages:
            if p.get("type") == "page":
                return p["webSocketDebuggerUrl"], p["id"]
        # Create new page
        resp2 = urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json/new?url=about:blank", timeout=5)
        new_page = json.loads(resp2.read())
        return new_page["webSocketDebuggerUrl"], new_page["id"]
    except Exception as e:
        print(f"[!] Gagal dapat halaman: {e}")
        return None, None

def screenshot(ws, name):
    """Take screenshot via CDP"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    msg = json.dumps({"id": 99, "method": "Page.captureScreenshot", "params": {"format": "png"}})
    ws.send(msg)
    resp = json.loads(ws.recv())
    if "result" in resp and "data" in resp["result"]:
        import base64
        path = f"{SCREENSHOT_DIR}/{name}.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(resp["result"]["data"]))
        print(f"  📸 Screenshot: {path}")
        return path
    return None

def cdp_cmd(ws, method, params=None, timeout=TIMEOUT):
    """Send CDP command, return result"""
    msg_id = int(time.time() * 1000) % 100000
    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    ws.send(json.dumps(payload))
    
    ws.settimeout(timeout)
    try:
        while True:
            resp = json.loads(ws.recv())
            if resp.get("id") == msg_id:
                if "error" in resp:
                    print(f"  [CDP ERROR] {method}: {resp['error']}")
                    return None
                return resp.get("result", {})
            # Non-matching message (event), ignore
    except Exception as e:
        print(f"  [CDP TIMEOUT] {method}: {e}")
        return None

def js(ws, expression, timeout=TIMEOUT):
    """Evaluate JavaScript in the page, return parsed result"""
    result = cdp_cmd(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    }, timeout=timeout)
    if result and "result" in result:
        val = result["result"].get("value")
        return val
    return None

def wait_for_element(ws, selector, timeout=15):
    """Wait for element to appear in DOM"""
    start = time.time()
    while time.time() - start < timeout:
        found = js(ws, f"document.querySelector('{selector}') !== null", timeout=3)
        if found:
            return True
        time.sleep(1)
    return False

def wait_for_navigation(ws, timeout=20):
    """Wait for page to finish loading"""
    start = time.time()
    while time.time() - start < timeout:
        ready = js(ws, "document.readyState", timeout=3)
        if ready == "complete":
            # Extra wait for JS to settle
            time.sleep(2)
            return True
        time.sleep(1)
    return False

# ═══════════════════════════════════════════
# MAIN FLOW
# ═══════════════════════════════════════════

def login_cf_with_google(ws, target_email):
    """
    Steps:
    1. Navigate to dash.cloudflare.com/login
    2. Click "Sign in with Google"
    3. Google OAuth screen → select account
    4. Wait for redirect back to CF dashboard
    5. Extract cookies + account info
    """
    print(f"\n{'='*50}")
    print(f"[*] Login CF dengan: {target_email}")
    print(f"{'='*50}")

    # Enable Network domain for cookie access
    cdp_cmd(ws, "Network.enable")
    cdp_cmd(ws, "Page.enable")
    cdp_cmd(ws, "Runtime.enable")

    # Step 1: Navigate to CF login
    print("  [1] Navigate ke CF login...")
    cdp_cmd(ws, "Page.navigate", {"url": "https://dash.cloudflare.com/login"})
    time.sleep(5)
    
    # Check if we hit Cloudflare challenge
    body_text = js(ws, "document.body ? document.body.innerText.substring(0, 200) : 'no body'")
    print(f"  Page: {body_text}")

    # Click "Sign in with Google" button
    print("  [2] Click Sign in with Google...")
    google_btn_selectors = [
        "a[href*='accounts.google.com']",
        "a[href*='google']",
        "button:has-text('Google')",
        "[data-provider='google']",
        "button.google",
        "a.google",
    ]
    
    clicked = False
    for sel in google_btn_selectors:
        try:
            if js(ws, f"document.querySelector('{sel}') !== null", timeout=2):
                js(ws, f"document.querySelector('{sel}').click()")
                clicked = True
                print(f"  Clicked: {sel}")
                break
        except:
            continue
    
    if not clicked:
        # Fallback: find any link/button containing "Google"
        found = js(ws, """
            (() => {
                const els = [...document.querySelectorAll('a, button, [role="button"]')];
                const match = els.find(e => e.textContent.toLowerCase().includes('google'));
                if (match) { match.click(); return match.textContent.trim(); }
                return null;
            })()
        """, timeout=5)
        if found:
            print(f"  Clicked (fallback): {found}")
            clicked = True
    
    if not clicked:
        print("[!] Gak nemu tombol Google sign-in. Screenshoot...")
        screenshot(ws, f"login_{target_email.replace('@','_')}")
        return None

    # Step 3: Wait for Google OAuth page
    time.sleep(5)
    current_url = js(ws, "window.location.href")
    print(f"  Current URL: {current_url}")

    # Are we on Google?
    if "accounts.google.com" in (current_url or ""):
        print("  [3] Di Google OAuth page...")
        
        # Click the correct account or enter email
        time.sleep(2)
        account_found = js(ws, f"""
            (() => {{
                const els = [...document.querySelectorAll('[data-email], [data-identifier], div[role="link"]')];
                for (const el of els) {{
                    if (el.textContent.includes('{target_email}')) {{
                        el.click();
                        return true;
                    }}
                }}
                return false;
            }})()
        """, timeout=5)
        
        if account_found:
            print(f"  Klik akun: {target_email}")
        else:
            # Maybe need to type email
            print("  Akun gak muncul di list, coba ketik email...")
            email_input = wait_for_element(ws, 'input[type="email"]', timeout=5)
            if email_input:
                js(ws, f"""
                    const inp = document.querySelector('input[type="email"]');
                    inp.value = '{target_email}';
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                """)
                time.sleep(1)
                # Click "Next"
                js(ws, """
                    const btns = [...document.querySelectorAll('button')];
                    const next = btns.find(b => b.textContent.includes('Next') || b.textContent.includes('Berikutnya'));
                    if (next) next.click();
                """)
            else:
                print("[!] Gak nemu email input di Google page")
                screenshot(ws, f"google_{target_email.replace('@','_')}")
                return None

        # Wait for redirect back to CF
        print("  [4] Tunggu redirect ke CF...")
        time.sleep(8)
        
        # Check if we need to click "Continue" on consent screen
        current_url = js(ws, "window.location.href")
        if "consent" in (current_url or "").lower() or "accounts.google.com" in (current_url or ""):
            print("  [4a] Klik Continue untuk consent...")
            # Click the blue "Continue" button
            js(ws, """
                const btns = [...document.querySelectorAll('button')];
                const cnt = btns.find(b => 
                    b.textContent.includes('Continue') || 
                    b.textContent.includes('Allow') ||
                    b.textContent.includes('Lanjutkan') ||
                    b.textContent.includes('Izinkan')
                );
                if (cnt) cnt.click();
            """)
            time.sleep(5)
        
        # Wait for CF dashboard to load
        for _ in range(10):
            time.sleep(2)
            current_url = js(ws, "window.location.href")
            if current_url and "dash.cloudflare.com" in current_url:
                break
        
        current_url = js(ws, "window.location.href")
        if not current_url or "login" in current_url:
            print(f"[!] Masih di login page: {current_url}")
            screenshot(ws, f"failed_{target_email.replace('@','_')}")
            return None

    elif "dash.cloudflare.com" in (current_url or ""):
        print("  [3] Sudah di CF (mungkin sudah login)")
    else:
        print(f"[!] URL tak terduga: {current_url}")
        screenshot(ws, f"unexpected_{target_email.replace('@','_')}")
        return None

    # Step 5: Extract cookies
    print("  [5] Extract cookies...")
    cookies = cdp_cmd(ws, "Network.getCookies")
    
    # Extract user info
    print("  [6] Extract account info...")
    account_info = js(ws, """
        (() => {
            try {
                // Try to get from CF's embedded state
                const scripts = [...document.querySelectorAll('script')];
                for (const s of scripts) {
                    const match = s.textContent.match(/window\\.__CF\\s*=\\s*({.+?});/);
                    if (match) return JSON.parse(match[1]);
                }
            } catch(e) {}
            
            // Try meta tags
            const userId = document.querySelector('meta[name="user-id"]')?.content;
            const userEmail = document.querySelector('meta[name="user-email"]')?.content;
            
            return {
                url: window.location.href,
                title: document.title,
                userId: userId,
                email: userEmail,
            };
        })()
    """)
    
    # Try to get account ID from URL
    current_url = js(ws, "window.location.href") or ""
    account_id = None
    match = re.search(r'/([a-f0-9]{32})/', current_url)
    if match:
        account_id = match.group(1)

    result = {
        "email": target_email,
        "cookies": cookies.get("cookies", []) if cookies else [],
        "cf_url": current_url,
        "account_id": account_id,
        "info": account_info,
    }
    
    print(f"  ✅ Done! Account ID: {account_id}")
    return result

def create_api_token(ws):
    """Create API token via CF dashboard API"""
    print("  [*] Create API token...")
    
    # Navigate to API tokens page
    cdp_cmd(ws, "Page.navigate", {"url": "https://dash.cloudflare.com/profile/api-tokens"})
    time.sleep(5)
    
    # Click "Create Token"
    js(ws, """
        const btns = [...document.querySelectorAll('button, a')];
        const create = btns.find(b => b.textContent.includes('Create') && b.textContent.includes('Token'));
        if (create) create.click();
    """)
    time.sleep(3)
    
    # Pilih template "Create Custom Token" atau langsung dari template
    js(ws, """
        // Click "Get started" on Custom Token
        const rows = [...document.querySelectorAll('tr, [role="row"]')];
        const custom = rows.find(r => r.textContent.includes('Custom') || r.textContent.includes('custom'));
        if (custom) {
            const btn = custom.querySelector('button, a');
            if (btn) btn.click();
        }
    """)
    time.sleep(3)
    
    # Set permissions (All zones, All permissions)
    js(ws, """
        // Set token name
        const name = document.querySelector('input[name="name"]');
        if (name) { name.value = 'workers-ai-token'; name.dispatchEvent(new Event('input', {bubbles: true})); }
        
        // Set all permissions
        const selects = [...document.querySelectorAll('select')];
        for (const sel of selects) {
            const opts = [...sel.options];
            if (opts.length > 1) sel.value = opts[opts.length - 1].value;
            sel.dispatchEvent(new Event('change', {bubbles: true}));
        }
    """)
    time.sleep(2)
    
    # Continue to summary and create
    js(ws, """
        const btns = [...document.querySelectorAll('button')];
        const cont = btns.find(b => b.textContent.includes('Continue') || b.textContent.includes('Summary'));
        if (cont) cont.click();
    """)
    time.sleep(3)
    
    js(ws, """
        const btns = [...document.querySelectorAll('button')];
        const create = btns.find(b => b.textContent.includes('Create Token'));
        if (create) create.click();
    """)
    time.sleep(3)
    
    # Get the token value (only shown once!)
    token = js(ws, """
        (() => {
            // Try various selectors for token display
            const code = document.querySelector('code, .token-value, [data-token]');
            if (code) return code.textContent.trim();
            
            // Try input fields
            const inputs = [...document.querySelectorAll('input[readonly]')];
            for (const i of inputs) {
                if (i.value && i.value.length > 40) return i.value;
            }
            return null;
        })()
    """)
    
    return token

# ═══════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════

def main():
    print("""
╔════════════════════════════════════════╗
║   CF AUTO ACCOUNT CREATOR (ADB+CDP)   ║
║   Pastikan:                           ║
║   • HP Android terkoneksi USB         ║
║   • USB Debugging ON                  ║
║   • Kiwi Browser terpasang            ║
║   • Google account sudah login        ║
╚════════════════════════════════════════╝
""")
    
    # Setup CDP
    if not ACCOUNTS:
        print("[!] Tidak ada akun. Set CF_ADB_ACCOUNTS atau isi "
              "~/.config/auto-freecf/adb_accounts.txt (satu email per baris).")
        return

    if not setup_cdp():
        return
    
    # Load existing results if any
    results = []
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            results = json.load(f)
        done_emails = [r.get("email") for r in results]
        print(f"[*] Loaded {len(results)} existing accounts")
    else:
        done_emails = []
    
    # Get CDP WebSocket
    ws_url, page_id = get_page_ws_url()
    if not ws_url:
        print("[!] Gagal dapat WebSocket URL")
        return
    
    print(f"[*] Page ID: {page_id}")
    
    # Process each account
    for email in ACCOUNTS:
        if email in done_emails:
            print(f"\n[SKIP] {email} — sudah diproses")
            continue
        
        # Connect WebSocket
        ws = create_connection(ws_url, timeout=TIMEOUT)
        
        try:
            # 1. Login CF with Google
            result = login_cf_with_google(ws, email)
            
            if result and result.get("cookies"):
                # 2. Create API token
                token = create_api_token(ws)
                if token:
                    result["api_token"] = token
                    print(f"  🎉 Token: {token[:20]}...")
                else:
                    print("  ⚠️ Gagal bikin token")
                
                results.append(result)
                
                # Save after each account
                with open(OUTPUT_FILE, "w") as f:
                    json.dump(results, f, indent=2)
                print(f"  💾 Saved to {OUTPUT_FILE}")
            else:
                print(f"  ❌ Gagal login {email}")
            
            # Sign out for next account
            js(ws, """
                // Clear session
                document.cookie.split(";").forEach(c => {
                    document.cookie = c.trim().split("=")[0] + "=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/";
                });
            """)
            cdp_cmd(ws, "Network.clearBrowserCookies")
            cdp_cmd(ws, "Page.navigate", {"url": "https://dash.cloudflare.com/logout"})
            time.sleep(2)
            
        except Exception as e:
            print(f"  [EXCEPTION] {email}: {e}")
            screenshot(ws, f"error_{email.replace('@','_')}")
        finally:
            ws.close()
            time.sleep(2)
    
    # Summary
    print(f"\n{'='*50}")
    print(f" DONE! {len(results)}/{len(ACCOUNTS)} akun berhasil diproses")
    print(f" Output: {OUTPUT_FILE}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()

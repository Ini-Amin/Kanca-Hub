#!/usr/bin/env python3
"""
GODMODE: Cloudflare Turnstile Managed Challenge Bypass
======================================================
Achieves cf_clearance on dash.cloudflare.com/login using:
- Patchright (Playwright fork) with Chrome 150
- Anti-detection init scripts
- Native API fetch from within browser context

REQUIREMENTS:
    pip install patchright curl_cffi
    patchright install chrome

USAGE:
    python3 godmode_cf_bypass.py

OUTPUT:
    Prints cf_clearance cookie + tests API access
"""

import os, json, time, sys

os.environ['DISPLAY'] = os.environ.get('DISPLAY', ':99')

# Ensure Xvfb is running
if os.system('pgrep Xvfb >/dev/null 2>&1') != 0:
    os.system('Xvfb :99 -screen 0 1920x1080x24 -ac &>/dev/null &')
    time.sleep(2)

from patchright.sync_api import sync_playwright


# ─── Anti-Detection Init Script ───────────────────────────────────────────
STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => false});
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

// Override permissions query
const _query = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : _query(p);
"""


def bypass_turnstile():
    """Launch stealth browser, get cf_clearance, test API access."""
    
    p = sync_playwright().start()
    
    try:
        b = p.chromium.launch(
            headless=False,
            channel='chrome',  # Chrome 150+
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--window-size=1920,1080',
                '--disable-dev-shm-usage',
            ]
        )
        
        ctx = b.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/150.0.0.0 Safari/537.36'
            ),
            locale='en-US',
            timezone_id='America/New_York',
        )
        
        pg = ctx.new_page()
        pg.add_init_script(STEALTH_INIT)
        
        print('[1] Loading CF Login...', flush=True)
        pg.goto(
            'https://dash.cloudflare.com/login',
            timeout=60000,
            wait_until='networkidle'
        )
        time.sleep(8)
        
        # Check for cf_clearance
        cookies = ctx.cookies()
        cf_clr = [c for c in cookies if c['name'] == 'cf_clearance']
        
        if not cf_clr:
            print('[FAIL] No cf_clearance — blocked', flush=True)
            pg.screenshot(path='/tmp/cf_blocked.png')
            return None
        
        clearance = cf_clr[0]['value']
        print(f'[OK] cf_clearance: {clearance[:60]}...', flush=True)
        
        # Test API access from within browser
        print('\n[2] Testing API access...', flush=True)
        
        for ep, label in [
            ('/api/v4/user', 'User Info'),
            ('/api/v4/accounts', 'Accounts'),
        ]:
            try:
                resp = pg.evaluate(f"""
                    async () => {{
                        const r = await fetch('https://dash.cloudflare.com{ep}', {{
                            credentials: 'include',
                            headers: {{'Accept': 'application/json'}}
                        }});
                        return {{status: r.status, data: await r.json()}};
                    }}
                """)
                
                if resp['status'] == 200:
                    data = resp['data']
                    result = data.get('result', {})
                    if isinstance(result, list):
                        print(f'  [{label}] {len(result)} items', flush=True)
                    else:
                        print(f'  [{label}] {str(list(result.keys()))}', flush=True)
                else:
                    err = resp['data'].get('errors', [{}])[0].get('message', '?')
                    status_code = resp['status']
                    print(f'  [{label}] {status_code}: {err}', flush=True)
                    
            except Exception as e:
                print(f'  [{label}] ERROR: {e}', flush=True)
        
        pg.screenshot(path='/tmp/cf_bypass_result.png')
        print(f'\n[DONE] Screenshot: /tmp/cf_bypass_result.png', flush=True)
        
        return clearance
        
    finally:
        try:
            b.close()
        except:
            pass
        try:
            p.stop()
        except:
            pass


if __name__ == '__main__':
    result = bypass_turnstile()
    if result:
        print(f'\n✓ CF_CLEARANCE={result}')
        sys.exit(0)
    else:
        print('\n✗ BYPASS FAILED')
        sys.exit(1)

"""Temp email generator — creates disposable addresses via mail API.

Architecture:
  1. mail_api (from config)     → user's primary URL
  2. mail_fallback (from config) → user's backup URL
  3. PUBLIC_RELAY (hardcoded)    → community relay, always available
"""

import httpx
import os
import random
import sys
from pathlib import Path
from typing import Optional

try:
    from gateway_session import gateway_headers, is_gateway
except ImportError:
    try:
        _sp = str(Path(__file__).resolve().parent.parent.parent / "scripts")
        if _sp not in sys.path:
            sys.path.insert(0, _sp)
        from gateway_session import gateway_headers, is_gateway
    except Exception:
        def is_gateway(p=None): return False
        def gateway_headers(s=None): return {}

# Hardcoded community relay — auto-updated, always available
PUBLIC_RELAY = "https://convergence-lobby-portal-planes.trycloudflare.com/new_address"


class EmailGenerator:
    """Generate temporary email addresses via mail API.
    
    Auto-falls back through three tiers:
      1. User-configured mail_api
      2. User-configured mail_fallback (if set)
      3. Hardcoded public relay (always)
    
    This ensures the tool works out-of-the-box for:
      - Browser Farm users (localhost works)
      - Laptop users with tunnel config
      - First-time users with no config at all
    """

    def __init__(
        self,
        api_url: str,
        domains: list[str],
        timeout: int = 60,
        fallback_url: Optional[str] = None,
        api_key: Optional[str] = None,
        proxy: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        self.api_url = api_url
        self.fallback_url = fallback_url
        self.domains = domains
        self.timeout = timeout
        self.api_key = api_key
        self.proxy = proxy
        self.session_id = session_id
        self._client: Optional[httpx.Client] = None
        self._active_url: str = api_url
        self._tier_used: str = "primary"

    def _headers(self) -> dict:
        """Auth headers for a private backend (e.g. Supabase temp-mail-api)."""
        h = {"x-api-key": self.api_key} if self.api_key else {}
        if self.proxy and is_gateway(self.proxy):
            h.update(gateway_headers(self.session_id))
        return h

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            client_kw = {"timeout": self.timeout}
            if self.proxy:
                client_kw["proxy"] = self.proxy
            self._client = httpx.Client(**client_kw)
        return self._client

    def _try_create(self, url: str, username: Optional[str], domain: str) -> dict:
        """Try creating email at given URL."""
        payload = {"domain": domain}
        if username:
            payload["name"] = username

        r = self.client.post(url, json=payload, headers=self._headers())
        r.raise_for_status()
        data = r.json()

        return {
            "email": data["address"],
            "jwt": data["jwt"],
            "address": data["address"],
            "domain": domain,
        }

    def create(self, username: Optional[str] = None, domain: Optional[str] = None) -> dict:
        """Create a new temporary email address.

        Tries URLs in this order:
          1. api_url (config.json → mail_api)
          2. fallback_url (config.json → mail_fallback)
          3. PUBLIC_RELAY (hardcoded community relay)

        Returns:
            dict with 'email', 'jwt', 'address', 'domain'
        """
        if domain is None:
            domain = random.choice(self.domains)

        # Build URL chain — deduplicate, always include public relay last
        urls: list[str] = []
        seen: set[str] = set()
        for url in [self.api_url, self.fallback_url, PUBLIC_RELAY]:
            if url and url not in seen:
                urls.append(url)
                seen.add(url)

        errors: list[str] = []
        for url in urls:
            tier = "primary" if url == self.api_url else (
                "fallback" if url == self.fallback_url else "public-relay"
            )
            # Retry transient network errors (fresh WARP tunnels often reset the
            # first connection): up to 3 tries with backoff.
            last_exc = None
            for attempt in range(3):
                try:
                    result = self._try_create(url, username, domain)
                    self._active_url = url
                    self._tier_used = tier
                    if tier != "primary":
                        print(f"  ⚠️  Using {tier} relay: {url}")
                    return result
                except httpx.HTTPStatusError as e:
                    # Endpoint-level error — don't fallback, surface immediately.
                    raise RuntimeError(
                        f"Mail API returned HTTP {e.response.status_code} from {tier} ({url})"
                    ) from e
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                        httpx.RemoteProtocolError, httpx.ReadError) as e:
                    last_exc = e
                    if attempt < 2:
                        import time as _t
                        _t.sleep(2 * (attempt + 1))
                        continue
                    break
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                    break
            if last_exc is not None:
                errors.append(f"{tier} ({url}): {type(last_exc).__name__}")
                continue

        # All URLs failed
        raise ConnectionError(
            "All mail relays failed. Network may be down or all relays unreachable.\n"
            + "  Errors:\n    " + "\n    ".join(errors)
            + "\n\n  Fix: check your internet connection, or set mail_api to a working relay in config.json"
        )

    def check_inbox(self, jwt: str, limit: int = 20, offset: int = 0) -> list:
        """Check inbox for received emails."""
        base = self._active_url.replace("/new_address", "")
        headers = {"Authorization": f"Bearer {jwt}", **self._headers()}
        r = self.client.get(
            f"{base}/parsed_mails",
            params={"limit": limit, "offset": offset},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("results") or data.get("items") or []
        return data

    def get_mail(self, jwt: str, mail_id: str | int) -> dict:
        """Get a parsed email by id."""
        base = self._active_url.replace("/new_address", "")
        headers = {"Authorization": f"Bearer {jwt}", **self._headers()}
        r = self.client.get(
            f"{base}/parsed_mail/{mail_id}",
            headers=headers,
        )
        r.raise_for_status()
        return r.json()

    def close(self):
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

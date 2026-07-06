"""Temp email generator — creates disposable addresses via mail API.

Architecture:
  1. mail_api (from config)     → user's primary URL
  2. mail_fallback (from config) → user's backup URL
  3. PUBLIC_RELAY (hardcoded)    → community relay, always available
"""

import httpx
import random
from typing import Optional

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
        timeout: int = 30,
        fallback_url: Optional[str] = None,
    ):
        self.api_url = api_url
        self.fallback_url = fallback_url
        self.domains = domains
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._active_url: str = api_url
        self._tier_used: str = "primary"

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _try_create(self, url: str, username: Optional[str], domain: str) -> dict:
        """Try creating email at given URL."""
        payload = {"domain": domain}
        if username:
            payload["name"] = username

        r = self.client.post(url, json=payload)
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
            try:
                result = self._try_create(url, username, domain)
                self._active_url = url
                self._tier_used = tier
                if tier != "primary":
                    print(f"  ⚠️  Using {tier} relay: {url}")
                return result
            except httpx.ConnectError as e:
                errors.append(f"{tier} ({url}): connection refused")
                continue
            except httpx.ConnectTimeout:
                errors.append(f"{tier} ({url}): timeout")
                continue
            except httpx.HTTPStatusError as e:
                # HTTP errors are endpoint-level — don't fallback, surface immediately
                raise RuntimeError(
                    f"Mail API returned HTTP {e.response.status_code} from {tier} ({url})"
                ) from e

        # All URLs failed
        raise ConnectionError(
            "All mail relays failed. Network may be down or all relays unreachable.\n"
            + "  Errors:\n    " + "\n    ".join(errors)
            + "\n\n  Fix: check your internet connection, or set mail_api to a working relay in config.json"
        )

    def check_inbox(self, jwt: str, limit: int = 20, offset: int = 0) -> list:
        """Check inbox for received emails."""
        base = self._active_url.replace("/new_address", "")
        r = self.client.get(
            f"{base}/parsed_mails",
            params={"limit": limit, "offset": offset},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("results") or data.get("items") or []
        return data

    def get_mail(self, jwt: str, mail_id: str | int) -> dict:
        """Get a parsed email by id."""
        base = self._active_url.replace("/new_address", "")
        r = self.client.get(
            f"{base}/parsed_mail/{mail_id}",
            headers={"Authorization": f"Bearer {jwt}"},
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

#!/usr/bin/env python3
"""Test Cloudflare login approaches"""

from curl_cffi import requests as cffi_requests
import json
import time

email = "your_email@example.com"
password = "your_password"

print(f"=== Testing Cloudflare Login for: {email} ===\n")

# Approach 1: curl_cffi with chrome impersonation
print("=== Approach 1: curl_cffi Chrome impersonation ===")
session = cffi_requests.Session(impersonate="chrome124")

# Get login page first to get cookies
print("1. Getting login page...")
resp = session.get("https://dash.cloudflare.com/login", timeout=30)
print(f"   Status: {resp.status_code}")
print(f"   Cookies: {dict(session.cookies)}")
print(f"   Title check: {'Just a moment' in resp.text}")

# Try the actual login API
print("\n2. Trying login API endpoints...")

# Try different endpoints
endpoints = [
    "https://dash.cloudflare.com/api/v4/login",
    "https://api.cloudflare.com/client/v4/user/login", 
    "https://dash.cloudflare.com/api/login",
    "https://dash.cloudflare.com/auth/login",
]

for endpoint in endpoints:
    try:
        resp = session.post(endpoint, json={"email": email, "password": password}, timeout=15)
        print(f"   {endpoint}")
        print(f"   → Status: {resp.status_code}")
        if resp.text:
            print(f"   → Response: {resp.text[:200]}")
        print()
    except Exception as e:
        print(f"   {endpoint} → Error: {e}\n")

# Approach 2: Try with form data instead of JSON
print("=== Approach 2: Form data login ===")
session2 = cffi_requests.Session(impersonate="chrome124")
resp = session2.get("https://dash.cloudflare.com/login", timeout=30)

try:
    resp = session2.post(
        "https://dash.cloudflare.com/login",
        data={"email": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": "https://dash.cloudflare.com/login"},
        timeout=15
    )
    print(f"   Status: {resp.status_code}")
    print(f"   URL after login: {resp.url}")
    print(f"   Cookies: {dict(session2.cookies)}")
    print(f"   Response: {resp.text[:300]}")
except Exception as e:
    print(f"   Error: {e}")

# Approach 3: Try Cloudflare's GraphQL endpoint
print("\n=== Approach 3: GraphQL endpoint ===")
session3 = cffi_requests.Session(impersonate="chrome124")
try:
    resp = session3.post(
        "https://dash.cloudflare.com/graphql",
        json={
            "query": "query { viewer { accounts { id name } } }"
        },
        headers={"Content-Type": "application/json"},
        timeout=15
    )
    print(f"   Status: {resp.status_code}")
    print(f"   Response: {resp.text[:300]}")
except Exception as e:
    print(f"   Error: {e}")

# Approach 4: Try with pre-login token extraction
print("\n=== Approach 4: Check for pre-login tokens ===")
session4 = cffi_requests.Session(impersonate="chrome124")
resp = session4.get("https://dash.cloudflare.com/login", timeout=30)
# Look for any CSRF tokens or hidden form fields
import re
tokens = re.findall(r'(?:csrf|token|_token|authenticity_token)["\s:=]+["\']([^"\']+)', resp.text, re.IGNORECASE)
print(f"   Found tokens: {tokens}")
# Look for any API endpoints in the page
apis = re.findall(r'(https?://[^"\'>\s]+api[^"\'>\s]*)', resp.text)
print(f"   Found API URLs: {apis[:5]}")

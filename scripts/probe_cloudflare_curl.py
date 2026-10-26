#!/usr/bin/env python3
"""Test Cloudflare login using curl_cffi"""

from curl_cffi import requests
import json

# Test with first account
email = "your_email@example.com"
password = "your_password"

print(f"Testing login for: {email}")

# Create session with browser impersonation
session = requests.Session(impersonate="chrome110")

# First, get the login page to get cookies
print("1. Getting login page...")
resp = session.get("https://dash.cloudflare.com/login")
print(f"   Status: {resp.status_code}")
print(f"   URL: {resp.url}")

# Try to login
print("2. Attempting login...")
login_data = {
    "email": email,
    "password": password
}

resp = session.post(
    "https://dash.cloudflare.com/api/v4/user/login",
    json=login_data
)

print(f"   Status: {resp.status_code}")
print(f"   Response: {resp.text[:500]}")

# Try to get user info
print("3. Getting user info...")
resp = session.get("https://dash.cloudflare.com/api/v4/user")
print(f"   Status: {resp.status_code}")
print(f"   Response: {resp.text[:500]}")

# Try to get accounts
print("4. Getting accounts...")
resp = session.get("https://dash.cloudflare.com/api/v4/accounts")
print(f"   Status: {resp.status_code}")
print(f"   Response: {resp.text[:500]}")

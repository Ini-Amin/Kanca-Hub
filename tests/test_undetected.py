#!/usr/bin/env python3
"""Test with undetected-chromedriver"""

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

email = "your_email@example.com"
password = "your_password"

print(f"=== Testing with undetected-chromedriver ===")
print(f"Email: {email}\n")

# Setup undetected chrome with Playwright's Chromium
options = uc.ChromeOptions()
options.binary_location = "/root/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

print("1. Starting browser...")
driver = uc.Chrome(options=options)

try:
    print("2. Navigating to login page...")
    driver.get("https://dash.cloudflare.com/login")
    
    print("3. Waiting for page to load...")
    time.sleep(5)
    
    print(f"   Current URL: {driver.current_url}")
    print(f"   Title: {driver.title}")
    
    # Wait for email input
    print("4. Waiting for email input...")
    wait = WebDriverWait(driver, 30)
    
    # Try multiple selectors for email
    email_selectors = [
        (By.NAME, "email"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name*='email']"),
        (By.CSS_SELECTOR, "input[placeholder*='email']"),
        (By.CSS_SELECTOR, "input[placeholder*='Email']"),
    ]
    
    email_input = None
    for selector_type, selector_value in email_selectors:
        try:
            email_input = wait.until(EC.presence_of_element_located((selector_type, selector_value)))
            print(f"   Found email input with: {selector_type}={selector_value}")
            break
        except:
            continue
    
    if not email_input:
        print("   ❌ Could not find email input")
        print(f"   Page source snippet: {driver.page_source[:500]}")
        raise Exception("Email input not found")
    
    # Fill email
    print("5. Filling email...")
    email_input.clear()
    email_input.send_keys(email)
    
    # Find and fill password
    print("6. Filling password...")
    password_input = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    password_input.clear()
    password_input.send_keys(password)
    
    # Find and click login button
    print("7. Clicking login button...")
    login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    login_button.click()
    
    # Wait for redirect
    print("8. Waiting for login to complete...")
    time.sleep(10)
    
    print(f"   Current URL: {driver.current_url}")
    print(f"   Title: {driver.title}")
    
    # Check if login successful
    if "login" in driver.current_url:
        print("   ❌ Still on login page")
    else:
        print("   ✓ Login successful!")
        
        # Extract account ID from URL
        if "/accounts/" in driver.current_url or driver.current_url.count("/") > 3:
            parts = driver.current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_id = parts[1].split("/")[0]
                print(f"   ✓ Account ID: {account_id}")
    
    # Get cookies for later use
    print("\n9. Extracting cookies...")
    cookies = driver.get_cookies()
    print(f"   Found {len(cookies)} cookies")
    for cookie in cookies[:5]:
        print(f"   - {cookie['name']}: {cookie['value'][:50]}...")
    
    print("\n✓ Test completed successfully!")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    
finally:
    print("\n10. Closing browser...")
    driver.quit()

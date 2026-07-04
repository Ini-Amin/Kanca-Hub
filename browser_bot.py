#!/usr/bin/env python3
"""Backward compatibility wrapper - imports from refactored src package"""

import sys
from pathlib import Path

# Add parent directory to path to import src package
parent_dir = Path(__file__).parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Import everything from src package for backward compatibility
from src.browser_bot import CFAutoGrabber, process_accounts
from src.utils import load_accounts, load_proxy_config, save_results

# Make available for direct imports
__all__ = ['CFAutoGrabber', 'process_accounts', 'load_accounts', 'load_proxy_config', 'save_results']

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Cloudflare Account Automation")
    parser.add_argument("--accounts", help="Path to accounts file (JSON or TXT)")
    parser.add_argument("--single", help="Single account in email:password format")
    parser.add_argument("--visible", action="store_true", default=False,
                        help="Show browser window (non-headless)")
    parser.add_argument("--proxy", help="Path to proxy config JSON file")
    parser.add_argument("--login-method", choices=["email", "google"], default="email", 
                       help="Login method: 'email' for email:password, 'google' for Google OAuth")
    
    args = parser.parse_args()
    
    # Load proxy if provided
    proxies = None
    if args.proxy:
        proxies = load_proxy_config(args.proxy)
        if proxies:
            print(f"✓ Loaded {len(proxies)} proxies from {args.proxy}")
            for p in proxies:
                print(f"  → {p.get('server')} ({p.get('country')}/{p.get('city')})")
        else:
            print(f"⚠️  Could not load proxies from {args.proxy}")
    
    # Single account mode
    if args.single:
        if ':' not in args.single:
            print("Error: Invalid format. Use email:password")
            sys.exit(1)
        
        email, password = args.single.split(':', 1)
        email = email.strip()
        password = password.strip()
        
        if not email or not password:
            print("Error: Email and password cannot be empty")
            sys.exit(1)
        
        print(f"Processing single account: {email}")
        print(f"Login method: {args.login_method}")
        print("=" * 60)
        
        accounts = [{'email': email, 'password': password}]
        results = process_accounts(accounts, headless=not args.visible, proxies=proxies, login_method=args.login_method)
        sys.exit(0 if results else 1)
    
    # Bulk accounts mode
    if args.accounts:
        accounts = load_accounts(args.accounts)
        print(f"Loaded {len(accounts)} accounts from {args.accounts}")
        print(f"Login method: {args.login_method}")
        
        results = process_accounts(accounts, headless=not args.visible, proxies=proxies, login_method=args.login_method)
        sys.exit(0 if results else 1)
    
    # No arguments provided
    print("Error: Please provide --accounts <file> or --single <email:password>")
    sys.exit(1)

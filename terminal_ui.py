#!/usr/bin/env python3
"""Terminal UI for Auto-FreeCF"""

import json
from pathlib import Path
from browser_bot import CFAutoGrabber

def clear_screen():
    print("\033[2J\033[H", end="")

def print_header():
    print("=" * 60)
    print("🚀 Auto-FreeCF - Terminal UI")
    print("=" * 60)
    print()

def print_menu():
    print("1. Process accounts from JSON file")
    print("2. Add account manually")
    print("3. View saved accounts")
    print("4. Exit")
    print()

def process_file():
    filename = input("Enter accounts file path (default: accounts.json): ").strip()
    if not filename:
        filename = "accounts.json"
    
    filepath = Path(filename)
    if not filepath.exists():
        print(f"❌ File not found: {filename}")
        return
    
    try:
        with open(filepath) as f:
            accounts = json.load(f)
        
        print(f"\n📋 Found {len(accounts)} accounts")
        print()
        
        results = []
        for i, account in enumerate(accounts, 1):
            email = account.get('email')
            password = account.get('password')
            
            print(f"\n{'=' * 60}")
            print(f"Processing {i}/{len(accounts)}: {email}")
            print('=' * 60)
            
            grabber = CFAutoGrabber(email, password)
            
            # Login
            if not grabber.login():
                print(f"❌ Login failed for {email}")
                results.append({'email': email, 'status': 'login_failed'})
                continue
            
            # Get Account ID
            if not grabber.get_account_id():
                print(f"❌ Failed to get Account ID for {email}")
                results.append({'email': email, 'status': 'account_id_failed'})
                continue
            
            # Create token
            if not grabber.create_workers_ai_token():
                print(f"❌ Failed to create token for {email}")
                results.append({'email': email, 'status': 'token_failed'})
                continue
            
            # Export
            result = grabber.export()
            results.append(result)
            print(f"✅ Success: {email}")
        
        print(f"\n{'=' * 60}")
        print(f"✅ Completed: {len(results)}/{len(accounts)} accounts")
        print(f"Results saved to: exports/cf_accounts.json")
        print('=' * 60)
        
    except Exception as e:
        print(f"❌ Error: {e}")

def add_manual():
    print("\nAdd account manually:")
    email = input("Email: ").strip()
    password = input("Password: ").strip()
    
    if not email or not password:
        print("❌ Email and password required")
        return
    
    print(f"\n{'=' * 60}")
    print(f"Processing: {email}")
    print('=' * 60)
    
    grabber = CFAutoGrabber(email, password)
    
    # Login
    if not grabber.login():
        print(f"❌ Login failed")
        return
    
    # Get Account ID
    if not grabber.get_account_id():
        print(f"❌ Failed to get Account ID")
        return
    
    # Create token
    if not grabber.create_workers_ai_token():
        print(f"❌ Failed to create token")
        return
    
    # Export
    result = grabber.export()
    print(f"\n✅ Success!")
    print(f"Account ID: {result.get('account_id')}")
    print(f"API Token: {result.get('api_token', '')[:20]}...")

def view_accounts():
    filepath = Path("exports/cf_accounts.json")
    if not filepath.exists():
        print("❌ No saved accounts found")
        return
    
    try:
        with open(filepath) as f:
            accounts = json.load(f)
        
        print(f"\n📋 Saved accounts ({len(accounts)}):")
        print()
        for i, acc in enumerate(accounts, 1):
            print(f"{i}. {acc.get('email')}")
            print(f"   Account ID: {acc.get('account_id', 'N/A')}")
            print(f"   API Token: {acc.get('api_token', 'N/A')[:20]}...")
            print(f"   Workers AI: {'✅' if acc.get('workers_ai_ok') else '❌'}")
            print()
    except Exception as e:
        print(f"❌ Error: {e}")

def main():
    while True:
        clear_screen()
        print_header()
        print_menu()
        
        choice = input("Select option (1-4): ").strip()
        
        if choice == '1':
            clear_screen()
            process_file()
            input("\nPress Enter to continue...")
        elif choice == '2':
            clear_screen()
            add_manual()
            input("\nPress Enter to continue...")
        elif choice == '3':
            clear_screen()
            view_accounts()
            input("\nPress Enter to continue...")
        elif choice == '4':
            print("Goodbye!")
            break
        else:
            print("❌ Invalid option")
            input("\nPress Enter to continue...")

if __name__ == '__main__':
    main()

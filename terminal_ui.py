#!/usr/bin/env python3
"""Beautiful Terminal UI for Auto-FreeCF"""

import json
import sys
import time
from pathlib import Path
from browser_bot import CFAutoGrabber

# ANSI colors
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    DIM = '\033[2m'

def clear_screen():
    print("\033[2J\033[H", end="")

def print_header():
    print(f"""
{Colors.CYAN}{Colors.BOLD}╔══════════════════════════════════════════════════════════╗
║                                                              ║
║   {Colors.GREEN}🚀 Auto-FreeCF{Colors.CYAN}                                         ║
║   {Colors.DIM}Cloudflare Workers AI Account ID & Token Grabber{Colors.CYAN}         ║
║                                                              ║
╚══════════════════════════════════════════════════════════╝{Colors.ENDC}
""")

def print_menu():
    print(f"""{Colors.BOLD}Choose an option:{Colors.ENDC}

  {Colors.GREEN}[1]{Colors.ENDC} 📂 Process accounts from JSON file
  {Colors.GREEN}[2]{Colors.ENDC} ✏️  Add account manually
  {Colors.GREEN}[3]{Colors.ENDC} 📋 View saved accounts
  {Colors.GREEN}[4]{Colors.ENDC} 🚪 Exit

""")

def progress_bar(iteration, total, prefix='', suffix='', length=50, fill='█'):
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{Colors.CYAN}{prefix}{Colors.ENDC} |{Colors.GREEN}{bar}{Colors.ENDC}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total:
        print()

def process_file():
    print(f"\n{Colors.BOLD}📂 Process from JSON file{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}\n")
    
    filename = input(f"{Colors.CYAN}Enter file path{Colors.ENDC} {Colors.DIM}(default: accounts.json){Colors.ENDC}: ").strip()
    if not filename:
        filename = "accounts.json"
    
    filepath = Path(filename)
    if not filepath.exists():
        print(f"\n{Colors.FAIL}❌ File not found:{Colors.ENDC} {filename}")
        return
    
    try:
        with open(filepath) as f:
            accounts = json.load(f)
        
        print(f"\n{Colors.GREEN}✓{Colors.ENDC} Found {Colors.BOLD}{len(accounts)}{Colors.ENDC} accounts\n")
        
        results = []
        for i, account in enumerate(accounts, 1):
            email = account.get('email')
            password = account.get('password')
            
            print(f"\n{Colors.BOLD}Processing {i}/{len(accounts)}:{Colors.ENDC} {Colors.CYAN}{email}{Colors.ENDC}")
            print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}")
            
            grabber = CFAutoGrabber(email, password)
            
            # Login
            sys.stdout.write(f"  {Colors.DIM}[1/4]{Colors.ENDC} Logging in... ")
            sys.stdout.flush()
            if not grabber.login():
                print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
                results.append({'email': email, 'status': 'login_failed'})
                continue
            print(f"{Colors.GREEN}✓{Colors.ENDC}")
            
            # Get Account ID
            sys.stdout.write(f"  {Colors.DIM}[2/4]{Colors.ENDC} Getting Account ID... ")
            sys.stdout.flush()
            if not grabber.get_account_id():
                print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
                results.append({'email': email, 'status': 'account_id_failed'})
                continue
            print(f"{Colors.GREEN}✓{Colors.ENDC}")
            
            # Create token
            sys.stdout.write(f"  {Colors.DIM}[3/4]{Colors.ENDC} Creating API token... ")
            sys.stdout.flush()
            if not grabber.create_workers_ai_token():
                print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
                results.append({'email': email, 'status': 'token_failed'})
                continue
            print(f"{Colors.GREEN}✓{Colors.ENDC}")
            
            # Export
            sys.stdout.write(f"  {Colors.DIM}[4/4]{Colors.ENDC} Exporting... ")
            sys.stdout.flush()
            result = grabber.export()
            results.append(result)
            print(f"{Colors.GREEN}✓{Colors.ENDC}")
            
            print(f"  {Colors.GREEN}✅ Success!{Colors.ENDC}")
        
        print(f"\n{Colors.DIM}{'═' * 60}{Colors.ENDC}")
        print(f"{Colors.GREEN}{Colors.BOLD}✅ Completed!{Colors.ENDC} {len(results)}/{len(accounts)} accounts processed")
        print(f"{Colors.CYAN}Results saved to:{Colors.ENDC} exports/cf_accounts.json")
        print(f"{Colors.DIM}{'═' * 60}{Colors.ENDC}")
        
    except Exception as e:
        print(f"\n{Colors.FAIL}❌ Error:{Colors.ENDC} {e}")

def add_manual():
    print(f"\n{Colors.BOLD}✏️  Add account manually{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}\n")
    
    email = input(f"{Colors.CYAN}Email:{Colors.ENDC} ").strip()
    password = input(f"{Colors.CYAN}Password:{Colors.ENDC} ").strip()
    
    if not email or not password:
        print(f"\n{Colors.FAIL}❌ Email and password required{Colors.ENDC}")
        return
    
    print(f"\n{Colors.BOLD}Processing:{Colors.ENDC} {Colors.CYAN}{email}{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}")
    
    grabber = CFAutoGrabber(email, password)
    
    # Login
    sys.stdout.write(f"  {Colors.DIM}[1/4]{Colors.ENDC} Logging in... ")
    sys.stdout.flush()
    if not grabber.login():
        print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    # Get Account ID
    sys.stdout.write(f"  {Colors.DIM}[2/4]{Colors.ENDC} Getting Account ID... ")
    sys.stdout.flush()
    if not grabber.get_account_id():
        print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    # Create token
    sys.stdout.write(f"  {Colors.DIM}[3/4]{Colors.ENDC} Creating API token... ")
    sys.stdout.flush()
    if not grabber.create_workers_ai_token():
        print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    # Export
    sys.stdout.write(f"  {Colors.DIM}[4/4]{Colors.ENDC} Exporting... ")
    sys.stdout.flush()
    result = grabber.export()
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}✅ Success!{Colors.ENDC}")
    print(f"  {Colors.CYAN}Account ID:{Colors.ENDC} {result.get('account_id', 'N/A')}")
    print(f"  {Colors.CYAN}API Token:{Colors.ENDC}  {result.get('api_token', 'N/A')[:30]}...")
    print(f"  {Colors.CYAN}Workers AI:{Colors.ENDC} {'✅ OK' if result.get('workers_ai_ok') else '❌ Failed'}")

def view_accounts():
    print(f"\n{Colors.BOLD}📋 View saved accounts{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}\n")
    
    filepath = Path("exports/cf_accounts.json")
    if not filepath.exists():
        print(f"{Colors.WARNING}⚠️  No saved accounts found{Colors.ENDC}")
        return
    
    try:
        with open(filepath) as f:
            accounts = json.load(f)
        
        print(f"{Colors.GREEN}✓{Colors.ENDC} Found {Colors.BOLD}{len(accounts)}{Colors.ENDC} saved accounts\n")
        
        for i, acc in enumerate(accounts, 1):
            print(f"{Colors.BOLD}{i}. {Colors.CYAN}{acc.get('email')}{Colors.ENDC}")
            print(f"   {Colors.DIM}Account ID:{Colors.ENDC} {acc.get('account_id', 'N/A')}")
            print(f"   {Colors.DIM}API Token:{Colors.ENDC}  {acc.get('api_token', 'N/A')[:30]}...")
            print(f"   {Colors.DIM}Workers AI:{Colors.ENDC} {'✅ OK' if acc.get('workers_ai_ok') else '❌ Failed'}")
            print()
    except Exception as e:
        print(f"{Colors.FAIL}❌ Error:{Colors.ENDC} {e}")

def main():
    while True:
        clear_screen()
        print_header()
        print_menu()
        
        choice = input(f"{Colors.BOLD}Select option{Colors.ENDC} {Colors.DIM}(1-4){Colors.ENDC}: ").strip()
        
        if choice == '1':
            clear_screen()
            process_file()
            input(f"\n{Colors.DIM}Press Enter to continue...{Colors.ENDC}")
        elif choice == '2':
            clear_screen()
            add_manual()
            input(f"\n{Colors.DIM}Press Enter to continue...{Colors.ENDC}")
        elif choice == '3':
            clear_screen()
            view_accounts()
            input(f"\n{Colors.DIM}Press Enter to continue...{Colors.ENDC}")
        elif choice == '4':
            print(f"\n{Colors.CYAN}Goodbye! 👋{Colors.ENDC}\n")
            break
        else:
            print(f"\n{Colors.FAIL}❌ Invalid option{Colors.ENDC}")
            input(f"\n{Colors.DIM}Press Enter to continue...{Colors.ENDC}")

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Simplified Terminal UI for Auto-FreeCF"""

import sys
import os
from pathlib import Path

from browser_bot import CFAutoGrabber, load_accounts, process_accounts


class Colors:
    """ANSI color codes"""
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
    os.system('cls' if os.name == 'nt' else 'clear')


def print_banner():
    banner = f"""
{Colors.CYAN}╔════════════════════════════════════════════════════════════════╗
║                                                                    ║
║   {Colors.GREEN}{Colors.BOLD}🚀 Auto-FreeCF{Colors.ENDC}{Colors.CYAN}                                          ║
║   {Colors.DIM}Cloudflare Workers AI Account ID & Token Grabber{Colors.ENDC}{Colors.CYAN}              ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
{Colors.DIM}   By mmoaa{Colors.ENDC}
{Colors.YELLOW}{Colors.BOLD}   ⚠️  BETA TESTING - Use at your own risk{Colors.ENDC}
"""
    print(banner)


def process_single():
    """Process single account from email:pass input"""
    print(f"\n{Colors.BOLD}📝 Single Account Mode{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}\n")
    
    user_input = input(f"{Colors.CYAN}Enter email:password{Colors.ENDC}: ").strip()
    
    if not user_input:
        print(f"\n{Colors.FAIL}❌ Input cannot be empty{Colors.ENDC}")
        return
    
    if ':' not in user_input:
        print(f"\n{Colors.FAIL}❌ Invalid format. Use: email:password{Colors.ENDC}")
        return
    
    email, password = user_input.split(':', 1)
    email = email.strip()
    password = password.strip()
    
    if not email or not password:
        print(f"\n{Colors.FAIL}❌ Email and password cannot be empty{Colors.ENDC}")
        return
    
    print(f"\n{Colors.BOLD}Processing:{Colors.ENDC} {Colors.CYAN}{email}{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}")
    
    grabber = CFAutoGrabber(email, password, headless=False)
    
    sys.stdout.write(f"  {Colors.DIM}[1/4]{Colors.ENDC} Logging in... ")
    sys.stdout.flush()
    if not grabber.login():
        print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    sys.stdout.write(f"  {Colors.DIM}[2/4]{Colors.ENDC} Getting Account ID... ")
    sys.stdout.flush()
    if not grabber.get_account_id():
        print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    sys.stdout.write(f"  {Colors.DIM}[3/4]{Colors.ENDC} Creating API token... ")
    sys.stdout.flush()
    if not grabber.create_workers_ai_token():
        print(f"{Colors.FAIL}❌ Failed{Colors.ENDC}")
        return
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    sys.stdout.write(f"  {Colors.DIM}[4/4]{Colors.ENDC} Exporting... ")
    sys.stdout.flush()
    result = grabber.export()
    print(f"{Colors.GREEN}✓{Colors.ENDC}")
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}✅ Success!{Colors.ENDC}")
    print(f"  {Colors.CYAN}Account ID:{Colors.ENDC} {result.get('account_id', 'N/A')}")
    print(f"  {Colors.CYAN}API Token:{Colors.ENDC}  {result.get('api_token', 'N/A')[:30]}...")
    print(f"  {Colors.CYAN}Workers AI:{Colors.ENDC} {'✅ OK' if result.get('workers_ai_ok') else '❌ Failed'}")


def process_bulk():
    """Process bulk accounts from file"""
    print(f"\n{Colors.BOLD}📂 Bulk Account Mode{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}\n")
    
    filename = input(f"{Colors.CYAN}Enter file path{Colors.ENDC} {Colors.DIM}(default: accounts.txt){Colors.ENDC}: ").strip()
    if not filename:
        filename = "accounts.txt"
    
    filepath = Path(filename)
    if not filepath.exists():
        print(f"\n{Colors.FAIL}❌ File not found:{Colors.ENDC} {filename}")
        return
    
    try:
        accounts = load_accounts(filename)
        print(f"\n{Colors.GREEN}✓{Colors.ENDC} Found {Colors.BOLD}{len(accounts)}{Colors.ENDC} accounts\n")
        
        results = process_accounts(accounts, headless=False)
        
        print(f"\n{Colors.DIM}{'═' * 60}{Colors.ENDC}")
        print(f"{Colors.GREEN}{Colors.BOLD}✅ Completed!{Colors.ENDC} {len(results)}/{len(accounts)} accounts processed")
        print(f"{Colors.CYAN}Results saved to:{Colors.ENDC} exports/cf_accounts.json")
        print(f"{Colors.DIM}{'═' * 60}{Colors.ENDC}")
        
    except Exception as e:
        print(f"\n{Colors.FAIL}❌ Error:{Colors.ENDC} {e}")


def main():
    """Main entry point"""
    clear_screen()
    print_banner()
    
    print(f"{Colors.BOLD}Choose mode:{Colors.ENDC}")
    print(f"  {Colors.GREEN}[1]{Colors.ENDC} Single account {Colors.DIM}(enter email:pass){Colors.ENDC}")
    print(f"  {Colors.GREEN}[2]{Colors.ENDC} Bulk accounts {Colors.DIM}(from file){Colors.ENDC}")
    print(f"  {Colors.GREEN}[3]{Colors.ENDC} Exit")
    print()
    
    choice = input(f"{Colors.BOLD}Select{Colors.ENDC} {Colors.DIM}(1-3){Colors.ENDC}: ").strip()
    
    if choice == '1':
        clear_screen()
        process_single()
    elif choice == '2':
        clear_screen()
        process_bulk()
    elif choice == '3':
        print(f"\n{Colors.CYAN}Goodbye! 👋{Colors.ENDC}\n")
        sys.exit(0)
    else:
        print(f"\n{Colors.FAIL}❌ Invalid option{Colors.ENDC}")
        sys.exit(1)
    
    print(f"\n{Colors.DIM}Press Enter to exit...{Colors.ENDC}")
    input()


if __name__ == '__main__':
    main()

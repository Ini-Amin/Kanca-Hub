#!/usr/bin/env python3
"""Modern Terminal UI for Auto-FreeCF"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Try to import rich for better terminal UI
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.prompt import Prompt, IntPrompt
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

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
    """Clear terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_banner():
    """Print modern banner"""
    banner = f"""
{Colors.CYAN}╔════════════════════════════════════════════════════════════════╗
║                                                                    ║
║   {Colors.GREEN}{Colors.BOLD}🚀 Auto-FreeCF{Colors.CYAN}{Colors.ENDC}                                          ║
║   {Colors.DIM}Cloudflare Workers AI Account ID & Token Grabber{Colors.CYAN}              ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
{Colors.DIM}   By mmoaa{Colors.ENDC}
"""
    print(banner)


def print_menu():
    """Print modern menu"""
    menu = f"""
{Colors.BOLD}┌─{Colors.ENDC} {Colors.CYAN}{Colors.BOLD}Main Menu{Colors.ENDC}
{Colors.BOLD}│{Colors.ENDC}
{Colors.BOLD}│{Colors.ENDC}  {Colors.GREEN}[1]{Colors.ENDC} 🌐  Web UI {Colors.DIM}(browser interface){Colors.ENDC}
{Colors.BOLD}│{Colors.ENDC}  {Colors.GREEN}[2]{Colors.ENDC} 💻  Terminal UI {Colors.DIM}(interactive menu){Colors.ENDC}
{Colors.BOLD}│{Colors.ENDC}  {Colors.GREEN}[3]{Colors.ENDC} 📝  Process accounts file
{Colors.BOLD}│{Colors.ENDC}  {Colors.GREEN}[4]{Colors.ENDC} 🚪  Exit
{Colors.BOLD}│{Colors.ENDC}
{Colors.BOLD}└─{Colors.ENDC} {Colors.DIM}Select option (1-4){Colors.ENDC}
"""
    print(menu)


def process_file():
    """Process accounts from file"""
    print(f"\n{Colors.BOLD}📂 Process from file{Colors.ENDC}")
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


def add_manual():
    """Add account manually"""
    print(f"\n{Colors.BOLD}✏️  Add account manually{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}\n")
    
    email = input(f"{Colors.CYAN}Email:{Colors.ENDC} ").strip()
    password = input(f"{Colors.CYAN}Password:{Colors.ENDC} ").strip()
    
    if not email or not password:
        print(f"\n{Colors.FAIL}❌ Email and password required{Colors.ENDC}")
        return
    
    print(f"\n{Colors.BOLD}Processing:{Colors.ENDC} {Colors.CYAN}{email}{Colors.ENDC}")
    print(f"{Colors.DIM}{'─' * 60}{Colors.ENDC}")
    
    grabber = CFAutoGrabber(email, password, headless=False)
    
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
    """View saved accounts"""
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
    """Main loop"""
    while True:
        clear_screen()
        print_banner()
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

#!/usr/bin/env python3
"""Validation script to verify Auto-FreeCF functionality"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def validate_imports():
    """Check if all imports work"""
    print("=" * 60)
    print("VALIDATION: Checking imports...")
    print("=" * 60)
    try:
        from browser_bot import CFAutoGrabber, load_accounts, load_proxy_config, process_accounts
        print("✓ All imports successful")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def validate_class_structure():
    """Check if CFAutoGrabber has all required methods"""
    print("\n" + "=" * 60)
    print("VALIDATION: Checking CFAutoGrabber structure...")
    print("=" * 60)
    
    from browser_bot import CFAutoGrabber
    
    required_methods = [
        '__init__',
        '_start_browser',
        '_close_browser',
        '_wait_for_challenge',
        '_login_manual_turnstile',
        'login',
        'login_google',
        'get_account_id',
        'create_workers_ai_token',
        'export',
        '__del__'
    ]
    
    all_exist = True
    for method in required_methods:
        if hasattr(CFAutoGrabber, method):
            print(f"✓ Method exists: {method}")
        else:
            print(f"✗ Method missing: {method}")
            all_exist = False
    
    return all_exist

def validate_instance_creation():
    """Check if we can create instances"""
    print("\n" + "=" * 60)
    print("VALIDATION: Testing instance creation...")
    print("=" * 60)
    
    from browser_bot import CFAutoGrabber
    
    try:
        # Test email:password login
        grabber1 = CFAutoGrabber("test@example.com", "password123", headless=True, login_method="email")
        print(f"✓ Created instance with email:password login")
        print(f"  - email: {grabber1.email}")
        print(f"  - login_method: {grabber1.login_method}")
        print(f"  - headless: {grabber1.headless}")
        
        # Test Google OAuth login
        grabber2 = CFAutoGrabber("test@gmail.com", "password123", headless=True, login_method="google")
        print(f"✓ Created instance with Google OAuth login")
        print(f"  - email: {grabber2.email}")
        print(f"  - login_method: {grabber2.login_method}")
        
        # Test with proxy
        proxy = {"server": "http://proxy.com:8080", "username": "user", "password": "pass"}
        grabber3 = CFAutoGrabber("test@example.com", "password123", headless=True, proxy=proxy)
        print(f"✓ Created instance with proxy")
        print(f"  - proxy server: {grabber3.proxy['server']}")
        
        return True
    except Exception as e:
        print(f"✗ Instance creation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def validate_helper_functions():
    """Check helper functions"""
    print("\n" + "=" * 60)
    print("VALIDATION: Testing helper functions...")
    print("=" * 60)
    
    from browser_bot import load_accounts, load_proxy_config
    import tempfile
    import json
    
    # Test load_accounts with TXT format
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("test1@example.com:pass1\n")
            f.write("test2@example.com:pass2\n")
            temp_file = f.name
        
        accounts = load_accounts(temp_file)
        os.unlink(temp_file)
        
        if len(accounts) == 2:
            print(f"✓ load_accounts (TXT) works: loaded {len(accounts)} accounts")
        else:
            print(f"✗ load_accounts (TXT) failed: expected 2, got {len(accounts)}")
            return False
    except Exception as e:
        print(f"✗ load_accounts (TXT) failed: {e}")
        return False
    
    # Test load_accounts with JSON format
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([
                {"email": "test1@example.com", "password": "pass1"},
                {"email": "test2@example.com", "password": "pass2"}
            ], f)
            temp_file = f.name
        
        accounts = load_accounts(temp_file)
        os.unlink(temp_file)
        
        if len(accounts) == 2:
            print(f"✓ load_accounts (JSON) works: loaded {len(accounts)} accounts")
        else:
            print(f"✗ load_accounts (JSON) failed: expected 2, got {len(accounts)}")
            return False
    except Exception as e:
        print(f"✗ load_accounts (JSON) failed: {e}")
        return False
    
    # Test load_proxy_config
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "server": "http://proxy.com:8080",
                "username": "user",
                "password": "pass"
            }, f)
            temp_file = f.name
        
        proxies = load_proxy_config(temp_file)
        os.unlink(temp_file)
        
        if len(proxies) == 1:
            print(f"✓ load_proxy_config works: loaded {len(proxies)} proxy")
        else:
            print(f"✗ load_proxy_config failed: expected 1, got {len(proxies)}")
            return False
    except Exception as e:
        print(f"✗ load_proxy_config failed: {e}")
        return False
    
    return True

def validate_flow_logic():
    """Check if the flow logic is correct"""
    print("\n" + "=" * 60)
    print("VALIDATION: Checking flow logic...")
    print("=" * 60)
    
    from browser_bot import CFAutoGrabber
    
    # Check that login methods exist and are callable
    grabber = CFAutoGrabber("test@example.com", "password123")
    
    if callable(getattr(grabber, 'login', None)):
        print("✓ login() method is callable")
    else:
        print("✗ login() method is not callable")
        return False
    
    if callable(getattr(grabber, 'login_google', None)):
        print("✓ login_google() method is callable")
    else:
        print("✗ login_google() method is not callable")
        return False
    
    if callable(getattr(grabber, 'get_account_id', None)):
        print("✓ get_account_id() method is callable")
    else:
        print("✗ get_account_id() method is not callable")
        return False
    
    if callable(getattr(grabber, 'create_workers_ai_token', None)):
        print("✓ create_workers_ai_token() method is callable")
    else:
        print("✗ create_workers_ai_token() method is not callable")
        return False
    
    if callable(getattr(grabber, 'export', None)):
        print("✓ export() method is callable")
    else:
        print("✗ export() method is not callable")
        return False
    
    return True

def main():
    """Run all validations"""
    print("\n" + "=" * 60)
    print("AUTO-FREECF VALIDATION SCRIPT")
    print("=" * 60)
    
    results = []
    
    results.append(("Imports", validate_imports()))
    results.append(("Class Structure", validate_class_structure()))
    results.append(("Instance Creation", validate_instance_creation()))
    results.append(("Helper Functions", validate_helper_functions()))
    results.append(("Flow Logic", validate_flow_logic()))
    
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n✓ ALL VALIDATIONS PASSED")
        print("The code structure is correct and ready for testing.")
        return 0
    else:
        print("\n✗ SOME VALIDATIONS FAILED")
        print("Please fix the issues before testing.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

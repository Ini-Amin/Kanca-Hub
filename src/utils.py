#!/usr/bin/env python3
"""Utility functions for Auto-FreeCF"""

import json
from pathlib import Path
from typing import List, Dict


def load_accounts(file_path: str) -> List[Dict[str, str]]:
    """Load accounts from JSON or TXT file"""
    path = Path(file_path)
    
    if not path.exists():
        print(f"Error: File {file_path} not found")
        return []
    
    accounts = []
    
    if path.suffix.lower() == '.json':
        # JSON format
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if 'email' in item and 'password' in item:
                        accounts.append({
                            'email': item['email'],
                            'password': item['password']
                        })
    
    elif path.suffix.lower() == '.txt':
        # TXT format: email:password per line
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and ':' in line:
                    email, password = line.split(':', 1)
                    accounts.append({
                        'email': email.strip(),
                        'password': password.strip()
                    })
    
    else:
        print(f"Error: Unsupported file format. Use .json or .txt")
        return []
    
    if not accounts:
        print("Error: No valid accounts found in file")
        return []
    
    return accounts


def load_proxy_config(proxy_file: str) -> List[Dict]:
    """Load proxy configuration from JSON file (supports single or multi-proxy)"""
    path = Path(proxy_file)
    if not path.exists():
        print(f"Warning: Proxy file not found: {proxy_file}")
        return []
    
    try:
        with open(path, 'r') as f:
            config = json.load(f)
        
        # Check if it's multi-proxy format (has "proxies" array)
        if 'proxies' in config and isinstance(config['proxies'], list):
            proxies = []
            for p in config['proxies']:
                if p.get('server') and p.get('username') and p.get('password'):
                    proxies.append({
                        'server': p['server'],
                        'username': p['username'],
                        'password': p['password'],
                        'country': p.get('country', 'N/A'),
                        'city': p.get('city', 'N/A')
                    })
            return proxies
        
        # Legacy single-proxy format
        elif config.get('server'):
            return [{
                'server': config['server'],
                'username': config.get('username'),
                'password': config.get('password'),
                'country': 'N/A',
                'city': 'N/A'
            }]
        
        else:
            print(f"Warning: Invalid proxy config format")
            return []
            
    except Exception as e:
        print(f"Warning: Could not load proxy config: {e}")
        return []


def save_results(results: List[Dict], output_file: str = "exports/cf_accounts.txt"):
    """Save results to TXT format (account_id:worker_token)"""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        for result in results:
            if result.get('account_id') and result.get('api_token'):
                f.write(f"{result['account_id']}:{result['api_token']}\n")
    
    print(f"\n{'='*60}")
    print(f"Results saved to: {output_path}")
    print(f"Total processed: {len(results)}")
    print('='*60)

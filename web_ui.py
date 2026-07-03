#!/usr/bin/env python3
"""Web UI for Auto-FreeCF"""

import json
import threading
import webbrowser
from pathlib import Path
from flask import Flask, request, render_template_string, jsonify
from browser_bot import CFAutoGrabber

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Auto-FreeCF - Web UI</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        h1 { color: #333; }
        textarea { width: 100%; height: 200px; font-family: monospace; padding: 10px; }
        button { background: #4CAF50; color: white; padding: 10px 20px; border: none; cursor: pointer; font-size: 16px; }
        button:hover { background: #45a049; }
        .result { margin-top: 20px; padding: 15px; background: #f0f0f0; border-radius: 5px; }
        .success { color: green; }
        .error { color: red; }
    </style>
</head>
<body>
    <h1>🚀 Auto-FreeCF - Web UI</h1>
    <p>Enter your Cloudflare accounts (JSON format):</p>
    <form id="accountsForm">
        <textarea id="accounts" placeholder='[
  {"email": "user1@example.com", "password": "pass1"},
  {"email": "user2@example.com", "password": "pass2"}
]'>[]</textarea>
        <br><br>
        <button type="submit">Process Accounts</button>
    </form>
    <div id="result" class="result" style="display:none;"></div>
    
    <script>
        document.getElementById('accountsForm').onsubmit = async (e) => {
            e.preventDefault();
            const resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            resultDiv.innerHTML = '<p>Processing... Please wait...</p>';
            
            try {
                const accounts = JSON.parse(document.getElementById('accounts').value);
                const response = await fetch('/process', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({accounts})
                });
                const data = await response.json();
                
                if (data.success) {
                    resultDiv.innerHTML = `<p class="success">✅ Success! Processed ${data.processed} accounts.</p>
                    <p>Results saved to: exports/cf_accounts.json</p>
                    <pre>${JSON.stringify(data.results, null, 2)}</pre>`;
                } else {
                    resultDiv.innerHTML = `<p class="error">❌ Error: ${data.error}</p>`;
                }
            } catch (err) {
                resultDiv.innerHTML = `<p class="error">❌ Error: ${err.message}</p>`;
            }
        };
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/process', methods=['POST'])
def process():
    try:
        data = request.json
        accounts = data.get('accounts', [])
        
        if not accounts:
            return jsonify({'success': False, 'error': 'No accounts provided'})
        
        results = []
        for account in accounts:
            email = account.get('email')
            password = account.get('password')
            
            if not email or not password:
                continue
            
            grabber = CFAutoGrabber(email, password)
            
            # Login
            if not grabber.login():
                results.append({'email': email, 'status': 'login_failed'})
                continue
            
            # Get Account ID
            if not grabber.get_account_id():
                results.append({'email': email, 'status': 'account_id_failed'})
                continue
            
            # Create token
            if not grabber.create_workers_ai_token():
                results.append({'email': email, 'status': 'token_failed'})
                continue
            
            # Export
            result = grabber.export()
            results.append(result)
        
        return jsonify({
            'success': True,
            'processed': len(results),
            'results': results
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--open', action='store_true', help='Open browser automatically')
    args = parser.parse_args()
    
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{args.port}')).start()
    
    print(f"🌐 Web UI running at http://localhost:{args.port}")
    print("Press Ctrl+C to stop")
    app.run(host='0.0.0.0', port=args.port, debug=False)

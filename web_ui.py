#!/usr/bin/env python3
"""Modern Web UI for Auto-FreeCF"""

import json
import threading
import webbrowser
from pathlib import Path
from flask import Flask, request, render_template_string, jsonify
from browser_bot import CFAutoGrabber

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-FreeCF - Cloudflare Account Grabber</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 800px;
            width: 100%;
            padding: 40px;
            animation: fadeIn 0.5s ease-in;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 2.5em;
            color: #333;
            margin-bottom: 10px;
        }
        
        .header p {
            color: #666;
            font-size: 1.1em;
        }
        
        .badge {
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
            margin: 5px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 600;
        }
        
        textarea {
            width: 100%;
            min-height: 250px;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            resize: vertical;
            transition: border-color 0.3s;
        }
        
        textarea:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 40px;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            width: 100%;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
        }
        
        .btn:active {
            transform: translateY(0);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        
        .result {
            margin-top: 30px;
            padding: 20px;
            border-radius: 10px;
            display: none;
            animation: slideIn 0.3s ease-out;
        }
        
        @keyframes slideIn {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        
        .result.success {
            background: #d4edda;
            border: 2px solid #c3e6cb;
            color: #155724;
        }
        
        .result.error {
            background: #f8d7da;
            border: 2px solid #f5c6cb;
            color: #721c24;
        }
        
        .result h3 {
            margin-bottom: 15px;
        }
        
        .result pre {
            background: rgba(255,255,255,0.5);
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
            font-size: 13px;
        }
        
        .loader {
            display: none;
            text-align: center;
            margin: 20px 0;
        }
        
        .loader.active {
            display: block;
        }
        
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 10px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .info-box {
            background: #e7f3ff;
            border-left: 4px solid #2196F3;
            padding: 15px;
            margin: 20px 0;
            border-radius: 5px;
        }
        
        .info-box h4 {
            margin-bottom: 10px;
            color: #1976D2;
        }
        
        .info-box code {
            background: rgba(0,0,0,0.05);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Auto-FreeCF</h1>
            <p>Cloudflare Workers AI Account ID & Token Auto-Grabber</p>
            <div>
                <span class="badge">✨ Auto Setup</span>
                <span class="badge">🤖 Full Automation</span>
                <span class="badge">🛡️ Bypass Challenge</span>
            </div>
        </div>
        
        <div class="info-box">
            <h4>📝 Format JSON</h4>
            <p>Paste accounts dalam format JSON array:</p>
            <code>[{"email": "user@example.com", "password": "yourpassword"}]</code>
        </div>
        
        <form id="accountsForm">
            <div class="form-group">
                <label for="accounts">Cloudflare Accounts:</label>
                <textarea id="accounts" placeholder='[
  {"email": "user1@example.com", "password": "pass1"},
  {"email": "user2@example.com", "password": "pass2"}
]'>[]</textarea>
            </div>
            <button type="submit" class="btn" id="submitBtn">
                🚀 Process Accounts
            </button>
        </form>
        
        <div class="loader" id="loader">
            <div class="spinner"></div>
            <p>Processing accounts... Please wait</p>
        </div>
        
        <div id="result" class="result"></div>
    </div>
    
    <script>
        document.getElementById('accountsForm').onsubmit = async (e) => {
            e.preventDefault();
            
            const resultDiv = document.getElementById('result');
            const loader = document.getElementById('loader');
            const submitBtn = document.getElementById('submitBtn');
            
            resultDiv.style.display = 'none';
            loader.classList.add('active');
            submitBtn.disabled = true;
            
            try {
                const accounts = JSON.parse(document.getElementById('accounts').value);
                
                const response = await fetch('/process', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({accounts})
                });
                
                const data = await response.json();
                
                loader.classList.remove('active');
                submitBtn.disabled = false;
                
                if (data.success) {
                    resultDiv.className = 'result success';
                    resultDiv.innerHTML = `
                        <h3>✅ Success!</h3>
                        <p>Processed <strong>${data.processed}</strong> accounts successfully.</p>
                        <p>Results saved to: <code>exports/cf_accounts.json</code></p>
                        <pre>${JSON.stringify(data.results, null, 2)}</pre>
                    `;
                    resultDiv.style.display = 'block';
                } else {
                    resultDiv.className = 'result error';
                    resultDiv.innerHTML = `<h3>❌ Error</h3><p>${data.error}</p>`;
                    resultDiv.style.display = 'block';
                }
            } catch (err) {
                loader.classList.remove('active');
                submitBtn.disabled = false;
                
                resultDiv.className = 'result error';
                resultDiv.innerHTML = `<h3>❌ Error</h3><p>${err.message}</p>`;
                resultDiv.style.display = 'block';
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

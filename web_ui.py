#!/usr/bin/env python3
"""Modern Web UI for Auto-FreeCF"""

import json
import threading
import webbrowser
from pathlib import Path
from flask import Flask, request, render_template_string, jsonify
from src.browser_bot import CFAutoGrabber
from src.utils import load_accounts

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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 900px;
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
            margin-bottom: 40px;
        }
        
        .header h1 {
            font-size: 2.5em;
            color: #2d3748;
            margin-bottom: 10px;
            font-weight: 700;
        }
        
        .header p {
            color: #718096;
            font-size: 1.1em;
            margin-bottom: 20px;
        }
        
        .badge {
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 0.85em;
            margin: 5px;
            font-weight: 500;
        }
        
        .info-box {
            background: #f7fafc;
            border-left: 4px solid #667eea;
            padding: 20px;
            margin: 20px 0;
            border-radius: 8px;
        }
        
        .info-box h4 {
            margin-bottom: 12px;
            color: #2d3748;
            font-size: 1.1em;
        }
        
        .info-box p {
            color: #4a5568;
            margin-bottom: 8px;
            line-height: 1.6;
        }
        
        .info-box code {
            background: #edf2f7;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.9em;
            color: #2d3748;
        }
        
        .form-group {
            margin-bottom: 24px;
        }
        
        label {
            display: block;
            margin-bottom: 10px;
            color: #2d3748;
            font-weight: 600;
            font-size: 1em;
        }
        
        textarea {
            width: 100%;
            min-height: 280px;
            padding: 16px;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            resize: vertical;
            transition: all 0.3s;
            background: #f7fafc;
        }
        
        textarea:focus {
            outline: none;
            border-color: #667eea;
            background: white;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 16px 40px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            width: 100%;
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        }
        
        .btn:active {
            transform: translateY(0);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .result {
            margin-top: 30px;
            padding: 24px;
            border-radius: 8px;
            display: none;
            animation: slideIn 0.3s ease-out;
        }
        
        @keyframes slideIn {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        
        .result.success {
            background: #f0fff4;
            border: 2px solid #9ae6b4;
            color: #22543d;
        }
        
        .result.error {
            background: #fff5f5;
            border: 2px solid #feb2b2;
            color: #742a2a;
        }
        
        .result h3 {
            margin-bottom: 16px;
            font-size: 1.3em;
        }
        
        .result pre {
            background: rgba(255,255,255,0.7);
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
            font-size: 13px;
            line-height: 1.5;
            border: 1px solid rgba(0,0,0,0.1);
        }
        
        .loader {
            display: none;
            text-align: center;
            margin: 30px 0;
        }
        
        .loader.active {
            display: block;
        }
        
        .spinner {
            border: 4px solid #f3f4f6;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite;
            margin: 0 auto 16px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .loader p {
            color: #4a5568;
            font-size: 1.1em;
        }
        
        .watermark {
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
            color: #a0aec0;
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
            <h4>📝 Supported Formats</h4>
            <p><strong>JSON format:</strong></p>
            <code>[{"email": "user@example.com", "password": "yourpassword"}]</code>
            <p style="margin-top: 12px;"><strong>TXT format (recommended):</strong></p>
            <code>email:password</code>
        </div>
        
        <form id="accountsForm">
            <div class="form-group">
                <label for="accounts">Cloudflare Accounts (JSON or TXT):</label>
                <textarea id="accounts" placeholder='JSON format:
[
  {"email": "user1@example.com", "password": "pass1"},
  {"email": "user2@example.com", "password": "pass2"}
]

OR TXT format:
user1@example.com:pass1
user2@example.com:pass2'></textarea>
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
        
        <div class="watermark">
            Made with ❤️ by mmoaa
        </div>
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
                const input = document.getElementById('accounts').value.trim();
                
                // Try to parse as JSON first
                let accounts;
                try {
                    accounts = JSON.parse(input);
                } catch (jsonError) {
                    // If not JSON, try TXT format
                    accounts = [];
                    const lines = input.split('\n');
                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (trimmed && trimmed.includes(':')) {
                            const [email, ...passParts] = trimmed.split(':');
                            accounts.push({
                                email: email.trim(),
                                password: passParts.join(':').trim()
                            });
                        }
                    }
                }
                
                if (!accounts || accounts.length === 0) {
                    throw new Error('No valid accounts found');
                }
                
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
                        <p>Results saved to: <code>exports/cf_accounts.txt</code></p>
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
        
        # Save results to file
        output_dir = Path("exports")
        output_dir.mkdir(exist_ok=True)
        output_file = output_dir / "cf_accounts.txt"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for result in results:
                if result.get('account_id') and result.get('api_token'):
                    f.write(f"{result['account_id']}:{result['api_token']}\n")
        
        return jsonify({
            'success': True,
            'processed': len(results),
            'results': results,
            'output_file': str(output_file)
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

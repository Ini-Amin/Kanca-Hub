#!/usr/bin/env node

const { spawn, spawnSync, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = __dirname;
const VENV = path.join(ROOT, 'venv');
const INSTALLED = path.join(VENV, '.installed');
const IS_WIN = process.platform === 'win32';

const venvPython = IS_WIN
  ? path.join(VENV, 'Scripts', 'python.exe')
  : path.join(VENV, 'bin', 'python');

// Colors
const c = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  green: '\x1b[32m',
  cyan: '\x1b[36m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  magenta: '\x1b[35m',
};

function log(msg) { console.log(msg); }
function logOk(msg) { console.log(`${c.green}✓${c.reset} ${msg}`); }
function logErr(msg) { console.log(`${c.red}✗${c.reset} ${msg}`); }
function logInfo(msg) { console.log(`${c.cyan}ℹ${c.reset} ${msg}`); }
function logStep(msg) { console.log(`${c.yellow}➤${c.reset} ${msg}`); }
function logDim(msg) { console.log(`${c.dim}  ${msg}${c.reset}`); }

function showLogo() {
  log('');
  log(`${c.cyan}${c.bold}╔══════════════════════════════════════════════════════════╗${c.reset}`);
  log(`${c.cyan}${c.bold}║                                                          ║${c.reset}`);
  log(`${c.cyan}${c.bold}║   🚀 Auto-FreeCF                                         ║${c.reset}`);
  log(`${c.cyan}${c.bold}║   ${c.dim}Cloudflare Workers AI Account ID & Token Grabber${c.cyan}       ║${c.reset}`);
  log(`${c.cyan}${c.bold}║                                                          ║${c.reset}`);
  log(`${c.cyan}${c.bold}╚══════════════════════════════════════════════════════════╝${c.reset}`);
  log(`${c.magenta}${c.dim}   By mmoaa${c.reset}`);
  log('');
}

function findPython() {
  for (const cmd of ['python3', 'python']) {
    try {
      const ver = execSync(`${cmd} --version 2>&1`, { encoding: 'utf8' }).trim();
      if (ver.startsWith('Python 3')) return cmd;
    } catch {}
  }
  return null;
}

function runSync(cmd, args, opts = {}) {
  try {
    // Use shell: true for all platforms - handles both simple commands and paths with spaces
    const result = spawnSync(cmd, args, {
      cwd: ROOT,
      stdio: opts.silent ? 'pipe' : 'inherit',
      shell: true,
    });
    
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Exit code ${result.status}`);
    return true;
  } catch (err) {
    if (!opts.silent) {
      logErr(`Command failed: ${cmd} ${args.join(' ')}`);
      if (err.message) log(`  ${err.message}`);
    }
    return false;
  }
}

function formatTime(ms) {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const s = sec % 60;
  return `${min}m ${s}s`;
}

function setup() {
  const totalStart = Date.now();

  // Check Python
  log(`${c.bold}📋 System Check${c.reset}`);
  log(`${c.dim}${'─'.repeat(50)}${c.reset}`);
  
  const python = findPython();
  if (!python) {
    logErr('Python 3 not found!');
    log('Install: https://www.python.org/downloads/');
    log('Make sure to check "Add Python to PATH" during install.');
    process.exit(1);
  }
  logOk(`Python found: ${python}`);

  // Create venv
  if (!fs.existsSync(VENV)) {
    logStep('Creating virtual environment...');
    logDim('This isolates Python dependencies (~10s)');
    const start = Date.now();
    if (!runSync(python, ['-m', 'venv', 'venv'])) {
      logErr('Failed to create virtual environment');
      process.exit(1);
    }
    logOk(`Virtual environment created (${formatTime(Date.now() - start)})`);
  } else {
    logOk('Virtual environment exists');
  }

  // Install deps
  if (!fs.existsSync(INSTALLED)) {
    log('');
    log(`${c.bold}📦 Installing Dependencies${c.reset}`);
    log(`${c.dim}${'─'.repeat(50)}${c.reset}`);
    logDim('First time setup — this may take a few minutes');
    log('');

    // Step 1: pip install
    logStep('[1/2] Installing Python packages...');
    logDim('Packages: httpx, curl_cffi, playwright, flask');
    logDim('Estimated time: ~30-60s');
    const pipStart = Date.now();
    if (!runSync(venvPython, ['-m', 'pip', 'install', '-q', '-r', 'requirements.txt'])) {
      logErr('Failed to install Python dependencies');
      process.exit(1);
    }
    logOk(`Python packages installed (${formatTime(Date.now() - pipStart)})`);
    log('');

    // Step 2: playwright install
    logStep('[2/2] Installing Chromium browser...');
    logDim('Downloading Chromium (~150MB)');
    logDim('Estimated time: ~1-3 min (depends on connection)');
    const pwStart = Date.now();
    if (!runSync(venvPython, ['-m', 'playwright', 'install', 'chromium'])) {
      logErr('Failed to install Chromium');
      process.exit(1);
    }
    logOk(`Chromium installed (${formatTime(Date.now() - pwStart)})`);

    fs.writeFileSync(INSTALLED, '');
    log('');
    log(`${c.dim}${'═'.repeat(50)}${c.reset}`);
    logOk(`All dependencies installed! Total: ${c.bold}${formatTime(Date.now() - totalStart)}${c.reset}`);
    log(`${c.dim}${'═'.repeat(50)}${c.reset}`);
  } else {
    logOk('Dependencies already installed');
  }
  log('');
}

function runPython(script, args = []) {
  return new Promise((resolve) => {
    const proc = spawn(venvPython, [path.join(ROOT, script), ...args], {
      stdio: 'inherit',
      cwd: ROOT,
    });
    proc.on('close', code => resolve(code));
    proc.on('error', err => {
      logErr(`Failed to start: ${err.message}`);
      resolve(1);
    });
  });
}

async function showMenu() {
  const readline = require('readline');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const ask = (q) => new Promise(resolve => rl.question(q, resolve));

  log(`${c.bold}Choose an option:${c.reset}`);
  log('');
  log(`  ${c.green}[1]${c.reset} 🌐 Web UI (browser interface)`);
  log(`  ${c.green}[2]${c.reset} 💻 Terminal UI (interactive menu)`);
  log(`  ${c.green}[3]${c.reset} 📝 Process accounts file`);
  log(`  ${c.green}[4]${c.reset} 🚪 Exit`);
  log('');

  const choice = await ask(`${c.bold}Select option${c.reset} ${c.dim}(1-4)${c.reset}: `);
  rl.close();

  switch (choice.trim()) {
    case '1':
      log('');
      logInfo('Starting Web UI on http://localhost:8080');
      await runPython('web_ui.py', ['--port', '8080']);
      break;
    case '2':
      log('');
      logInfo('Starting Terminal UI...');
      await runPython('terminal_ui.py');
      break;
    case '3': {
      log('');
      const rl2 = readline.createInterface({ input: process.stdin, output: process.stdout });
      const filepath = await new Promise(resolve =>
        rl2.question(`${c.cyan}Enter accounts file path${c.reset} ${c.dim}(default: accounts.txt)${c.reset}: `, resolve)
      );
      rl2.close();
      const fp = filepath.trim() || 'accounts.txt';
      logInfo(`Processing accounts from ${fp}...`);
      await runPython('browser_bot.py', ['--accounts', fp]);
      break;
    }
    case '4':
      log('');
      log(`${c.cyan}Goodbye! 👋${c.reset}`);
      break;
    default:
      log('');
      logErr('Invalid option');
  }
}

async function main() {
  showLogo();
  setup();
  await showMenu();
}

main().catch(err => {
  logErr(err.message);
  process.exit(1);
});

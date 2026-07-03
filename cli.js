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

const venvPip = IS_WIN
  ? path.join(VENV, 'Scripts', 'pip.exe')
  : path.join(VENV, 'bin', 'pip');

const venvPlaywright = IS_WIN
  ? path.join(VENV, 'Scripts', 'playwright.exe')
  : path.join(VENV, 'bin', 'playwright');

// Colors
const c = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  green: '\x1b[32m',
  cyan: '\x1b[36m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
};

function log(msg) { console.log(msg); }
function logOk(msg) { console.log(`${c.green}✓${c.reset} ${msg}`); }
function logErr(msg) { console.log(`${c.red}✗${c.reset} ${msg}`); }
function logInfo(msg) { console.log(`${c.cyan}ℹ${c.reset} ${msg}`); }

function showLogo() {
  log('');
  log(`${c.cyan}${c.bold}╔══════════════════════════════════════════════════════════╗${c.reset}`);
  log(`${c.cyan}${c.bold}║                                                          ║${c.reset}`);
  log(`${c.cyan}${c.bold}║   🚀 Auto-FreeCF                                         ║${c.reset}`);
  log(`${c.cyan}${c.bold}║   ${c.dim}Cloudflare Workers AI Account ID & Token Grabber${c.cyan}       ║${c.reset}`);
  log(`${c.cyan}${c.bold}║                                                          ║${c.reset}`);
  log(`${c.cyan}${c.bold}╚══════════════════════════════════════════════════════════╝${c.reset}`);
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
    // Windows needs special handling for paths with spaces
    const isWindows = process.platform === 'win32';
    const result = spawnSync(cmd, args, {
      cwd: ROOT,
      stdio: opts.silent ? 'pipe' : 'inherit',
      shell: isWindows, // Use shell on Windows to handle paths with spaces
      windowsVerbatimArguments: isWindows,
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

function setup() {
  // Check Python
  const python = findPython();
  if (!python) {
    logErr('Python 3 not found!');
    log('Install: https://www.python.org/downloads/');
    log('Make sure to check "Add Python to PATH" during install.');
    process.exit(1);
  }
  logInfo(`Python: ${python}`);

  // Create venv
  if (!fs.existsSync(VENV)) {
    logInfo('Creating virtual environment...');
    if (!runSync(python, ['-m', 'venv', 'venv'])) {
      logErr('Failed to create virtual environment');
      process.exit(1);
    }
    logOk('Virtual environment created');
  }

  // Install deps
  if (!fs.existsSync(INSTALLED)) {
    logInfo('Installing dependencies (first time, may take ~5 min)...');
    log('');

    if (!runSync(venvPython, ['-m', 'pip', 'install', '-q', '-r', 'requirements.txt'])) {
      logErr('Failed to install Python dependencies');
      process.exit(1);
    }

    logInfo('Installing browser (Chromium)...');
    if (!runSync(venvPython, ['-m', 'playwright', 'install', 'chromium'])) {
      logErr('Failed to install Chromium');
      process.exit(1);
    }

    fs.writeFileSync(INSTALLED, '');
    log('');
    logOk('All dependencies installed!');
  }
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
        rl2.question(`${c.cyan}Enter accounts file path${c.reset} ${c.dim}(default: accounts.json)${c.reset}: `, resolve)
      );
      rl2.close();
      const fp = filepath.trim() || 'accounts.json';
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

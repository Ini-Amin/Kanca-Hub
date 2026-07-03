#!/usr/bin/env node

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const ROOT = __dirname;
const VENV_DIR = path.join(ROOT, 'venv');
const INSTALLED_MARKER = path.join(VENV_DIR, '.installed');

// Colors
const colors = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  cyan: '\x1b[36m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  red: '\x1b[31m',
  magenta: '\x1b[35m',
};

function log(msg) { console.log(msg); }
function logOk(msg) { console.log(`${colors.green}✓${colors.reset} ${msg}`); }
function logInfo(msg) { console.log(`${colors.cyan}ℹ${colors.reset} ${msg}`); }
function logStep(msg) { console.log(`${colors.yellow}➤${colors.reset} ${msg}`); }
function logErr(msg) { console.log(`${colors.red}✗${colors.reset} ${msg}`); }

function findPython() {
  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  try {
    const result = spawnSync(pythonCmd, ['--version'], { encoding: 'utf8' });
    if (result.status === 0) return pythonCmd;
  } catch {}
  return null;
}

function runSync(cmd, args, options = {}) {
  const opts = { stdio: 'inherit', ...options };
  const isWin = process.platform === 'win32';
  const isFullPath = cmd.includes(path.sep) || cmd.includes('\\') || cmd.includes('/');
  
  if (isWin && isFullPath) {
    return spawnSync(cmd, args, opts).status === 0;
  } else {
    return spawnSync(cmd, args, { ...opts, shell: true }).status === 0;
  }
}

function runAsync(cmd, args, options = {}) {
  return new Promise((resolve) => {
    const opts = { stdio: 'inherit', ...options };
    const isWin = process.platform === 'win32';
    const isFullPath = cmd.includes(path.sep) || cmd.includes('\\') || cmd.includes('/');
    
    let proc;
    if (isWin && isFullPath) {
      proc = spawn(cmd, args, opts);
    } else {
      proc = spawn(cmd, args, { ...opts, shell: true });
    }
    
    proc.on('close', (code) => resolve(code === 0));
  });
}

function formatTime(ms) {
  const sec = Math.floor(ms / 1000);
  const min = Math.floor(sec / 60);
  const s = sec % 60;
  if (min > 0) return `${min}m ${s}s`;
  return `${s}s`;
}

async function setup() {
  const python = findPython();
  if (!python) {
    logErr('Python 3 not found! Please install Python 3.10+');
    process.exit(1);
  }
  
  if (!fs.existsSync(INSTALLED_MARKER)) {
    log('\n📦 Installing dependencies (first time only)...');
    log(`${colors.dim}This may take a few minutes...${colors.reset}\n`);
    
    logStep('Creating virtual environment...');
    if (!runSync(python, ['-m', 'venv', VENV_DIR])) {
      logErr('Failed to create virtual environment');
      process.exit(1);
    }
    logOk('Virtual environment created');
    
    logStep('Installing Python packages...');
    const pipStart = Date.now();
    const pipCmd = path.join(VENV_DIR, process.platform === 'win32' ? 'Scripts' : 'bin', 'pip');
    if (!runSync(pipCmd, ['install', '-q', '-r', 'requirements.txt'])) {
      logErr('Failed to install Python packages');
      process.exit(1);
    }
    logOk(`Python packages installed (${formatTime(Date.now() - pipStart)})`);
    
    logStep('Installing Playwright browsers...');
    const pwStart = Date.now();
    const pyCmd = path.join(VENV_DIR, process.platform === 'win32' ? 'Scripts' : 'bin', 'python');
    if (!runSync(pyCmd, ['-m', 'playwright', 'install', 'chromium'])) {
      logErr('Failed to install Playwright browsers');
      process.exit(1);
    }
    logOk(`Playwright browsers installed (${formatTime(Date.now() - pwStart)})`);
    
    fs.writeFileSync(INSTALLED_MARKER, new Date().toISOString());
    logOk('Setup complete!');
  }
}

function getPythonCmd() {
  return path.join(VENV_DIR, process.platform === 'win32' ? 'Scripts' : 'bin', 'python');
}

async function processSingle(emailPass, proxyFile) {
  const pyCmd = getPythonCmd();
  const browserBot = path.join(ROOT, 'browser_bot.py');
  const cmdArgs = [browserBot, '--single', emailPass];
  
  if (proxyFile) {
    cmdArgs.push('--proxy', proxyFile);
  }
  
  const success = await runAsync(pyCmd, cmdArgs);
  process.exit(success ? 0 : 1);
}

async function processBulk(filePath, proxyFile) {
  const pyCmd = getPythonCmd();
  const browserBot = path.join(ROOT, 'browser_bot.py');
  const cmdArgs = [browserBot, '--accounts', filePath, '--headless'];
  
  if (proxyFile) {
    cmdArgs.push('--proxy', proxyFile);
  }
  
  const success = await runAsync(pyCmd, cmdArgs);
  process.exit(success ? 0 : 1);
}

async function main() {
  log(`${colors.cyan}${colors.bold}`);
  log('╔══════════════════════════════════════════════════════════╗');
  log('║                                                          ║');
  log('║   🚀 Auto-FreeCF                                         ║');
  log('║   Cloudflare Workers AI Account ID & Token Grabber       ║');
  log('║                                                          ║');
  log('╚══════════════════════════════════════════════════════════╝');
  log(`${colors.reset}${colors.magenta}   By mmoaa${colors.reset}`);
  log(`${colors.yellow}${colors.bold}   ⚠️  BETA TESTING - Use at your own risk${colors.reset}\n`);
  
  await setup();
  
  // Parse arguments
  const args = process.argv.slice(2);
  const proxyArg = args.find(a => a.startsWith('--proxy='));
  const proxyFile = proxyArg ? proxyArg.split('=')[1] : null;
  
  // Check for file argument (bulk mode)
  const fileArg = args.find(a => !a.startsWith('--') && (a.endsWith('.txt') || a.endsWith('.json')));
  
  // Check for email:pass argument (single mode)
  const singleArg = args.find(a => !a.startsWith('--') && a.includes('@') && a.includes(':'));
  
  if (fileArg) {
    // CLI bulk mode
    logInfo(`Bulk mode: ${fileArg}`);
    if (proxyFile) logInfo(`Proxy: ${proxyFile}`);
    await processBulk(fileArg, proxyFile);
    return;
  }
  
  if (singleArg) {
    // CLI single mode
    logInfo(`Single mode: ${singleArg.split(':')[0]}`);
    if (proxyFile) logInfo(`Proxy: ${proxyFile}`);
    await processSingle(singleArg, proxyFile);
    return;
  }
  
  // Interactive mode - simplified
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });
  
  const question = (prompt) => new Promise(resolve => rl.question(prompt, resolve));
  
  log(`\n${colors.bold}Choose mode:${colors.reset}`);
  log(`  ${colors.green}[1]${colors.reset} Single account ${colors.dim}(enter email:password)${colors.reset}`);
  log(`  ${colors.green}[2]${colors.reset} Bulk accounts ${colors.dim}(from file)${colors.reset}`);
  log(`  ${colors.green}[3]${colors.reset} Exit\n`);
  
  const choice = await question(`${colors.bold}Select${colors.reset} ${colors.dim}(1-3)${colors.reset}: `);
  
  if (choice === '1') {
    const emailPass = await question(`${colors.cyan}Enter email:password${colors.reset}: `);
    if (!emailPass || !emailPass.includes(':')) {
      logErr('Invalid format. Use: email:password');
      rl.close();
      process.exit(1);
    }
    const proxy = await question(`${colors.dim}Proxy file (optional, Enter to skip)${colors.reset}: `);
    await processSingle(emailPass.trim(), proxy.trim() || null);
  } else if (choice === '2') {
    const file = await question(`${colors.cyan}Enter file path${colors.reset} ${colors.dim}(default: accounts.txt)${colors.reset}: `);
    const filePath = file.trim() || 'accounts.txt';
    if (!fs.existsSync(filePath)) {
      logErr(`File not found: ${filePath}`);
      rl.close();
      process.exit(1);
    }
    const proxy = await question(`${colors.dim}Proxy file (optional, Enter to skip)${colors.reset}: `);
    await processBulk(filePath, proxy.trim() || null);
  } else if (choice === '3') {
    log('\nGoodbye! 👋\n');
    rl.close();
    process.exit(0);
  } else {
    logErr('Invalid option');
    rl.close();
    process.exit(1);
  }
  
  rl.close();
}

main().catch(err => {
  logErr(err.message);
  process.exit(1);
});

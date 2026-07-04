#!/usr/bin/env node

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const ROOT = __dirname;
const IS_WIN = process.platform === 'win32';

// Use writable location for venv (avoid permission issues on Windows)
function getVenvDir() {
  if (IS_WIN) {
    // Use AppData\Local on Windows
    const appData = process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || process.env.HOME || '', 'AppData', 'Local');
    return path.join(appData, 'auto-freecf', 'venv');
  } else {
    // Use ~/.local/share on Linux/Mac
    const home = process.env.HOME || process.env.USERPROFILE || '/tmp';
    return path.join(home, '.local', 'share', 'auto-freecf', 'venv');
  }
}

const VENV_DIR = getVenvDir();
const INSTALLED_MARKER = path.join(VENV_DIR, '.installed');

// Python executable paths in venv
const PYTHON_EXE = path.join(VENV_DIR, IS_WIN ? 'Scripts' : 'bin', IS_WIN ? 'python.exe' : 'python');
const PIP_EXE = path.join(VENV_DIR, IS_WIN ? 'Scripts' : 'bin', IS_WIN ? 'pip.exe' : 'pip');

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
  // Try multiple Python commands on Windows
  const candidates = IS_WIN ? ['python', 'python3', 'py'] : ['python3', 'python'];
  
  for (const cmd of candidates) {
    try {
      const result = spawnSync(cmd, ['--version'], { encoding: 'utf8', shell: true });
      if (result.status === 0 && result.stdout) {
        const version = result.stdout.trim();
        // Check if it's Python 3
        if (version.match(/Python 3\./)) {
          logInfo(`Found ${version}`);
          return cmd;
        }
      }
    } catch {}
  }
  return null;
}

// Run command and capture output (for error reporting)
function runSyncCapture(cmd, args, options = {}) {
  const opts = { encoding: 'utf8', ...options };
  
  try {
    const result = spawnSync(cmd, args, opts);
    return {
      success: result.status === 0,
      stdout: result.stdout || '',
      stderr: result.stderr || '',
      status: result.status
    };
  } catch (err) {
    return {
      success: false,
      stdout: '',
      stderr: err.message,
      status: -1
    };
  }
}

// Run command with visible output (for interactive commands)
function runSync(cmd, args, options = {}) {
  const opts = { stdio: 'inherit', ...options };
  
  try {
    const result = spawnSync(cmd, args, opts);
    return result.status === 0;
  } catch (err) {
    logErr(`Command failed: ${err.message}`);
    return false;
  }
}

function runAsync(cmd, args, options = {}) {
  return new Promise((resolve) => {
    const opts = { stdio: 'inherit', ...options };
    
    let proc;
    try {
      proc = spawn(cmd, args, opts);
    } catch (err) {
      logErr(`Failed to start: ${err.message}`);
      resolve(false);
      return;
    }
    
    proc.on('error', (err) => {
      logErr(`Process error: ${err.message}`);
      resolve(false);
    });
    
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

// Ensure directory exists
function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

async function setup() {
  const python = findPython();
  if (!python) {
    logErr('Python 3 not found! Please install Python 3.10+');
    logInfo('Download from: https://www.python.org/downloads/');
    process.exit(1);
  }
  
  if (!fs.existsSync(INSTALLED_MARKER)) {
    log('\n📦 Installing dependencies (first time only)...');
    log(`${colors.dim}This may take a few minutes...${colors.reset}\n`);
    
    // Ensure venv parent directory exists
    logStep(`Creating virtual environment at: ${VENV_DIR}`);
    ensureDir(path.dirname(VENV_DIR));
    
    // Create venv with error capture
    const venvResult = runSyncCapture(python, ['-m', 'venv', VENV_DIR], { shell: true });
    
    if (!venvResult.success) {
      logErr('Failed to create virtual environment');
      if (venvResult.stderr) {
        log(`${colors.dim}Error: ${venvResult.stderr}${colors.reset}`);
      }
      
      // Try alternative: use --without-pip flag and install pip manually
      logInfo('Trying alternative method...');
      const altResult = runSyncCapture(python, ['-m', 'venv', '--without-pip', VENV_DIR], { shell: true });
      
      if (!altResult.success) {
        logErr('Alternative method also failed');
        if (altResult.stderr) {
          log(`${colors.dim}Error: ${altResult.stderr}${colors.reset}`);
        }
        logInfo('\nTroubleshooting:');
        logInfo('1. Make sure Python is installed correctly');
        logInfo('2. Try running: python -m venv test_env');
        logInfo('3. Check antivirus is not blocking venv creation');
        process.exit(1);
      }
    }
    logOk('Virtual environment created');
    
    // Install Python packages
    logStep('Installing Python packages...');
    const pipStart = Date.now();
    const reqPath = path.join(ROOT, 'requirements.txt');
    
    // Use python -m pip instead of pip directly (more reliable on Windows)
    const pipResult = runSyncCapture(PYTHON_EXE, ['-m', 'pip', 'install', '-q', '-r', reqPath], { 
      shell: true,
      timeout: 300000 // 5 minutes timeout
    });
    
    if (!pipResult.success) {
      logErr('Failed to install Python packages');
      if (pipResult.stderr) {
        log(`${colors.dim}${pipResult.stderr}${colors.reset}`);
      }
      process.exit(1);
    }
    logOk(`Python packages installed (${formatTime(Date.now() - pipStart)})`);
    
    // Install Playwright browsers
    logStep('Installing Playwright browsers...');
    const pwStart = Date.now();
    
    const pwResult = runSyncCapture(PYTHON_EXE, ['-m', 'playwright', 'install', 'chromium'], {
      shell: true,
      timeout: 600000 // 10 minutes timeout
    });
    
    if (!pwResult.success) {
      logErr('Failed to install Playwright browsers');
      if (pwResult.stderr) {
        log(`${colors.dim}${pwResult.stderr}${colors.reset}`);
      }
      process.exit(1);
    }
    logOk(`Playwright browsers installed (${formatTime(Date.now() - pwStart)})`);
    
    fs.writeFileSync(INSTALLED_MARKER, new Date().toISOString());
    logOk('Setup complete!');
  }
}

function getPythonCmd() {
  return PYTHON_EXE;
}

async function processSingle(emailPass, proxyFile) {
  const pyCmd = getPythonCmd();
  const browserBot = path.join(ROOT, 'browser_bot.py');
  const cmdArgs = [browserBot, '--single', emailPass];
  
  if (proxyFile) {
    cmdArgs.push('--proxy', proxyFile);
  }
  
  const success = await runAsync(pyCmd, cmdArgs, { shell: true });
  process.exit(success ? 0 : 1);
}

async function processBulk(filePath, proxyFile) {
  const pyCmd = getPythonCmd();
  const browserBot = path.join(ROOT, 'browser_bot.py');
  const cmdArgs = [browserBot, '--accounts', filePath, '--headless'];
  
  if (proxyFile) {
    cmdArgs.push('--proxy', proxyFile);
  }
  
  const success = await runAsync(pyCmd, cmdArgs, { shell: true });
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

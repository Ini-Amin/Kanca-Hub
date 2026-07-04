#!/usr/bin/env node

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');

const ROOT = __dirname;
const IS_WIN = process.platform === 'win32';

// ============================================================
// VENV LOCATION - Use writable, accessible path
// ============================================================
function getVenvDir() {
  if (IS_WIN) {
    const appData = process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || process.env.HOME || '', 'AppData', 'Local');
    return path.join(appData, 'auto-freecf', 'venv');
  } else {
    const home = process.env.HOME || process.env.USERPROFILE || '/tmp';
    return path.join(home, '.local', 'share', 'auto-freecf', 'venv');
  }
}

const VENV_DIR = getVenvDir();
const INSTALLED_MARKER = path.join(VENV_DIR, '.installed');

// Python/pip executables inside venv
const PYTHON_EXE = path.join(VENV_DIR, IS_WIN ? 'Scripts' : 'bin', IS_WIN ? 'python.exe' : 'python');
const PIP_EXE = path.join(VENV_DIR, IS_WIN ? 'Scripts' : 'bin', IS_WIN ? 'pip.exe' : 'pip');

// ============================================================
// COLORS
// ============================================================
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

// ============================================================
// FIND PYTHON - Resolve to FULL PATH (critical for Windows)
// ============================================================
function findPython() {
  const candidates = IS_WIN ? ['python', 'py'] : ['python3', 'python'];
  
  for (const cmd of candidates) {
    try {
      // Step 1: Resolve to full path using 'where' (Win) or 'which' (Linux)
      const whereCmd = IS_WIN ? 'where' : 'which';
      const whereResult = spawnSync(whereCmd, [cmd], { encoding: 'utf8' });
      
      if (whereResult.status !== 0 || !whereResult.stdout.trim()) continue;
      
      // Get first match (full path)
      const fullPath = whereResult.stdout.trim().split(/\r?\n/)[0].trim();
      if (!fullPath) continue;
      
      // Step 2: Verify it's Python 3.x
      // IMPORTANT: No shell:true here - pass full path directly
      const versionResult = spawnSync(fullPath, ['--version'], { encoding: 'utf8' });
      
      if (versionResult.status === 0) {
        const version = (versionResult.stdout || versionResult.stderr || '').trim();
        if (version.match(/Python 3\./)) {
          logInfo(`Found ${version} at ${fullPath}`);
          return fullPath;
        }
      }
    } catch {}
  }
  return null;
}

// ============================================================
// RUN COMMANDS - WITHOUT shell:true (fixes space-in-path bug)
// ============================================================

/**
 * Run command synchronously with output capture.
 * CRITICAL: No shell:true - this fixes WinError 5 on paths with spaces.
 * When shell:true is used on Windows, cmd.exe splits paths on spaces.
 */
function runCapture(exe, args, options = {}) {
  const opts = { encoding: 'utf8', ...options };
  
  try {
    const result = spawnSync(exe, args, opts);
    return {
      success: result.status === 0,
      stdout: result.stdout || '',
      stderr: result.stderr || '',
      status: result.status,
      error: result.error ? result.error.message : null
    };
  } catch (err) {
    return {
      success: false,
      stdout: '',
      stderr: err.message,
      status: -1,
      error: err.message
    };
  }
}

/**
 * Run command asynchronously with visible output.
 * No shell:true - fixes space-in-path issues.
 */
function runAsync(exe, args, options = {}) {
  return new Promise((resolve) => {
    const opts = { stdio: 'inherit', ...options };
    
    let proc;
    try {
      proc = spawn(exe, args, opts);
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

function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

// Remove directory recursively (for cleanup on failure)
function removeDir(dirPath) {
  try {
    if (fs.existsSync(dirPath)) {
      fs.rmSync(dirPath, { recursive: true, force: true });
    }
  } catch {}
}

// ============================================================
// SETUP
// ============================================================
async function setup() {
  const pythonExe = findPython();
  if (!pythonExe) {
    logErr('Python 3 not found! Please install Python 3.10+');
    logInfo('Download from: https://www.python.org/downloads/');
    logInfo('IMPORTANT: Check "Add Python to PATH" during installation');
    process.exit(1);
  }
  
  if (!fs.existsSync(INSTALLED_MARKER)) {
    log('\n📦 Installing dependencies (first time only)...');
    log(`${colors.dim}This may take a few minutes...${colors.reset}\n`);
    
    // Ensure venv parent directory exists
    logStep(`Creating virtual environment...`);
    logInfo(`Location: ${VENV_DIR}`);
    ensureDir(path.dirname(VENV_DIR));
    
    // Clean up any partial venv from previous failed attempt
    removeDir(VENV_DIR);
    
    // Create venv - NO shell:true (fixes space-in-path bug)
    const venvResult = runCapture(pythonExe, ['-m', 'venv', VENV_DIR], {
      timeout: 120000
    });
    
    if (!venvResult.success) {
      logErr('Failed to create virtual environment');
      if (venvResult.stderr) log(`${colors.dim}${venvResult.stderr}${colors.reset}`);
      if (venvResult.error) log(`${colors.dim}${venvResult.error}${colors.reset}`);
      
      // Try alternative: --without-pip
      logInfo('Trying alternative method (--without-pip)...');
      const altResult = runCapture(pythonExe, ['-m', 'venv', '--without-pip', VENV_DIR], {
        timeout: 120000
      });
      
      if (!altResult.success) {
        logErr('Alternative method also failed');
        if (altResult.stderr) log(`${colors.dim}${altResult.stderr}${colors.reset}`);
        
        logInfo('\n⚠ Troubleshooting:');
        logInfo('1. Try manually: ' + pythonExe + ' -m venv "' + VENV_DIR + '"');
        logInfo('2. Check antivirus is not blocking venv creation');
        logInfo('3. Try running PowerShell as Administrator');
        process.exit(1);
      }
      
      // If --without-pip worked, bootstrap pip
      logInfo('Bootstrapping pip...');
      const getpipResult = runCapture(pythonExe, ['-m', 'ensurepip', '--default-pip'], {
        timeout: 60000
      });
      if (!getpipResult.success) {
        logErr('Failed to bootstrap pip');
        process.exit(1);
      }
    }
    
    // Verify python.exe exists in venv
    if (!fs.existsSync(PYTHON_EXE)) {
      logErr('Virtual environment python not found at: ' + PYTHON_EXE);
      process.exit(1);
    }
    logOk('Virtual environment created');
    
    // Install Python packages - NO shell:true
    logStep('Installing Python packages...');
    const pipStart = Date.now();
    const reqPath = path.join(ROOT, 'requirements.txt');
    
    const pipResult = runCapture(PYTHON_EXE, ['-m', 'pip', 'install', '-q', '-r', reqPath], {
      timeout: 300000
    });
    
    if (!pipResult.success) {
      logErr('Failed to install Python packages');
      if (pipResult.stderr) log(`${colors.dim}${pipResult.stderr}${colors.reset}`);
      process.exit(1);
    }
    logOk(`Python packages installed (${formatTime(Date.now() - pipStart)})`);
    
    // Install patchright browsers - NO shell:true
    logStep('Installing browser (patchright/chromium)...');
    const pwStart = Date.now();
    
    const pwResult = runCapture(PYTHON_EXE, ['-m', 'patchright', 'install', 'chromium'], {
      timeout: 600000
    });
    
    if (!pwResult.success) {
      // Fallback: try playwright command (in case patchright aliases it)
      logInfo('Trying alternative browser install...');
      const pwResult2 = runCapture(PYTHON_EXE, ['-m', 'playwright', 'install', 'chromium'], {
        timeout: 600000
      });
      
      if (!pwResult2.success) {
        logErr('Failed to install browser');
        if (pwResult.stderr) log(`${colors.dim}${pwResult.stderr}${colors.reset}`);
        if (pwResult2.stderr) log(`${colors.dim}${pwResult2.stderr}${colors.reset}`);
        process.exit(1);
      }
    }
    logOk(`Browser installed (${formatTime(Date.now() - pwStart)})`);
    
    fs.writeFileSync(INSTALLED_MARKER, new Date().toISOString());
    logOk('Setup complete!');
  }
}

function getPythonCmd() {
  return PYTHON_EXE;
}

// ============================================================
// PROCESS ACCOUNTS
// ============================================================
async function processSingle(emailPass, proxyFile, loginMethod = 'email') {
  const pyCmd = getPythonCmd();
  const browserBot = path.join(ROOT, 'browser_bot.py');
  const cmdArgs = [browserBot, '--single', emailPass];
  
  if (proxyFile) {
    cmdArgs.push('--proxy', proxyFile);
  }
  
  if (loginMethod === 'google') {
    cmdArgs.push('--login-method', 'google');
  }
  
  // NO shell:true - fixes space-in-path
  const success = await runAsync(pyCmd, cmdArgs);
  process.exit(success ? 0 : 1);
}

async function processBulk(filePath, proxyFile, loginMethod = 'email') {
  const pyCmd = getPythonCmd();
  const browserBot = path.join(ROOT, 'browser_bot.py');
  const cmdArgs = [browserBot, '--accounts', filePath];
  
  if (proxyFile) {
    cmdArgs.push('--proxy', proxyFile);
  }
  
  if (loginMethod === 'google') {
    cmdArgs.push('--login-method', 'google');
  }
  
  // NO shell:true - fixes space-in-path
  const success = await runAsync(pyCmd, cmdArgs);
  process.exit(success ? 0 : 1);
}

// ============================================================
// MAIN
// ============================================================
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
  const loginMethod = args.includes('--google') ? 'google' : 'email';
  
  const fileArg = args.find(a => !a.startsWith('--') && (a.endsWith('.txt') || a.endsWith('.json')));
  const singleArg = args.find(a => !a.startsWith('--') && a.includes('@') && a.includes(':'));
  
  if (fileArg) {
    logInfo(`Bulk mode: ${fileArg}`);
    logInfo(`Login method: ${loginMethod}`);
    if (proxyFile) logInfo(`Proxy: ${proxyFile}`);
    await processBulk(fileArg, proxyFile, loginMethod);
    return;
  }
  
  if (singleArg) {
    logInfo(`Single mode: ${singleArg.split(':')[0]}`);
    logInfo(`Login method: ${loginMethod}`);
    if (proxyFile) logInfo(`Proxy: ${proxyFile}`);
    await processSingle(singleArg, proxyFile, loginMethod);
    return;
  }
  
  // Interactive mode
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });
  
  const question = (prompt) => new Promise(resolve => rl.question(prompt, resolve));
  
  log(`\n${colors.bold}Choose mode:${colors.reset}`);
  log(`  ${colors.green}[1]${colors.reset} Single account ${colors.dim}(email:password)${colors.reset}`);
  log(`  ${colors.green}[2]${colors.reset} Single account ${colors.dim}(Google OAuth)${colors.reset}`);
  log(`  ${colors.green}[3]${colors.reset} Bulk accounts ${colors.dim}(from file)${colors.reset}`);
  log(`  ${colors.green}[4]${colors.reset} Exit\n`);
  
  const choice = await question(`${colors.bold}Select${colors.reset} ${colors.dim}(1-4)${colors.reset}: `);
  
  if (choice === '1') {
    const emailPass = await question(`${colors.cyan}Enter email:password${colors.reset}: `);
    if (!emailPass || !emailPass.includes(':')) {
      logErr('Invalid format. Use: email:password');
      rl.close();
      process.exit(1);
    }
    const proxy = await question(`${colors.dim}Proxy file (optional, Enter to skip)${colors.reset}: `);
    await processSingle(emailPass.trim(), proxy.trim() || null, 'email');
  } else if (choice === '2') {
    const emailPass = await question(`${colors.cyan}Enter Google email:password${colors.reset}: `);
    if (!emailPass || !emailPass.includes(':')) {
      logErr('Invalid format. Use: google_email:password');
      rl.close();
      process.exit(1);
    }
    const proxy = await question(`${colors.dim}Proxy file (optional, Enter to skip)${colors.reset}: `);
    await processSingle(emailPass.trim(), proxy.trim() || null, 'google');
  } else if (choice === '3') {
    const file = await question(`${colors.cyan}Enter file path${colors.reset} ${colors.dim}(default: accounts.txt)${colors.reset}: `);
    const filePath = file.trim() || 'accounts.txt';
    if (!fs.existsSync(filePath)) {
      logErr(`File not found: ${filePath}`);
      rl.close();
      process.exit(1);
    }
    const proxy = await question(`${colors.dim}Proxy file (optional, Enter to skip)${colors.reset}: `);
    await processBulk(filePath, proxy.trim() || null, 'email');
  } else if (choice === '4') {
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

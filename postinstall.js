// postinstall.js - runs after npm install
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = __dirname;
const VENV = path.join(ROOT, 'venv');
const INSTALLED = path.join(VENV, '.installed');

console.log('🚀 Auto-FreeCF - Setting up...');

// Find Python
function findPython() {
  for (const cmd of ['python3', 'python']) {
    try {
      const ver = execSync(`${cmd} --version 2>&1`, { encoding: 'utf8' }).trim();
      if (ver.startsWith('Python 3')) return cmd;
    } catch {}
  }
  return null;
}

const python = findPython();
if (!python) {
  console.error('❌ Python 3 not found! Install: https://www.python.org/downloads/');
  process.exit(1);
}
console.log(`✓ Python: ${python}`);

// Create venv
if (!fs.existsSync(VENV)) {
  console.log('📦 Creating virtual environment...');
  execSync(`${python} -m venv venv`, { cwd: ROOT, stdio: 'inherit' });
  console.log('✓ Virtual environment created');
}

// Install deps
if (!fs.existsSync(INSTALLED)) {
  console.log('📦 Installing dependencies (this may take ~5 min)...');
  const pip = process.platform === 'win32'
    ? path.join(VENV, 'Scripts', 'pip')
    : path.join(VENV, 'bin', 'pip');
  
  execSync(`${pip} install -q -r requirements.txt`, { cwd: ROOT, stdio: 'inherit' });
  
  const patchright = process.platform === 'win32'
    ? path.join(VENV, 'Scripts', 'patchright')
    : path.join(VENV, 'bin', 'patchright');
  
  execSync(`${patchright} install chromium`, { cwd: ROOT, stdio: 'inherit' });
  
  fs.writeFileSync(INSTALLED, '');
  console.log('✓ Dependencies installed');
}

console.log('✅ Setup complete! Run `moycf` to start.');

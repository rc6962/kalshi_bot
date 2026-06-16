#!/usr/bin/env python
"""
setup_env.py

Automates:
  • Removal of an existing virtual environment (`venv` folder)
  • Creation of a fresh venv using the currently‑available Python interpreter
  • Installation of required packages
  • A short run of main.py to confirm Google‑Sheets integration

Usage (run from the project root):
    python setup_env.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
VENV_DIR = PROJECT_ROOT / "venv"

def run(cmd, env=None, cwd=None):
    """Run a command, stream output, raise on error."""
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return result

def remove_old_venv():
    if VENV_DIR.is_dir():
        print(f"Removing existing venv at {VENV_DIR} …")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        print("✅ Old venv removed.")
    else:
        print("No previous venv folder found; proceeding.")

def create_venv():
    print(f"Creating new virtual environment in {VENV_DIR} …")
    run([sys.executable, "-m", "venv", str(VENV_DIR)])
    print("✅ Venv created.")

def pip_path():
    """Return the path to the pip executable inside the venv."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "pip.exe"
    else:
        return VENV_DIR / "bin" / "pip"

def python_path():
    """Return the path to the python executable inside the venv."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    else:
        return VENV_DIR / "bin" / "python"

def install_requirements():
    pip_exe = str(pip_path())
    # Upgrade pip first
    run([pip_exe, "install", "--upgrade", "pip"])

    # Core dependencies for the bot
    packages = [
        "gspread",
        "google-auth",
        "aiohttp",
        "websockets",
        "cryptography",
    ]
    print("\nInstalling required packages …")
    run([pip_exe, "install"] + packages)
    print("✅ Packages installed.")

def run_sanity_check():
    py_exe = str(python_path())
    print("\nRunning a quick sanity check (main.py) …")
    print("Press Ctrl+C after you see the line: \"Google Sheets integration initialized successfully!\"")
    try:
        run([py_exe, "main.py"], cwd=str(PROJECT_ROOT))
    except KeyboardInterrupt:
        print("\n⚠️  User interrupted – sanity check complete.")
    except Exception as e:
        print(f"\n❌  Sanity check failed: {e}")

def main():
    print("\n=== Kalshi Bot Environment Setup ===")
    remove_old_venv()
    create_venv()
    install_requirements()
    run_sanity_check()
    print("\n=== Setup finished! ===")
    print("You are now ready to run the bot:")
    print("    <project_root>\\venv\\Scripts\\python.exe main.py")
    print("\nTo deactivate the virtual environment later, just exit the terminal or run `deactivate` (if you activate it manually).")

if __name__ == "__main__":
    main()

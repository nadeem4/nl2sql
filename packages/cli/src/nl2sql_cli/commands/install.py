import sys
import subprocess
from rich.prompt import Confirm
from nl2sql_cli.config import KNOWN_ADAPTERS
from nl2sql_cli.console import console, print_success, print_error, print_step

def install_package(package_name: str) -> bool:
    print_step(f"Installing {package_name}...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        print_success(f"Installed {package_name}")
        return True
    except subprocess.CalledProcessError:
        print_error(f"Failed to install {package_name}")
        return False

def install_command(adapter_name: str):
    target_pkg = adapter_name
    if adapter_name in KNOWN_ADAPTERS:
        target_pkg = KNOWN_ADAPTERS[adapter_name]
    
    if Confirm.ask(f"Install [cyan]{target_pkg}[/cyan]?"):
        install_package(target_pkg)

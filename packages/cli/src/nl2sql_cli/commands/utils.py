import sys
import subprocess
import json
from typing import List, Dict, Any, Optional
from nl2sql_cli.config import CORE_MODULE
from nl2sql_cli.console import print_error

def run_core_command(args: List[str], capture_json: bool = False, stream: bool = False) -> Optional[Any]:
    """
    Executes a command against the Core module via subprocess.
    
    Args:
        args: List of command line arguments (e.g. ["--diagnose", "--json"])
        capture_json: If True, captures stdout and parses as JSON.
        stream: If True, streams output to console (for long running tasks).
    """
    cmd = [sys.executable, "-m", CORE_MODULE] + args
    
    try:
        if stream:
            # Stream output directly to user terminal
            subprocess.run(cmd, check=True)
            return None
            
        elif capture_json:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # Try to parse error JSON if available
                try:
                    err_data = json.loads(result.stdout)
                    if "error" in err_data:
                        print_error(f"Core Error: {err_data['error']}")
                        return None
                except json.JSONDecodeError:
                    pass
                
                print_error(f"Core process failed (Exit Code {result.returncode})")
                if result.stderr:
                    print_error(f"Stderr: {result.stderr.strip()}")
                return None
            
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                print_error("Failed to parse JSON response from Core.")
                print_error(f"Raw Output: {result.stdout[:200]}...") # truncate
                return None
        
        else:
             # Standard run, capture nothing
             subprocess.run(cmd, check=True)
             return None

    except FileNotFoundError:
        print_error(f"Python executable not found? ({sys.executable})")
        return None
    except subprocess.CalledProcessError as e:
        print_error(f"Command failed: {e}")
        return None
    except Exception as e:
        print_error(f"Unexpected error invoking Core: {e}")
        return None

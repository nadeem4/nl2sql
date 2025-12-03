import sys
import os
import subprocess

def launch_dashboard() -> None:
    # dashboard.py is in src/nl2sql/dashboard.py
    # This file is src/nl2sql/commands/dashboard.py
    # So we need to go up one level to find dashboard.py
    
    current_dir = os.path.dirname(__file__)
    # src/nl2sql/commands/../dashboard.py -> src/nl2sql/dashboard.py
    dashboard_path = os.path.abspath(os.path.join(current_dir, "..", "dashboard.py"))
    
    print(f"Launching Streamlit Dashboard from {dashboard_path}...")
    try:
        # Use sys.executable to run streamlit module directly
        subprocess.run([sys.executable, "-m", "streamlit", "run", dashboard_path], check=True)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    except Exception as e:
        print(f"Error launching dashboard: {e}", file=sys.stderr)

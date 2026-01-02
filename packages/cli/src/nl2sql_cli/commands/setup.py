from rich.prompt import Confirm
from nl2sql_cli.console import console, print_success, print_step, print_error
from nl2sql_cli.config import KNOWN_ADAPTERS, CORE_PACKAGE
from nl2sql_cli.commands.doctor import check_package
from nl2sql_cli.commands.install import install_package

def setup_command():
    console.print("[bold cyan]NL2SQL Setup Wizard[/bold cyan]\n")

    # 1. Dependency Check
    if not check_package(CORE_PACKAGE):
        console.print("[yellow]Core package missing.[/yellow]")
        if Confirm.ask(f"Install {CORE_PACKAGE}?"):
            if install_package(CORE_PACKAGE):
                print_success("Core installed.")
            else:
                print_error("Failed to install Core.")
                return # Exit or continue? Better to return if core is critical logic later.
    else:
        print_success("Environment OK.")

    # 2. Adapters
    print_step("Checking Adapters...")
    missing = []
    for name, pkg in KNOWN_ADAPTERS.items():
        import_name = pkg.replace("-", "_")
        if not check_package(import_name):
            missing.append((name, pkg))
    
    if missing:
        console.print(f"[yellow]Found {len(missing)} missing adapters.[/yellow]")
        if Confirm.ask("Install missing adapters?"):
            for name, pkg in missing:
                if Confirm.ask(f"Install {name}?"):
                    install_package(pkg)
    else:
        print_success("All known adapters installed.")

    # 3. Connectivity Check
    print_step("Checking Database Connectivity...")
    
    from nl2sql_cli.commands.utils import run_core_command
    
    data = run_core_command(["--diagnose", "--json"], capture_json=True)
    
    if data and "connectivity" in data:
        results = data["connectivity"]
        all_ok = True
        
        for ds_id, info in results.items():
            success = info.get("ok", False)
            msg = info.get("details", "")
            
            if success:
                console.print(f"[green]✔ {ds_id}: OK[/green]")
            else:
                console.print(f"[red]✘ {ds_id}: Failed ({msg})[/red]")
                all_ok = False
        
        if not all_ok:
            console.print("[yellow]Warning: Some datasources are failing validation.[/yellow]")
            if not Confirm.ask("Continue anyway?"):
                return
    else:
         console.print("[yellow]Connectivity check incomplete (Core offline or no output).[/yellow]")

    # 4. Indexing
    console.print("")
    if Confirm.ask("Run Schema Indexing now?"):
        print_step("Starting Indexer...")
        from nl2sql_cli.commands.utils import run_core_command
        
        # Stream the output so user sees progress
        run_core_command(["--index"], stream=True)
        print_success("Indexing process finished.")

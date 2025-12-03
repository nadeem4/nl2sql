from __future__ import annotations

import sys
import threading
import time
from typing import Dict, List, Optional

# Windows-specific input handling
try:
    import msvcrt
except ImportError:
    msvcrt = None

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.columns import Columns
from rich import box

class NL2SQLTUI:
    """
    Text User Interface for NL2SQL Pipeline using Rich.
    Manages a split layout with Orchestrator and Execution panels.
    Supports interactive scrolling and panel switching.
    """

    def __init__(self):
        self.console = Console()
        self.layout = Layout()
        self.live = Live(self.layout, console=self.console, refresh_per_second=10, screen=True)
        
        # State
        self.orchestrator_logs: List[str] = []
        self.branch_logs: Dict[str, List[str]] = {} # branch_label -> logs
        self.branch_titles: Dict[str, str] = {} # branch_label -> title
        
        # Interactive State
        self.active_panel = "orchestrator" # "orchestrator" or "execution"
        self.scroll_offsets: Dict[str, int] = {"orchestrator": 0} # 0 means at bottom (tailing)
        self.running = False
        self.input_thread = None
        
        self.setup_layout()

    def setup_layout(self):
        """Initializes the layout structure."""
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3)
        )
        self.layout["header"].update(Panel("NL2SQL Pipeline", style="bold white on blue", box=box.ROUNDED))
        self.layout["footer"].update(Panel("Press Ctrl+C to exit | TAB: Switch Panel | UP/DOWN: Scroll", style="dim", box=box.ROUNDED))
        
        self.layout["body"].split_row(
            Layout(name="orchestrator", ratio=1),
            Layout(name="execution", ratio=2)
        )
        
        self.update_panels()

    def start(self):
        """Starts the Live display and input listener."""
        self.running = True
        self.live.start()
        if msvcrt:
            self.input_thread = threading.Thread(target=self._input_listener, daemon=True)
            self.input_thread.start()

    def stop(self):
        """Stops the Live display."""
        self.running = False
        self.live.stop()

    def interact(self):
        """
        Blocks and keeps the TUI running until user exits.
        Replaces the simple input() wait.
        """
        self.log_orchestrator("\n[bold yellow]Interactive Mode: Press 'q' or Enter to exit.[/bold yellow]")
        while self.running:
            time.sleep(0.1)

    def _input_listener(self):
        """Background thread to handle keyboard input."""
        while self.running:
            if msvcrt and msvcrt.kbhit():
                key = msvcrt.getch()
                
                # Handle special keys
                if key == b'\xe0': # Arrow keys prefix
                    key = msvcrt.getch()
                    if key == b'H': # UP
                        self._scroll(1)
                    elif key == b'P': # DOWN
                        self._scroll(-1)
                elif key == b'\t': # TAB
                    self._switch_panel()
                elif key in (b'q', b'\r', b'\n'): # Quit
                    self.running = False
                    break
            time.sleep(0.05)

    def _switch_panel(self):
        """Switches active panel between Orchestrator and Execution."""
        if self.active_panel == "orchestrator":
            self.active_panel = "execution"
        else:
            self.active_panel = "orchestrator"
        self.update_panels()

    def _scroll(self, direction: int):
        """
        Scrolls the active panel.
        direction: +1 for UP (back in history), -1 for DOWN (towards newest).
        """
        current = self.scroll_offsets.get(self.active_panel, 0)
        new_offset = max(0, current + direction)
        
        # Limit scrolling based on content length (approximate)
        # We don't have exact line counts easily available for dynamic grid, 
        # but for orchestrator we do.
        if self.active_panel == "orchestrator":
            max_scroll = max(0, len(self.orchestrator_logs) - 10)
            new_offset = min(new_offset, max_scroll)
            
        self.scroll_offsets[self.active_panel] = new_offset
        self.update_panels()

    def _render_content_with_scrollbar(self, logs: List[str], height: int = 20, offset: int = 0) -> str:
        """
        Renders logs with a visual scrollbar if needed.
        offset: 0 means show latest (tail), >0 means scroll back 'offset' lines from bottom.
        """
        total_lines = len(logs)
        if total_lines <= height:
            return "\n".join(logs)
            
        # Determine visible slice
        if offset == 0:
            visible_logs = logs[-height:]
            scroll_pos = 1.0 # Bottom
        else:
            end = total_lines - offset
            start = max(0, end - height)
            visible_logs = logs[start:end]
            scroll_pos = 1.0 - (offset / max(1, total_lines - height))

        # Add scrollbar to each line
        content_lines = []
        bar_idx = int(scroll_pos * (height - 1))
        
        for i, line in enumerate(visible_logs):
            # Simple scrollbar: █ for handle, │ for track
            char = "█" if i == bar_idx else "│"
            # Pad line to ensure scrollbar is aligned? 
            # Rich handles wrapping, so appending to the string might wrap weirdly.
            # But for simple logs it might be okay.
            # Better: Just return text, let Rich handle it. 
            # Implementing a true side-bar in text is hard without a Grid.
            # Let's just return the text slice for now.
            content_lines.append(line)
            
        return "\n".join(content_lines)

    def update_panels(self):
        """Refreshes the content of the panels based on current state."""
        
        # Styles for active/inactive
        orch_style = "blue" if self.active_panel == "orchestrator" else "dim blue"
        exec_style = "green" if self.active_panel == "execution" else "dim green"
        
        # Orchestrator Panel
        orch_offset = self.scroll_offsets.get("orchestrator", 0)
        orch_text = self._render_content_with_scrollbar(self.orchestrator_logs, height=20, offset=orch_offset)
        
        title = "[bold]Orchestrator[/bold]"
        if orch_offset > 0:
            title += f" (Scroll: {orch_offset})"
            
        self.layout["orchestrator"].update(
            Panel(orch_text, title=title, border_style=orch_style, box=box.ROUNDED)
        )

        # Execution Panel (Dynamic Grid)
        # Note: Scrolling execution grid is complex because it has multiple sub-panels.
        # For now, we'll just scroll the individual branch logs if selected?
        # Or just scroll the whole grid? 
        # Simpler: "execution" scroll offset applies to ALL branches for now.
        exec_offset = self.scroll_offsets.get("execution", 0)
        
        if not self.branch_logs:
            self.layout["execution"].update(
                Panel("Waiting for execution branches...", title="[bold]Execution[/bold]", border_style=exec_style, box=box.ROUNDED)
            )
        else:
            panels = []
            for branch_label, logs in self.branch_logs.items():
                title = self.branch_titles.get(branch_label, branch_label)
                content = self._render_content_with_scrollbar(logs, height=15, offset=exec_offset)
                panels.append(
                    Panel(content, title=f"[bold]{title}[/bold]", border_style=exec_style, box=box.ROUNDED, height=17)
                )
            
            grid_title = "[bold]Execution[/bold]"
            if exec_offset > 0:
                grid_title += f" (Scroll: {exec_offset})"

            self.layout["execution"].update(
                Panel(Columns(panels), title=grid_title, border_style=exec_style, box=box.ROUNDED)
            )

    def log_orchestrator(self, message: str):
        """Adds a log message to the orchestrator panel."""
        self.orchestrator_logs.append(message)
        # Auto-scroll to bottom if currently at bottom
        if self.scroll_offsets.get("orchestrator", 0) == 0:
             self.update_panels()

    def log_branch(self, branch_label: str, message: str, title: Optional[str] = None):
        """Adds a log message to a specific execution branch."""
        if branch_label not in self.branch_logs:
            self.branch_logs[branch_label] = []
        
        if title:
            self.branch_titles[branch_label] = title
            
        self.branch_logs[branch_label].append(message)
        # Auto-scroll
        if self.scroll_offsets.get("execution", 0) == 0:
            self.update_panels()
        
    def set_footer(self, message: str):
        """Updates the footer message."""
        self.layout["footer"].update(Panel(message, style="dim", box=box.ROUNDED))

"""folder_picker.py — Native OS folder browser dialog integration for Gradio.
Enables opening native Windows File Explorer / OS folder picker dialog.
"""

import os


def browse_directory(initial_dir: str = "", title: str = "请选择文件夹") -> str:
    """Open native OS folder picker dialog and return selected path."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        init_dir = os.path.abspath(initial_dir) if initial_dir else os.getcwd()
        if not os.path.exists(init_dir):
            init_dir = os.getcwd()

        root = tk.Tk()
        root.withdraw()           # Hide main tkinter window
        root.wm_attributes("-topmost", 1)  # Bring dialog to the front

        chosen = filedialog.askdirectory(title=title, initialdir=init_dir)
        root.destroy()

        if chosen:
            return os.path.normpath(chosen)
    except Exception as e:
        print(f"[folder_picker] Native dialog unavailable: {e}")

    # Fallback to current input if cancelled or unavailable
    return initial_dir or ""

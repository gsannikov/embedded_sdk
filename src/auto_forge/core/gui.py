"""
Script:         gui.py
Author:         AutoForge Team

Description:
    Core module defining the 'CoreGUI' class.
    CoreGUI manages the graphical user interface (GUI) layer of the AutoForge
    system while ensuring that all GUI interactions are handled safely from the
    main thread, provides thread-safe message box support, and coordinates
    event dispatching across core modules through a managed Tkinter loop.
"""

import atexit
import platform
import queue
import sys
import threading
import time
from contextlib import suppress

# Third-party
with suppress(ImportError):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from typing import Any, Optional

# AutoForge imports
from auto_forge import (AutoForgeModuleType, AutoLogger, CoreModuleInterface, InputBoxButtonType, InputBoxLineType,
                        InputBoxTextType, MessageBoxType, Registry, )

AUTO_FORGE_MODULE_NAME = "GUI"
AUTO_FORGE_MODULE_DESCRIPTION = "Set of several GUI notification routines"


class CoreGUI(CoreModuleInterface):
    def __init__(self, *args, **kwargs):

        self._alive = True
        self._gui_thread = None  # Optional joinable thread

        # This will terminate AutoForge if tkinter is not installed.
        # This is required because tkinter must be installed as a system package and cannot be installed via 'pip install'.
        self._validate_tkinter_libraries()

        super().__init__(*args, **kwargs)

    def _initialize(self, **_kwargs: Any):
        """
        Initializes the GUI system. Spawns a thread to run user logic,
        and keeps the GUI running in the main thread.
        """

        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(f"{self.__class__.__name__} must be initialized from the main thread!")

        self._root = tk.Tk()
        self._root.withdraw()
        self._msg_queue = queue.Queue()
        self._response_queue = queue.Queue()
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)

        # Register finalizer just in case
        atexit.register(self._shutdown)

        # Add to AutoForge modules registry
        Registry.get_instance().register_module(name=AUTO_FORGE_MODULE_NAME, description=AUTO_FORGE_MODULE_DESCRIPTION,
                                                auto_forge_module_type=AutoForgeModuleType.CORE)

        self._root.after(100, lambda: self._process_queue())  # type: ignore

    @staticmethod
    def _validate_tkinter_libraries() -> None:
        """
        Checks for required third-party or system-bound libraries like 'tkinter'.
        If not found, provides OS-specific installation instructions and exits.
        """
        try:
            import tkinter  # noqa: F401
        except ImportError:
            message = ["\nError: 'tkinter' is required but not installed."]

            system = platform.system()

            if system == "Linux":
                try:
                    with open("/etc/os-release") as f:
                        os_release = f.read().lower()
                    if "fedora" in os_release:
                        message.append("On Fedora, install it with: sudo dnf install python3-tkinter")
                    elif "ubuntu" in os_release or "debian" in os_release:
                        message.append("On Ubuntu or Debian, install it with: sudo apt install python3-tk")
                    else:
                        message.append("Please install 'tkinter' using your Linux distribution's package manager.")
                except Exception as exception:
                    message.append(f"Exception: {exception}")
            elif system == "Darwin":
                message.append("On macOS, ensure Python was installed via python.org or Homebrew with Tcl/Tk support.")
            elif system == "Windows":
                message.append(
                    "On Windows, ensure you are using the official Python installer from python.org, which includes 'tkinter' by default.")
            else:
                message.append("Please ensure 'tkinter' is available in your Python environment.")

            sys.stderr.write("\n".join(message) + "\n\n")
            sys.exit(1)

    def _process_queue(self):
        """
        Internal method that processes GUI function requests from the message queue.
        Repeatedly scheduled via `after()` to run on the Tkinter event loop.
        """
        try:
            while True:
                func = self._msg_queue.get_nowait()
                result = func()
                self._response_queue.put(result)
        except queue.Empty:
            pass
        self._root.after(100, lambda: self._process_queue())  # type: ignore

    def _shutdown(self):
        """
        Gracefully shuts down the GUI system.
        Stops the Tkinter event loop, destroys the root window,
        and joins the background GUI logic thread if one is running.
        """
        self._alive = False
        try:
            self._root.quit()
            self._root.destroy()
        except tk.TclError:
            pass
        if self._gui_thread:
            self._gui_thread.join(timeout=2)

    def __del__(self):
        self._shutdown()

    def _wait_for_response(self) -> Optional[Any]:
        """
        Waits for a GUI response from the response queue, while processing Tkinter events.
        Returns:
            Optional[Any]: The result from the response queue, or None if timed out or GUI closed.
        """

        while True:
            try:
                # Try to get a result without blocking
                return self._response_queue.get_nowait()
            except queue.Empty:
                pass  # No result yet; continue

            try:
                self._root.update_idletasks()
                self._root.update()
            except tk.TclError:
                return None  # GUI closed

            time.sleep(0.05)  # Small sleep to avoid busy-wait

    def input_box(self, caption: str, button_type: InputBoxButtonType, lines: list[InputBoxLineType],
                  centered: bool = True, top_most: bool = True) -> dict[str, str]:
        """
        Displays a customizable input dialog with one or more labeled fields.

        Args:
            caption (str): Title of the dialog window.
            button_type (InputBoxButtonType): Button layout (e.g., OK, OK/Cancel).
            lines (list[InputBoxLineType]): List of labeled input fields.
            centered (bool): Whether to center the dialog on the screen.
            top_most (bool): Whether to display the dialog as a topmost window.

        Returns:
            dict[str, str]: A dictionary mapping line labels to user input.
                            Returns an empty dict if the dialog is canceled or times out.
        """

        def _show_input_box() -> dict[str, str]:
            result: dict[str, str] = {}

            def on_ok():
                """ OK Button handler """
                for i, single_entry in enumerate(entries):
                    result[lines[i].label] = single_entry.get()
                dialog.destroy()

            def on_cancel():
                """ Cancel Button handler """
                result.clear()
                dialog.destroy()

            dialog = tk.Toplevel(self._root)
            dialog.title(caption)
            dialog.attributes('-topmost', top_most)

            if centered:
                dialog.update_idletasks()
                w = dialog.winfo_reqwidth()
                h = dialog.winfo_reqheight()
                x = (dialog.winfo_screenwidth() // 2) - (w // 2)
                y = (dialog.winfo_screenheight() // 2) - (h // 2)
                dialog.geometry(f"+{x}+{y}")

            entries = []
            for line in lines:
                frame = ttk.Frame(dialog)
                frame.pack(padx=10, pady=5, fill='x')
                ttk.Label(frame, text=line.label, width=20).pack(side='left')
                entry = ttk.Entry(frame, width=line.length if line.length > 0 else 20,
                                  show='*' if line.text_type == InputBoxTextType.INPUT_PASSWORD else '')
                entry.insert(0, line.input_text)
                entry.pack(side='left', expand=True, fill='x')
                entries.append(entry)

            button_frame = ttk.Frame(dialog)
            button_frame.pack(pady=10)

            ttk.Button(button_frame, text="OK", command=on_ok).pack(side='left', padx=5)
            if button_type == InputBoxButtonType.INPUT_CANCEL:
                ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side='left', padx=5)

            dialog.protocol("WM_DELETE_WINDOW", on_cancel)
            dialog.resizable(False, False)
            dialog.grab_set()
            dialog.wait_window()
            return result

        self._msg_queue.put(_show_input_box)
        return self._wait_for_response()

    def message_box(self, text: str, caption: str, box_type: MessageBoxType, _centered: bool = True,
                    top_most: bool = True) -> Optional[str]:
        """
        Displays a message box to the user in a thread-safe and GUI-friendly way.

        Args:
            text (str): The message content to display.
            caption (str): The window title of the message box.
            box_type (MessageBoxType): The type of message box to show (e.g., OK, Yes/No).
            _centered (bool): Currently unused. Reserved for future positioning support.
            top_most (bool): If True, the message box is displayed as a topmost window.

        Returns:
            Optional[str or bool]: The result of the dialog, which may vary depending
            on the box type (e.g., True/False for yes/no, None for cancel).
        """

        def _show_message_box():
            win = tk.Toplevel()
            win.withdraw()
            win.attributes('-topmost', top_most)

            if box_type == MessageBoxType.MB_OK:
                result = messagebox.showinfo(caption, text, parent=win)
            elif box_type == MessageBoxType.MB_OKCANCEL:
                result = messagebox.askokcancel(caption, text, parent=win)
            elif box_type == MessageBoxType.MB_RETRYCANCEL:
                result = messagebox.askretrycancel(caption, text, parent=win)
            elif box_type == MessageBoxType.MB_YESNO:
                result = messagebox.askyesno(caption, text, parent=win)
            elif box_type == MessageBoxType.MB_YESNOCANCEL:
                result = messagebox.askyesnocancel(caption, text, parent=win)
            elif box_type == MessageBoxType.MB_ERROR:
                result = messagebox.showerror(caption, text, parent=win)
            elif box_type == MessageBoxType.MB_WARNING:
                result = messagebox.showwarning(caption, text, parent=win)
            else:
                result = None

            win.destroy()
            return result

        self._msg_queue.put(_show_message_box)
        return self._wait_for_response()

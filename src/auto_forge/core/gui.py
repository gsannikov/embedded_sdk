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

import queue
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Optional, Any

# AutoGorge local imports
from auto_forge import (CoreModuleInterface, Registry, AutoForgeModuleType, MessageBoxType,
                        InputBoxButtonType, InputBoxLineType, ToolBox, AutoLogger)

AUTO_FORGE_MODULE_NAME = "GUI"
AUTO_FORGE_MODULE_DESCRIPTION = "Set of several GUI notification routines"


class CoreGUI(CoreModuleInterface):
    def __init__(self, *args, **kwargs):

        self._alive = True
        self._gui_thread = None  # Optional joinable thread
        super().__init__(*args, **kwargs)

    def _initialize(self, **kwargs: Any):
        """
        Initializes the GUI system. Spawns a thread to run user logic,
        and keeps the GUI running in the main thread.
        """
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("CoreGUI must be initialized from the main thread!")

        self._root = tk.Tk()
        self._root.withdraw()
        self._msg_queue = queue.Queue()
        self._response_queue = queue.Queue()
        self._logger = AutoLogger().get_logger(name=AUTO_FORGE_MODULE_NAME)
        self._toolbox = ToolBox.get_instance()

        Registry.get_instance().register_module(
            name=AUTO_FORGE_MODULE_NAME,
            description=AUTO_FORGE_MODULE_DESCRIPTION,
            auto_forge_module_type=AutoForgeModuleType.CORE)

        self._root.after(100, lambda: self._process_queue())  # type: ignore

        # Optional: run user code from a background thread
        entry_point = kwargs.get("entry_point")
        if callable(entry_point):
            start_acknowledge = threading.Event()
            self._gui_thread = threading.Thread(target=entry_point, daemon=True,
                                                name="AutoForgeEventSync", args=(start_acknowledge,))
            self._gui_thread.start()
            # Wait for the thread to signal back that it has started
            if not start_acknowledge.wait(timeout=5):
                raise RuntimeError("timeout waiting for entry point thread to start")

    def _process_queue(self):
        """
        Internal method that processes GUI function requests from the message queue.
        Repeatedly scheduled via `after()` to run on the Tkinter event loop.
        """
        try:
            # Mark the singleton instance as ready (used by wait_until_ready() callers)
            self.mark_ready()
            while True:
                func = self._msg_queue.get_nowait()
                result = func()
                self._response_queue.put(result)
        except queue.Empty:
            pass
        self._root.after(100, lambda: self._process_queue())  # type: ignore

    def shutdown(self):
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
        self.shutdown()

    def message_box(self, text: str, caption: str, box_type: MessageBoxType,
                    _centered: bool = True, top_most: bool = True) -> Optional[str]:
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
        return self._response_queue.get()

    def input_box(self, caption: str,
                  button_type: InputBoxButtonType,
                  lines: list[InputBoxLineType],
                  centered: bool = True,
                  top_most: bool = True) -> dict[str, str]:
        """
        Displays a customizable input dialog with one or more labeled fields.

        Args:
            caption (str): The title of the dialog window.
            button_type (InputBoxButtonType): The button layout (e.g., OK/Cancel).
            lines (list[InputBoxLineType]): A list of labeled input lines to show.
            centered (bool): Whether to center the dialog on the screen.
            top_most (bool): Whether to display the dialog as a topmost window.

        Returns:
            dict[str, str]: A dictionary mapping line labels to the user's responses.
                            Returns an empty dict if the user cancels the dialog.
        """
        pass

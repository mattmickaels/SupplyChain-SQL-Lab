"""Graphical launcher for the self-contained desktop distribution."""

import logging
import os
import queue
import socket
import sys
import threading
import tkinter as tk
import webbrowser
from logging.handlers import RotatingFileHandler
from tkinter import messagebox, ttk

from sclab.paths import data_dir

TITLE = "SupplyChain SQL Lab"
LOG = logging.getLogger(__name__)


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    """An OS-owned lock, released even when the launcher crashes."""

    def __init__(self, path):
        self.file = path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise AlreadyRunning from exc

    def close(self):
        self.file.close()


class StartupCancelled(Exception):
    pass


class LabService:
    """Own the server and its socket; keep slow work off the GUI thread."""

    def __init__(self):
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.server = None
        self.url = None
        self.thread = None

    @property
    def ready(self):
        return (
            self.alive
            and self.server is not None
            and self.server.started
            and not self.cancelled.is_set()
        )

    @property
    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="lab-server")
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        if self.server is not None:
            self.server.should_exit = True

    def _progress(self, message, fraction):
        if self.cancelled.is_set():
            raise StartupCancelled
        self.events.put((message, fraction))

    def _run(self):
        try:
            # Import after the window appears, so first startup never looks frozen.
            import uvicorn

            from sclab.data.build import ensure_database
            from sclab.web.app import create_app

            self._progress("Preparing your learning database...", 0)
            ensure_database(progress=self._progress)
            self._progress("Opening the lab...", 1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
                self.server = uvicorn.Server(
                    uvicorn.Config(
                        create_app(),
                        log_config=None,
                        access_log=False,
                        loop="asyncio",
                        http="h11",
                        ws="none",
                        timeout_graceful_shutdown=10,
                    )
                )
                if not self.cancelled.is_set():
                    self.server.run(sockets=[listener])
                    if not self.cancelled.is_set():
                        raise RuntimeError("The lab server stopped unexpectedly")
        except StartupCancelled:
            pass
        except BaseException:
            LOG.exception("The lab could not run")
            self.events.put(("error", "The lab could not start. See the log for details."))
        finally:
            self.events.put(("stopped", 0))


class Launcher:
    def __init__(self, root, *, open_browser=True):
        self.root = root
        self.auto_open = open_browser
        self.closing = False
        self.opened = False
        self.failed = False
        self.service = LabService()
        root.title(TITLE)
        root.geometry("580x420")
        root.minsize(580, 420)
        root.configure(bg="#f3f6fb")
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f6fb")
        style.configure("TLabel", background="#f3f6fb", foreground="#21334d", font=("Arial", 11))
        style.configure("Title.TLabel", font=("Arial", 24, "bold"))
        style.configure("Small.TLabel", font=("Arial", 10), foreground="#52647d")
        style.configure("TButton", font=("Arial", 11), padding=(16, 10))
        style.configure("TProgressbar", background="#187d77", troughcolor="#dfe7f1")
        frame = ttk.Frame(root, padding=28)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="KESTREL PROCESS EQUIPMENT", style="Small.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Your SQL learning lab", style="Title.TLabel").pack(
            anchor="w", pady=(10, 8)
        )
        ttk.Label(frame, text="Explore the data. Learn by doing.").pack(anchor="w")
        self.status = tk.StringVar(value="Starting your lab...")
        ttk.Label(frame, textvariable=self.status, wraplength=500).pack(anchor="w", pady=(28, 12))
        self.progress = ttk.Progressbar(frame, maximum=1)
        self.progress.pack(fill="x")
        self.address = tk.StringVar(
            value="First launch builds your sample data. This may take a few minutes."
        )
        ttk.Label(frame, textvariable=self.address, style="Small.TLabel", wraplength=500).pack(
            anchor="w", pady=12
        )
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        self.open_button = ttk.Button(
            buttons, text="Open lab", command=self.open_lab, state="disabled"
        )
        self.open_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop lab", command=self.close)
        self.stop_button.pack(side="right")
        ttk.Label(
            frame,
            text=(
                "Keep this window open while learning.\nYour saved progress stays on this computer."
            ),
            style="Small.TLabel",
            wraplength=500,
        ).pack(anchor="w", pady=(18, 0))
        self.service.start()
        root.after(100, self.poll)

    def open_lab(self):
        if self.failed:
            webbrowser.open((data_dir() / "launcher.log").as_uri())
        elif self.service.ready:
            if not webbrowser.open(self.service.url):
                self.status.set("Open the address below in your browser.")

    def close(self):
        self.closing = True
        self.status.set("Stopping safely... Please wait.")
        self.open_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.service.stop()

    def poll(self):
        while True:
            try:
                message, value = self.service.events.get_nowait()
            except queue.Empty:
                break
            if message == "error":
                self.failed = True
                self.status.set(value)
                self.address.set(str(data_dir() / "launcher.log"))
                self.open_button.configure(text="Open log", state="normal")
                self.stop_button.configure(text="Close")
            elif message != "stopped" and not self.closing:
                self.status.set(message)
                self.progress["value"] = value
        if self.service.ready and not self.opened and not self.closing:
            self.opened = True
            self.status.set("Ready to learn")
            self.address.set(self.service.url)
            self.progress["value"] = 1
            self.open_button.configure(state="normal")
            if self.auto_open:
                self.open_lab()
        if self.closing and not self.service.alive:
            self.root.destroy()
            return
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    root.withdraw()
    lock = None
    launcher = None
    try:
        home = data_dir()
        try:
            lock = InstanceLock(home / "launcher.lock")
        except AlreadyRunning:
            messagebox.showinfo(
                TITLE,
                "The lab launcher is already open.\n"
                "Use its Open lab button to return to your lesson.",
            )
            root.destroy()
            return
        handler = RotatingFileHandler(
            home / "launcher.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        # Windowed bundles have no stdout/stderr. Third-party libraries may write to them.
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        launcher = Launcher(root, open_browser="--smoke-test" not in sys.argv)
        if "--smoke-test" in sys.argv:
            from sclab.desktop_smoke import attach_smoke_test

            attach_smoke_test(launcher, sys.argv[sys.argv.index("--smoke-test") + 1])
        root.deiconify()
        root.mainloop()
    except Exception as exc:
        LOG.exception("Launcher failure")
        messagebox.showerror(TITLE, f"The launcher could not start.\n\n{exc}")
        root.destroy()
    finally:
        if launcher is not None:
            launcher.service.stop()
            if launcher.service.thread is not None:
                launcher.service.thread.join()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    main()

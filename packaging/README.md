# Desktop distribution

Learners receive an installer containing Python, Tcl/Tk, application packages, lessons, templates, and vendored browser assets.
The launcher runs the existing FastAPI app on an OS-assigned loopback port and opens the default browser.
It owns the server for its entire lifetime and stops it when the launcher closes.
Generation and serving run outside the GUI thread, with cancellation checked between generation stages.
A process-owned file lock prevents two desktop launchers from sharing the same data directory.
The terminal launcher should be stopped before opening the desktop edition against the same data directory.

## Build

Build on the target OS with Python 3.12 including Tcl/Tk.
The GitHub **Desktop installers** workflow uses python.org builds through `actions/setup-python` and builds Windows x64, macOS Apple Silicon, and macOS Intel separately.
Windows x64 builds also run through Windows 11's x64 emulation on ARM devices.
The workflow can be run manually in GitHub Actions or triggered by a version tag.
It uploads installer artifacts; it does not publish a release automatically.

```text
uv sync --locked --group packaging
uv run --no-sync python -m PyInstaller --noconfirm packaging/sclab.spec
```

The [PyInstaller spec](https://pyinstaller.org/en/stable/spec-files.html) collects the lab's non-Python resources and builds a windowed executable without a terminal.
On Windows, compile `packaging/windows.iss` with Inno Setup 6, passing `/DAppVersion=<project version>`.
The installer goes in `dist/installers`, creates desktop and Start menu shortcuts, and installs under the current user's local Programs folder.
It uses [Inno Setup's non-administrator installation mode](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm).
Uninstall removes installed program files and shortcuts but deliberately preserves the user's lab data.
On macOS, the workflow wraps the `.app` in a disk image with an Applications shortcut.

## Verify before distributing

```text
uv run --no-sync python packaging/smoke_test.py "dist/SupplyChain SQL Lab/SupplyChain SQL Lab.exe"
```

On macOS, substitute `dist/SupplyChain SQL Lab.app/Contents/MacOS/SupplyChain SQL Lab`.
The smoke check uses a fresh temporary `SCLAB_HOME`, starts the actual graphical executable, waits for database generation, loads all ten lessons and static assets, executes SQL over HTTP, and closes the launcher.
It checks that the server socket is closed, then repeats the launch against existing data.
Run the installed executable through the same check to validate installer output as well as the build folder.
Also inspect the launcher visually and verify install, shortcut launch, and uninstall on a clean target machine without Python.
The smoke check is automated evidence, not a substitute for that clean-machine acceptance check.

## Signing and distribution

Builds are unsigned unless signing is added separately.
Windows reputation checks and macOS Gatekeeper can therefore warn or prevent launch.
Signing credentials are not stored in this repository.
For wider distribution, sign the Windows executable and installer, and sign and notarize the macOS app before creating its disk image.
Publish only artifacts whose platform build and smoke checks have passed, and include SHA-256 checksums with the release.

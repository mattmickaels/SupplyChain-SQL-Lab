from pathlib import Path
import sys
import tomllib

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text())['project']['version']
a = Analysis(
    [str(root / "packaging" / "desktop_entry.py")],
    pathex=[str(root / "src")],
    datas=collect_data_files("sclab"),
    hiddenimports=["uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"],
    excludes=["pytest", "ruff"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SupplyChain SQL Lab",
          console=False, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SupplyChain SQL Lab")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="SupplyChain SQL Lab.app", bundle_identifier="com.mattmickaels.sclab",
                 info_plist={"CFBundleShortVersionString": version, "NSHighResolutionCapable": True})

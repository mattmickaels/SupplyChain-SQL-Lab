"""Write SHA-256 checksums beside the platform's installer artifacts."""

import hashlib
from pathlib import Path

folder = Path("dist/installers")
lines = []
for artifact in sorted(folder.iterdir()):
    if artifact.suffix.lower() not in {".exe", ".dmg"}:
        continue
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    lines.append(f"{digest}  {artifact.name}\n")
if not lines:
    raise RuntimeError("No installers found to checksum")
(folder / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")

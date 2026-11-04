"""Generate a requirements.lock from a requirements.txt using pip's resolver.

Uses `pip install --dry-run --report` so the lock reflects what pip would
actually install, including transitive dependencies, rather than a pip freeze
of the whole conda environment (which includes packages the project never
declares).

Filters out platform-specific packages (CUDA/nvidia) to ensure the lock
is reproducible across platforms (GitHub Actions runners don't have CUDA).
"""
import json
import subprocess
import sys
from pathlib import Path

# Platform-specific packages to exclude from lock file
# These are installed by the platform (conda/CUDA) not by pip
PLATFORM_SPECIFIC_PREFIXES = (
    "cuda-",
    "nvidia-",
    "triton",
)

def is_platform_specific(name: str) -> bool:
    """Check if a package is platform-specific (CUDA/nvidia)."""
    name_lower = name.lower()
    return any(name_lower.startswith(prefix) for prefix in PLATFORM_SPECIFIC_PREFIXES)

req = Path(sys.argv[1])
out = Path(sys.argv[2])
report = Path("/tmp/_lock_report.json")

cmd = [
    sys.executable, "-m", "pip", "install",
    "--dry-run", "--quiet", "--ignore-installed", "--report", str(report),
    "-r", str(req),
]
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(f"RESOLVE FAILED for {req}:")
    print(res.stderr[-2000:])
    sys.exit(1)

data = json.loads(report.read_text())
pkgs = []
for item in data.get("install", []):
    meta = item["metadata"]
    name, ver = meta["name"], meta["version"]
    if name.lower() == "pip":
        continue
    if is_platform_specific(name):
        print(f"  Excluding platform-specific package: {name}=={ver}")
        continue
    pkgs.append((name.lower(), name, ver))

pkgs.sort()
lines = [
    "# Auto-generated lock file. Do not edit by hand.",
    f"# Source: {req.name}",
    "# Regenerate: python tools/gen_lock.py requirements.txt requirements.lock",
    "#",
    "# Pins every direct and transitive dependency to the exact version pip",
    "# resolved. Installing from this file is reproducible; installing from",
    "# requirements.txt is not, because unpinned ranges resolve differently as",
    "# upstream releases.",
    "#",
    "# Platform-specific packages (CUDA/nvidia) are excluded and should be",
    "# installed via conda or the platform's package manager.",
    "",
]
lines += [f"{name}=={ver}" for _, name, ver in pkgs]
out.write_text("\n".join(lines) + "\n")
print(f"{out}: {len(pkgs)} packages (excluded platform-specific)")

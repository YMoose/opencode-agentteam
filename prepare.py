#!/usr/bin/env python3
"""Export a pinned checkout and optionally install its filtered, locked dependencies."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


parser = argparse.ArgumentParser()
parser.add_argument("--repository", type=Path, required=True)
parser.add_argument("--commit", default="3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3")
parser.add_argument("--install", action="store_true")
args = parser.parse_args()
commit = subprocess.check_output(["git", "-C", str(args.repository), "rev-parse", args.commit + "^{commit}"], text=True).strip()
snapshot = Path(tempfile.mkdtemp(prefix="opencode-team-p0-"))
archive = subprocess.Popen(["git", "-C", str(args.repository), "archive", commit], stdout=subprocess.PIPE)
try:
    subprocess.run(["tar", "-x", "-C", str(snapshot)], stdin=archive.stdout, check=True)
finally:
    archive.stdout.close()
assert archive.wait() == 0
(snapshot / "p0-source.json").write_text(json.dumps({"commit": commit}, indent=2) + "\n")
print(snapshot, flush=True)
if args.install:
    subprocess.run([
        "bun", "install", "--filter", "@opencode/core", "--filter", "@opencode/server",
        "--frozen-lockfile", "--ignore-scripts", "--cache-dir", str(snapshot / ".bun-cache"),
    ], cwd=snapshot, stdout=sys.stderr, check=True)

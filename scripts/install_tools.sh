#!/usr/bin/env bash
# Local development: install the scanners from config/tools.yaml and the pinned semgrep rule pack.
# The container image (M4.1) does the same at exact versions with checksum verification; Homebrew
# installs its current version, so this script reports any mismatch with the pinned one.
set -euo pipefail

TOOLS_DIR="${ARCHLENS_TOOLS_DIR:-$HOME/.cache/archlens/tools}"
cd "$(dirname "$0")/.."

missing=()
for tool in gitleaks osv-scanner semgrep hadolint checkov actionlint; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if ((${#missing[@]})); then
  if command -v brew >/dev/null 2>&1; then
    brew install "${missing[@]}"
  else
    echo "Install manually (no Homebrew): ${missing[*]}" >&2
    exit 1
  fi
fi

# semgrep rules are pinned to a local file so scans are reproducible and need no registry access.
mkdir -p "$TOOLS_DIR/semgrep"
rules="$TOOLS_DIR/semgrep/p-default.yml"
curl -fsSL --max-time 120 -o "$rules.tmp" "https://semgrep.dev/c/p/default"
mv "$rules.tmp" "$rules"
shasum -a 256 "$rules" | tee "$rules.sha256"

echo "installed vs pinned (config/tools.yaml):"
uv run python - <<'EOF'
from archlens.config import load_config
from archlens.facts.base import installed_version
import shutil

args = {"gitleaks": ("version",), "actionlint": ("-version",)}
for name, cfg in load_config().tools.tools.items():
    if name == "lizard":
        from archlens.facts.scanners.lizard_ import VERSION as found
    else:
        exe = shutil.which(name)
        found = installed_version(exe, *args.get(name, ("--version",))) if exe else "missing"
    mark = "ok" if found == cfg.version else "MISMATCH"
    print(f"  {name:12} {str(found):10} pinned {cfg.version:10} {mark}")
EOF

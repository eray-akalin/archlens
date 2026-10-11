# Third-party notices

The ArchLens container image (`ghcr.io/eray-akalin/archlens`) redistributes the tools below,
unmodified, at the versions pinned in `config/tools.yaml`. Each is under its own license; the
source code of each version is available at the link given. ArchLens's own license is the one
declared in its repository.

| Tool | Version | License | Source |
|---|---|---|---|
| gitleaks | 8.30.1 | MIT | https://github.com/gitleaks/gitleaks/tree/v8.30.1 |
| osv-scanner | 2.6.0 | Apache-2.0 | https://github.com/google/osv-scanner/tree/v2.6.0 |
| hadolint | 2.15.1 | GPL-3.0 | https://github.com/hadolint/hadolint/tree/v2.15.1 |
| actionlint | 1.7.12 | MIT | https://github.com/rhysd/actionlint/tree/v1.7.12 |
| semgrep (engine) | 1.179.0 | LGPL-2.1 | https://github.com/semgrep/semgrep/tree/v1.179.0 |
| checkov | 3.3.20 | Apache-2.0 | https://github.com/bridgecrewio/checkov/tree/3.3.20 |
| lizard | 1.24.1 | MIT-style (see its LICENSE.txt) | https://github.com/terryyin/lizard |

hadolint is distributed under the GNU General Public License v3.0 and the semgrep engine under
the GNU Lesser General Public License v2.1. The binaries in the image are the upstream release
artifacts, verified by sha256 at build time (`scripts/fetch_scanners.py`). Their complete
corresponding source is at the links above.

Semgrep registry rules are **not** included: the Semgrep Rules License permits internal use only.
The image runs with `ARCHLENS_SEMGREP_REGISTRY_RULES=false`, so semgrep does not run in it (see
`docs/AZURE.md` §4).

The Python packages in `/app/.venv` (resolved from `uv.lock`) and the semgrep and checkov
virtualenvs carry their license files in their `*.dist-info` directories. The base image is the
official `python:3.12-slim-trixie` image (Debian; Python Software Foundation License).

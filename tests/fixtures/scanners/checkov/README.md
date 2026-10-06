# checkov fixture

- Tool version: 3.3.20
- Captured: 2026-10-06 by `scripts/capture_scanner_fixtures.py`
- Input: `tests/fixtures/scanners/checkov/input/`
- Command (`<repo>`, `<work>`, `<tools>` are placeholders):

  ```
  checkov -d <repo> --framework kubernetes terraform cloudformation bicep arm --skip-download --skip-results-upload --soft-fail -o json --quiet --compact
  ```

Output is redacted and the input root is replaced by `__ROOT__`.

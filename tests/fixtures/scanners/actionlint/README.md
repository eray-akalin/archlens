# actionlint fixture

- Tool version: 1.7.12
- Captured: 2026-10-06 by `scripts/capture_scanner_fixtures.py`
- Input: `tests/fixtures/scanners/actionlint/input/`
- Command (`<repo>`, `<work>`, `<tools>` are placeholders):

  ```
  actionlint -config-file <work>/actionlint.yaml -no-color -shellcheck= -pyflakes= -format {{json .}} <repo>/.github/workflows/broken.yml
  ```

Output is redacted and the input root is replaced by `__ROOT__`.

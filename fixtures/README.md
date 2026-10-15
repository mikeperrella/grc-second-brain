# fixtures

All content here is synthetic test data. Nothing is from a real company.

- `vault-adversarial/` is a small vault of hostile notes (hidden instructions, a fake frontmatter
  block, links that try to leave the vault, an unquoted frontmatter link). The tests index it and
  assert that the content is treated as plain data (CLAUDE.md 8.6). Characters that are invisible
  in an editor (zero-width, right-to-left override, U+2028) are generated inside the tests instead
  of being committed, so a reviewer can see exactly what the test does.
- `exports/` holds a synthetic Okta-style MFA enrollment CSV for the reality check (ADR-016); its
  own README says its authenticator type strings are examples, not confirmed Okta values.
- `vault-contradictions/` is a synthetic vault of planted policy contradictions, and
  `eval/contradiction-set.json` labels them (dev and held-out) for the contradiction detector
  (ADR-019). Every note says it is synthetic. The set was frozen in chunk 4c and is pinned by
  `sidecar/tests/test_contradiction_freeze.py`.

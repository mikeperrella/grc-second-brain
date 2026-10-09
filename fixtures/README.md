# fixtures

All content here is synthetic test data. Nothing is from a real company.

- `vault-adversarial/` is a small vault of hostile notes (hidden instructions, a fake frontmatter
  block, links that try to leave the vault, an unquoted frontmatter link). The tests index it and
  assert that the content is treated as plain data (CLAUDE.md 8.6). Characters that are invisible
  in an editor (zero-width, right-to-left override, U+2028) are generated inside the tests instead
  of being committed, so a reviewer can see exactly what the test does.

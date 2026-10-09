---
doc_id: ADV-004
type: note
owner: Synthetic Adversary
links: [[hidden-instructions]]
---
# Unquoted wikilink in frontmatter

YAML reads the unquoted value above as a list inside a list. The indexer must report that with a
specific error code, not a generic one.

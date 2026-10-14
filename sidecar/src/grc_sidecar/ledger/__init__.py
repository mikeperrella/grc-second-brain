"""The promise ledger's logic (ADR-017): the reviewed-workbook import and the ledger listings.

Storage is in `repositories/` (the append-only SQLite store); the HTTP layer is `api/ledger.py`.
Nothing here calls a model, the network or a process, and nothing here logs content.
"""

# ADR-008: Framework data packs

Status: Accepted (M3a decides the format; the content arrives in M3d to M3f)

## Context

ALIBI maps a company's own policies to frameworks (SOC 2, NIST 800-53, ISO 27001). To do that it needs framework content on the machine: control IDs, titles and, where we may redistribute it, the control text. That content is not the user's vault. It must be read-only, it must not crowd out the company's own policies in retrieval, and it must carry its provenance and licence so we never ship text we are not allowed to.

The app never downloads anything (CLAUDE.md 3.2), so the content is prepared by a human at development time and ships inside the installer.

## Decision

**Format.** A pack is one directory, `packs/<pack_id>/`, holding Markdown files and one `pack.json`. The Markdown files carry ordinary frontmatter (`type: framework`), so the same parser reads them as vault files. `pack.json` is validated by `PackMetadata` (`grc_sidecar/packs/models.py`): every field is required, there are no defaults, unknown fields are rejected, and the fields are tied together by licensing rules:

| Rule | Why |
|---|---|
| `redistributable = false` allows only `content_kind = ids_and_original_summaries` | Licensed text is never bundled. |
| `origin = converted_public_domain` means `content_kind = full_text` | Converted public-domain sources are complete. |
| `origin = original_authored` means `content_kind = ids_and_original_summaries` | Our own work is IDs plus our own summaries, never copied text. |
| `source_sha256`, `content_sha256` are 64 lowercase hex | Provenance and tamper detection. |
| `id_list_verified_against` is required free text; `"unverified"` is legal | Records what an ID list was actually checked against. Never names a source nobody saw. |

JSON is used for the metadata so none of the YAML pitfalls in ADR-009 apply to it. The loader (M3d) recomputes `content_sha256` and refuses a pack whose files no longer match. That detects accidental or careless edits. It is an integrity check, not authenticity: someone who can rewrite both the files and `pack.json` is not stopped, and signing is out of scope.

**Location and separation.** Packs live at repo-root `packs/` and ship read-only. They are indexed into their own database file (`packs.sqlite3`), separate from the vault index, with a `collection` label (`pack:<id>`) on every row. Retrieval (M4) queries the two stores separately and caps the share of framework text in the model's context, so a large pack such as 800-53 cannot push the company's own policies out.

**Acquisition.** The app never fetches content. A human downloads the public-domain NIST OSCAL files (CSF 2.0, SP 800-171 Rev 3, SP 800-53 Rev 5) from NIST's `usnistgov/oscal-content` repository at a pinned commit, records the SHA-256, and runs a development-only converter (`tools/oscal_to_markdown.py`, standard library only, reads one local file, no network). Raw source files are not committed: the converted Markdown, `pack.json`, the source hash and the commit-pinned URL are, which makes the result reproducible without adding the 10 MB raw 800-53 catalog to the repository.

**SOC 2 and ISO 27001.** Their text is copyrighted. Those packs hold control IDs for every control, and our own titles and one or two sentence summaries only for the controls the sample vault maps to. Users who want more import their own licensed material later.

**Licence of the NIST content (recorded in M3d).** The `usnistgov/oscal-content` LICENSE.md at commit `78650f02ad9321bb7b817846f8fbd4f2bcd620de` says: "As a work of the United States government, this project is in the public domain within the United States." It also states a worldwide copyright waiver under CC0 1.0 ("we waive copyright and related rights in the work worldwide"). `pack.json` records `license = "US-Gov-PD (17 USC 105); CC0-1.0 (usnistgov/oscal-content LICENSE.md)"`. Note: the text was read through a web-fetch tool that returned short quotes, not a raw file download, so the project owner should open LICENSE.md once and confirm these two sentences.

**Loader behaviour (M3d).** `grc_sidecar/packs/loader.py` verifies every pack before anything is indexed, and one failing pack stops all of them (fail closed). A pack folder may hold only `pack.json` and top-level `*.md` files, the packs folder may hold only pack directories, and symlinks are refused, so the set of files that is hashed is exactly the set that gets indexed. The loader takes any `EmbeddingClient`; the deterministic fake lives only in `tests/`. The pack database is `packs.sqlite3` inside `index_dir`. It is built at runtime, is covered by the `*.sqlite3` rule in `.gitignore`, and is never committed or shipped.

**Withdrawn controls (decision).** NIST catalogs include withdrawn controls (removed, merged into others, or moved). They are kept so old references still resolve, but they must never read like current requirements. The converter therefore writes active controls normally and puts every withdrawn control in a final `## Withdrawn controls` section of its file as one line: the ID, the word "Withdrawn", and the "Incorporated into", "Moved to" or "Addressed by" note when the source has one. A stub carries no requirement text. Counts are checked separately (active and withdrawn) against the source, both inside the converter and in `test_pack_loader.py`.

| Source (OSCAL, pinned commit) | Total | Active | Withdrawn |
|---|---|---|---|
| NIST CSF 2.0 (categories and subcategories) | 219 | 128 | 91 |
| NIST SP 800-171 Rev 3 | 130 | 97 | 33 |
| NIST SP 800-53 Rev 5 (controls and enhancements) | 1196 | 1014 | 182 |

Measured size of the converted packs (M3f, bytes on disk including `pack.json`): CSF 2.0 78,062; SP 800-171 Rev 3 134,798; SP 800-53 Rev 5 1,007,239 (20 files, one per control family). Total 1,220,099 bytes, about 1.2 MB, against a 15 MB limit; the raw 10.4 MB 800-53 catalog is not committed.

What the converter leaves out: the assessment objectives and assessment methods (SP 800-53 and 800-171), because they are large and not needed for mapping policies to controls. Markdown link URLs in the prose are dropped (the sources carry app-internal paths); the link text stays. Whitespace in prose is collapsed to single spaces so source text can never begin a heading or a frontmatter fence.

## Consequences

- A half-filled or hand-edited `pack.json` fails loudly, naming the bad fields and never their values.
- Mistakes in licensing policy are caught by validation, not by memory.
- Rev 3 of SP 800-171 is the first target. CMMC Level 2 assessments may reference Rev 2 (stated by the project owner, not independently verified); a Rev 2 pack is a later item to verify before anyone relies on it.
- The converter and the pack content arrive in later chunks (M3d CSF and 800-171, M3e SOC 2 and ISO IDs, M3f 800-53). M3a only fixes the format and the metadata model.
- Before any converted NIST content is committed, the licence stated in the `oscal-content` repository is read and recorded in `pack.json`.

# ADR-023: Graph view

Status: **Proposed (M6.5 chunk 65a); nothing in it is built.** Written 2026-10-18 from `M6_5_PLAN.md`. The route, the list view and any canvas described here do not exist yet. Everything is for a **debug build only until M8**. All caps are unmeasured starting values. **The React Flow decision is left OPEN.**

## Context

CLAUDE.md Section 9 lists a "React Flow graph view with its sidecar route and dependency" for M6.5, and CLAUDE.md 8.2 says graph edges come only from explicit `[[wikilinks]]` and frontmatter `links`, never from semantic similarity. The product brief does not describe a graph view. The compliance lead the brief describes (a team of one answering customer questionnaires) has one concrete question a map can help with: **before I edit this policy, what links to it and how many promises rest on it?** The blast-radius preview answers the second half for promises only (ADR-018, G36); backlinks and controls are not built there. A graph view is navigation, not a verdict.

The index has a `links` table with each link's `status` (`resolved`, `unresolved`, `ambiguous`, `rejected`) and `origin` (`body`, `frontmatter`) (code: `sidecar/src/grc_sidecar/repositories/sqlite_vec_repo.py::_SCHEMA`; `vault/models.py::ResolvedLink`). The index can be stale; recall reads the vault for that reason (ADR-017 decision 19). CLAUDE.md Section 4's stack table names NetworkX, but **no sidecar file mentions it** (`git grep -i networkx -- sidecar` finds nothing; it appears only in CLAUDE.md), so no graph library is pinned or imported.

## Decision (proposed)

**1. A read-only route, `GET /vault/graph`, that scans the vault, not the index.** It uses the existing vault scanner, parser and link resolution, as the `ControlMap` does (ADR-016 decision 2). *Reason:* a map built from a stale index could show a link that no longer exists, which is the failure mode recall avoids. *Rejected:* the index's link table (can be stale); a new index table (the index is disposable derived state, CLAUDE.md 8.1).

**2. Edges are resolved explicit links only.** Nodes are company vault documents `{index, path, doc_id, type, live_promises}`; edges are `{from, to, origin: body|frontmatter, count}`. Counts of unresolved, ambiguous and rejected links are returned as integers. A fixed `says` line is returned with every response. *Reason:* CLAUDE.md 8.2; a similarity edge would invent a relationship the author never wrote. *Rejected:* similarity edges, control-mapping edges (a different relation, ADR-016, and not what a link means).

**3. Caps: 500 nodes and 5,000 edges (unmeasured starting values), refused whole above the cap with counts.** `422 graph_refused` with code `graph_too_large` and integer counts, never a partial graph. *Reason:* a partial map that looks complete would be worse than none (the same rule as the ControlMap's `control_map_too_large`, ADR-016 decision 2). **Known limit, recorded as an open question and not a decision:** a vault above 500 documents gets no graph, while the ControlMap accepts 2,000 (ADR-016 decision 2). The owner decides whether to raise the cap, page the graph, or accept the difference. *Rejected:* truncating at the cap.

**4. What it sends to the window.** Vault-relative paths, `doc_id`, `type` and counts. **No document text.** The window shows them as plain text through the existing `PlainText` component. *Reason:* the window needs names and counts to navigate; the text is in the vault.

**5. `live_promises` and the ledger.** When a ledger folder is in use, each node carries the count of live promises that cite its path. **When no ledger folder is in use, `live_promises` is null and the window shows "not available (no ledger folder in use)", never 0.** *Reason:* "0 promises" would read as "nothing rests on this policy", the same false assurance the blast-radius screen warns about (F32-2). *Rejected:* a zero default.

**6. Relation to G36.** The graph shows backlinks as navigation. Blast radius still covers promises only; controls and backlinks are still not part of its result, and G36 stays open. The graph screen states: "This map is not evidence that a control works or that a promise is true." and "Lines are the explicit links written in the files. A missing line does not mean two documents are unrelated." (drafts for the owner's approval).

**7. List view first (chunk 65k).** The first graph screen is an accessible list per document (links out, links in, live promise count, counts of broken links), with a deterministic selected-document layout available as a pure function. It is fully keyboard and screen-reader accessible and testable as static markup. *Reason:* it delivers the question above without any new dependency or outside link.

**8. React Flow: OPEN.** The owner decides after seeing the list view (65k). Evidence (read from the published tarballs, nothing installed):

- `@xyflow/react` exact pin **12.12.0** (published 2026-09-24), license MIT; direct dependencies `@xyflow/system` 0.0.83 (MIT), `zustand` ^4.4.0 (MIT), `classcat` ^5.0.3 (MIT); transitive d3-drag, d3-zoom, d3-selection, d3-interpolate, d3-dispatch, d3-transition, d3-color, d3-timer (ISC), d3-ease (BSD-3-Clause), `use-sync-external-store` (MIT) and `@types/d3-*` (MIT).
- No `preinstall`, `install` or `postinstall` in the packages whose `package.json` was read (`@xyflow/react`, `@xyflow/system`, d3-zoom, d3-drag, d3-selection, d3-transition, zustand, classcat); `classcat` has `prepare`. The other d3 packages and `use-sync-external-store` were read by npm view only (**not verified**).
- No `fetch`, `XMLHttpRequest`, `sendBeacon` or `WebSocket` in the React Flow and system bundles or the extracted d3, zustand and classcat sources; development warnings are guarded by `NODE_ENV === 'development'`.
- CSP: no `createElement('style')`, `insertRule`, `adoptedStyleSheets` or `setAttribute('style')` in either bundle; d3 sets styles through `this.style.setProperty` (the CSSOM, not blocked by `style-src 'self'`); the shipped CSS has no `url()`, `@import` or `@font-face`.
- **Attribution:** it renders `<a href="https://reactflow.dev?utm_source=attribution" target="_blank">` by default; `proOptions={{ hideAttribution: true }}` removes it; the authors ask: "Please only hide this attribution when you are subscribed to React Flow Pro".
- It does not lay out nodes (each node needs a `position`); a layout of our own code would be needed, not a second dependency.
- It uses `ResizeObserver` for node and pane sizes; how it renders under `renderToStaticMarkup` is **not verified**, so canvas behaviour would be hand-checked.
- The host registers no `on_navigation` (`git grep on_navigation -- apps/desktop/src-tauri` finds nothing); other navigation guards were not searched for. What a click on the attribution link does in the bundled debug build is **not verified**.

**Owner rule: no clickable outside link may ship unguarded.** If React Flow is adopted, either the attribution is hidden after the owner has read the React Flow Pro terms, or it is kept and a Rust navigation guard with its own test ships first. The attribution is never left as an unguarded link. Adopting it also needs CLAUDE.md Section 2.6's request (name, pinned version, purpose, license, network and telemetry behaviour), the hand checks listed in `M6_5_PLAN.md` section 9, and an update to CLAUDE.md Section 4.

**What the graph view loses without React Flow:** pan, zoom, drag and a minimap, and a large vault is read through a selected-document view instead of one canvas. It gains keyboard and screen-reader access, static-markup tests, no new dependency and no outside link.

## Consequences

- One new sidecar route and one new proxy variant (`VaultGraph`; ADR-020 addendum), no new host command and no capability change.
- Threat-model flow F42 and rows F42-1 to F42-4 are planned ("No mitigation (planned, M6.5 chunk 65c / 65k)").
- No Python dependency is added; networkx stays unused. Whether CLAUDE.md Section 4's NetworkX row should change is an owner decision, held for a report first.
- Starting values (500 nodes, 5,000 edges, the 4 MiB response cap, the 60 s timeout) are unmeasured.

## Mutation proofs (M6.5)

None yet: nothing is built. Planned: an edge added from text similarity (a test that a note that only resembles another gets no edge must fail); the node cap removed; a link target outside the vault followed; a `live_promises` of 0 returned with no ledger folder.

Revisit if: the owner approves React Flow (a new dependency request and, possibly, a navigation guard); the vault regularly exceeds 500 documents; or blast radius gains backlinks and controls (G36), at which point the graph and blast radius should share a source.

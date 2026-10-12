# CLAUDE.md: GRC Second Brain

Read this entire file before any task. Rules marked **MUST** are non-negotiable. If a task conflicts with a MUST rule, stop and ask instead of working around it.

---

## 1. What this project is

The product is named **ALIBI** (Answers Linked to Immutable, Backed Information), formerly GRC Second Brain. The problem, audience, promise, features and non-goals are in `docs/product-brief.md`.

A local-first, air-gapped desktop GRC (Governance, Risk, and Compliance) application. It uses local LLMs through Ollama to:

- auto-fill security questionnaires (SIG, CAIQ, custom Excel),
- map content to compliance frameworks (SOC 2, NIST 800-53, ISO 27001),
- propose infrastructure remediations (Terraform/Docker) as reviewable drafts,

grounded entirely in a local, Obsidian-compatible Markdown knowledge vault.

The architecture must stay modular (clean interfaces, repository pattern, typed boundaries) so the codebase can later evolve into a commercial product under BizAI Solutions.

The owner (Mike) must be able to explain every design decision and every line of code. Optimize for clarity and correctness over cleverness.

---

## 2. Working agreement (how to collaborate)

1. **Plan before code.** For any non-trivial task, produce a plan first: files to create/modify, public interfaces, test cases, security considerations, and open questions. Wait for approval before writing code.
2. **One chunk at a time.** Work only on the current milestone chunk (see Section 9). Do not start the next chunk until Mike reports the verification results.
3. **Explain the why.** For every architectural choice, give a 2 to 4 sentence rationale Mike could repeat in an interview. Record significant decisions as ADRs in `docs/adr/NNN-title.md` (Context, Decision, Consequences).
4. **Be literal about steps.** When Mike must run something, give the exact command, the directory to run it from, and what success output looks like. Explain jargon inline.
5. **Never claim unverified results.** Do not say tests or linters pass unless you ran them and saw the output. If you could not run something, say so.
6. **Dependencies require approval.** Before adding any package, state its name, pinned version, purpose, license, and whether it has any network, telemetry, or auto-update behavior.
7. **No fabricated content.** Never invent metrics, benchmark numbers, or claims for the README or docs. All sample data is synthetic and labeled as such.
8. **End every chunk with the verification commands** from Section 6 for the areas touched.
9. **Change files only with the edit tool**, never sed, heredocs or stdin scripts, so every change is reviewable as a diff. Exception: the project formatters (ruff format, cargo fmt) may rewrite files in place; show the resulting git diff before committing.

---

## 3. Air-gap protocol (MUST)

**Scope:** these rules govern the shipped application at runtime. Development tools (pnpm, cargo, pip, Claude Code) use the network to install packages; that is expected. The product itself never does.

1. **No cloud AI or SaaS SDKs.** The only LLM endpoint is `http://127.0.0.1:11434` (Ollama). Never import or call OpenAI, Anthropic, Google, AWS, Pinecone, LangSmith, or any hosted service.
2. **No external network.**
   - Python: every HTTP client is created by the single factory in `grc_sidecar/security/net.py`, which sets `trust_env=False` and rejects any non-loopback `base_url`. No other module constructs `httpx` clients (enforced by Semgrep).
   - Rust: every `reqwest` client uses `.no_proxy()` and a loopback URL.
   - No telemetry, analytics, crash reporters, remote feature flags, or update checkers. Never add the Tauri updater plugin.
   - Frontend: no CDNs or remote fonts. Bundle all assets. Content Security Policy: `default-src 'self'`; `connect-src` limited to `ipc:` and `http://ipc.localhost`.
   - `pnpm tauri dev` serves the UI from the Vite dev server, where Tauri injects no CSP; the shipped `csp` in `tauri.conf.json` is verified in a debug build of the bundled app.
   - Any library with optional telemetry (e.g. ChromaDB) must have it explicitly disabled in code and covered by a test.
3. **Loopback only.** Uvicorn binds `host="127.0.0.1"`, `port=0` (OS-assigned). Never `0.0.0.0` or `::`.
4. **Zero-trust IPC.**
   - Rust generates a 32-byte token from a CSPRNG at startup, hex-encodes it, and passes it to the sidecar as env var `GRC_AUTH_TOKEN`.
   - The sidecar reads the token once at startup, then deletes it from `os.environ`.
   - Every HTTP request (including `/health`) must carry header `X-GRC-Auth`, verified with `hmac.compare_digest`. Failure returns `401` with no detail.
   - Port handshake: after binding, the sidecar prints exactly one JSON line to stdout, `{"event":"ready","port":<int>}`. Rust reads it with a timeout and kills the sidecar if it never arrives.
5. **No shell execution.** Never use `shell=True`, `os.system`, `eval`, `exec`, or invoke `cmd.exe`, `powershell`, or `bash` from app code. Use `subprocess.run([...], shell=False, timeout=..., env=<explicit minimal env>)`. The child env must NOT contain `GRC_AUTH_TOKEN`. In Rust, use `std::process::Command` with explicit args or the Tauri sidecar API with a scoped capability. The sidecar enforces this at runtime with a process-creation audit hook (ADR-007); M7 extends its allowlist, never removes it.
6. **Safe filesystem.** Every path from the user or the frontend is canonicalized with `Path.resolve(strict=True)` and checked with `candidate.is_relative_to(approved_root)`. Reject anything that escapes, including via `..` or symlinks. Rust performs the same check (`canonicalize` + `starts_with`) before forwarding. Defense in depth: both layers check.
7. **Models are provisioned by a human.** At startup the sidecar calls Ollama `GET /api/tags` (through the restricted client) and records which required models are present. A missing model or an unreachable Ollama does not stop the sidecar from starting: it reports a typed degraded state on an authenticated endpoint, and every model-dependent operation fails closed with a typed error until the models are present. The app never calls `/api/pull`. The sidecar refuses non-local models (a cloud-named model, or one whose listing carries `remote_host` or `remote_model`), because Ollama can forward those to ollama.com (ADR-012).
8. **Egress guard test.** A session-wide pytest fixture patches `socket.socket.connect` to raise on any non-loopback address. The full suite must pass with it enabled.

---

## 4. Tech stack

| Layer | Choice |
|---|---|
| Frontend | React, TypeScript (strict), TailwindCSS, React Flow, Zod |
| Host | Tauri v2, Rust (stable) |
| Sidecar | Python 3.11, FastAPI, Uvicorn, Pydantic v2, packaged with PyInstaller |
| Generation model | `llama3.1:8b` via Ollama (configurable; default pinned) |
| Embedding model | `nomic-embed-text` via Ollama |
| Vector store | `sqlite-vec` behind a `VectorRepository` protocol (see ADR-001) |
| Graph | NetworkX, built only from explicit links |
| Excel | OpenPyXL |
| Knowledge vault | Obsidian-compatible Markdown with YAML frontmatter |

**Resolved spec decisions (do not revert without an ADR):**
- `llama3.2:8b` does not exist (Llama 3.2 text models are 1B and 3B only). Default generation model is `llama3.1:8b`.
- ADR-001: `sqlite-vec` is the default vector store: single file, no server process, no telemetry, packages cleanly with PyInstaller. ChromaDB remains a swappable implementation behind the same interface.
- Answer status is defined in Section 8.4 and ADR-013: it is computed from checks, and the model's confidence can only lower it.

---

## 5. Repository map

```
grc-second-brain/
  CLAUDE.md
  .claude/settings.json        Agent permissions (committed)
  apps/desktop/                Tauri v2 application
    src/                       React + TypeScript frontend
    src-tauri/                 Rust host
  sidecar/                     Python intelligence sidecar
    pyproject.toml
    .venv/                     Local virtualenv (gitignored)
    src/grc_sidecar/
      main.py                  App factory + startup handshake
      config.py                Typed settings (pydantic-settings)
      security/                auth.py, paths.py, net.py
      api/                     FastAPI routers (thin; no business logic)
      llm/                     Ollama client, prompt builders, schemas
      vault/                   Markdown parsing, chunking, indexer
      retrieval/               Query builder, hybrid merge, graph expansion, context budget
      repositories/            VectorRepository protocol + implementations
      questionnaire/           Excel engine
      remediation/             Policy-to-code engine
    tests/
  vault-sample/                Synthetic Obsidian vault (fictional company); pinned by the M4 golden tests
  vault-demo/                  Second synthetic vault for the M5 evaluation set and demo (M5b)
  fixtures/                    Synthetic questionnaires and test files (eval/ holds the labeled eval set)
  docs/
    adr/                       Architecture Decision Records
    threat-model.md
```

---

## 6. Verification commands

Run from the directory shown. On Windows, call the venv Python directly (no activation needed).

**Python** (from `sidecar/`):
```
.venv/Scripts/python -m ruff check . ../tools --config pyproject.toml
.venv/Scripts/python -m ruff format --check . ../tools --config pyproject.toml
.venv/Scripts/python -m mypy --strict src ../tools
.venv/Scripts/python -m pytest
```

**Rust** (from `apps/desktop/src-tauri/`):
```
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
```

**Frontend** (from `apps/desktop/`):
```
pnpm lint
pnpm typecheck
pnpm test
```

**Definition of done for any chunk:** all commands above pass for the touched areas, new behavior has tests (including at least one negative/security test), and Mike has seen the output.

---

## 7. Code standards

**Python**
- Python 3.11+, `mypy --strict` clean, `ruff` clean.
- Pydantic v2 models at every boundary (HTTP, LLM output, file parsing).
- FastAPI dependency injection (`Depends`) for settings, repositories, and clients. Routers stay thin.
- No bare `except`. Define typed domain exceptions and map them to HTTP errors in one place.
- Logs go to a local file only. Never log the auth token, and never log document or questionnaire content above DEBUG.

**TypeScript**
- `strict: true`, `noImplicitAny: true`, `noUncheckedIndexedAccess: true`. Never use `any`.
- Every payload crossing the Tauri IPC boundary is parsed with Zod before use.

**Rust**
- Zero clippy warnings with `-D warnings`.
- `Result` with `thiserror` for recoverable errors. No `unwrap()`/`expect()` on anything derived from user input, paths, or IPC.
- Tauri capabilities follow least privilege: grant only the specific commands and scopes each window needs.

---

## 8. Subsystem contracts

### 8.1 Knowledge vault and indexing
- Markdown files are the single source of truth. The vector index is disposable derived state and can be rebuilt at any time.
- Frontmatter fields: `doc_id`, `type`, `owner`, `review_by` (next review date), `control_mappings` (list), `links` (optional list).
- Chunk ID is deterministic: hash of `(source_path, doc_id, sha256(file), start_line, end_line)`.
- When a source file changes: delete all chunks for that source, re-chunk, re-embed, upsert. Stale vectors are a bug.
- Never convert Markdown into proprietary database objects.

### 8.2 Graph RAG
- Graph edges come only from explicit `[[wikilinks]]` and frontmatter `links`. Never from semantic similarity.
- Retrieval flow: the vault index is checked for files changed since indexing first (a stale file is never returned); keyword (FTS5) and vector search each return ranked chunks, merged by Reciprocal Rank Fusion into seed chunks (framework text is capped so it cannot crowd out company policy; withdrawn controls are left out); graph expansion follows resolved explicit links from the seeds (bounded depth, default 1) into one reserved slot; the combined context is passed to the LLM. If the embedding model is unavailable, retrieval is keyword-only and says so (`mode`).
- Every chunk in context keeps its `source_path` and line range so citations can be verified, and carries a `source_kind`: framework text is never evidence that the company does something.
- A non-empty retrieval result is not evidence: there is no relevance floor, so the questionnaire engine decides "no evidence" itself (the grounding check, 8.4).

### 8.3 Structured output
- Every generation request passes a JSON schema via Ollama's `format` parameter.
- Every request, on every attempt, is exactly one `system` message (instructions only) and one `user` message (the untrusted data). Never a longer conversation: Ollama drops older messages silently when a prompt is too long, and the overflow control (`shift: false`) is complete only for two messages (ADR-012).
- Responses are parsed strictly with Pydantic.
- Bounded retries: one shared budget of at most 2 attempts per question, covering schema and grounding failures together. On attempt 2, rebuild the user message with the exact error text (pydantic's documentation URL removed) inside its data block. After 2 failures, return `NEEDS_REVIEW`. Never loop indefinitely. Error text never reaches a log above DEBUG.

### 8.4 Questionnaire engine
The status contract, with its reasons, is ADR-013 (status and grounding). The file and run rules get their own ADR-014 when they are built (M5i).

- Read the input file once, hash those bytes and parse the same bytes. Load with `openpyxl.load_workbook(..., read_only=False, data_only=False)` (add `keep_vba=True` for a macro-enabled package, decided from its declared content type). Re-hash the file on disk before writing and fail if it changed.
- Never modify original question cells and never insert a row. Write to new appended columns titled `GRC Answer [UNREVIEWED DRAFT]`, `Confidence (model self-rating) [UNREVIEWED DRAFT]`, `Status [UNREVIEWED DRAFT]`, `Reason` and `Evidence` (vault-relative path, exact lines, quote), plus `Review`. Save to a new file whose name carries `UNREVIEWED-DRAFT`, with exclusive create: never overwrite any existing file, not only the input.
- Known limitation: OpenPyXL can drop images and charts on save. Inventory the package parts before and after, warn the user, report exactly what was dropped, and document it.
- The model cites an opaque per-request source ID (`S1` to `S5`), not a path; code maps it back to `source_path` and the line range. LLM output schema: `answer`, `source_id`, `exact_quote`, `model_confidence`, `coverage`, `conflicting_source_ids`, `no_evidence`.
- **Evidence status is computed by the backend. Model output can only lower a status, never raise it.**
  - `SUPPORTED` requires all of: the quote is found verbatim (whitespace normalization only) in the raw text of the chunk the cited ID maps to; that chunk was in the context sent; its `source_kind` is `company_policy`; its review is not overdue (injected `Clock`); the model names no conflict among the context's sources; the quote meets the length floor; the cited file is unchanged when the export is written; `coverage` is `full` and `model_confidence` ≥ 0.90; for an identifier-only question, the cited chunk contains a whole written form of the identifier. Framework text is never evidence of company practice.
  - `PARTIAL`: those checks pass (except the review date and the coverage and confidence conditions) and a limit applies: review overdue, partial coverage, confidence in [0.40, 0.90), keyword-only retrieval, or files withheld because they changed after indexing. The answer is written and the limits are in the `Reason` column.
  - `NEEDS_REVIEW`: anything else, including every "no evidence" result and confidence below 0.40. The answer cell stays blank and the `Reason` column says why. Every reason that applies is recorded. Never write a compliance claim.
- Nothing in M5 is approved. Review state is a separate field from evidence status, always `unreviewed` in M5, so approval can be added later without renaming a status.
- Progress streams as Server-Sent Events from the sidecar (e.g. `Question 37/150 completed`), carrying counts, indexes and status codes, never document or question text. The Rust host proxy comes in M6.

### 8.5 Policy-to-code engine
- Pipeline: Control Requirement, Policy Gap, Evidence, Remediation Objective, structured plan (Ollama), Terraform/Docker patch, static validation, **human approval**.
- Static validation only: `terraform init -backend=false` with a pre-seeded local provider mirror, then `terraform validate`; `docker build --network=none`. All via argument arrays, never a shell.
- Every proposal carries `requires_human_approval: true`. There is no code path to `terraform apply`, `docker push`, or any deployment. Output is a patch file for a human to review.

### 8.6 Prompt-injection defense
- Vault content, vendor documents, and questionnaire text are untrusted data. Wrap them in clearly labeled delimiters and instruct the model to treat them as data only.
- The model has no tools. It can only return schema-validated JSON. All file writes and process execution are performed by backend code acting on validated fields, never on free text.
- Include adversarial test fixtures (instructions hidden in vault notes and questionnaire cells) in the test suite.

---

## 9. Milestones

| ID | Chunk | Done when |
|---|---|---|
| M0 | Repo bootstrap and tooling | Structure exists, Tauri template runs, first commit made |
| M0.5 | Local security scaffolding | Semgrep rules, pre-commit secret scanning, Cursor rule file, Dependabot config committed |
| M1 | Sidecar bootstrap | Typed config, loopback server on port 0, ready handshake, auth middleware, path guard, egress guard test, authenticated `/health` |
| M1.5 | CI pipeline | Semgrep, Bandit, lint/type/test jobs green; sidecar tests pass inside a `--network none` container; network canary proves isolation |
| M2 | Tauri host | Token generation, sidecar spawn, handshake with timeout, typed proxy command, least-privilege capabilities |
| M3 | Vault indexer | Frontmatter parsing, deterministic chunking, `sqlite-vec` repository, change-driven re-index |
| M4 | Retrieval | Hybrid keyword and vector retrieval merged by Reciprocal Rank Fusion, explicit-link graph expansion with citations, typed `source_kind`, framework cap, withdrawn handling, staleness flags |
| M5 | Questionnaire engine | Non-destructive Excel output, grounding check, computed status, authenticated sidecar routes with SSE progress (no host change: the file picker, the Rust path check and the SSE proxy are in M6) |
| M5.5 | Promise ledger and recall, blast-radius preview, contradiction detector, reality check (offline import of IT exports checked against policy claims) | Each has tests and mutation proofs |
| M6 | UI | Progress view, review queue for `NEEDS_REVIEW`, React Flow graph view, plus the host side deferred from M5: the questionnaire and vault folder pickers, the Rust path check (`canonicalize` + `starts_with`, 3.6) and the Rust SSE proxy, with the questionnaire and export roots passed to the sidecar (G9) |
| M7 | Policy-to-code | Structured plan, patch generation, static validation, approval gate |
| M8 | Packaging and docs | PyInstaller sidecar bundled as Tauri external binary, README, generate and bundle a third-party license notices file with the installer, hash-locked Python dependencies (uv or pip-tools), deferred from M1.5, verify the installer contains only the app binary (and the sidecar externalBin), never `fake_sidecar` |
| M9 | Public release | Branch protection with required checks, CodeRabbit enabled, SECURITY.md, history scanned for secrets, decide the license (permissive, none, AGPL, or a private commercial version), review the Semgrep Rules License of the registry rulesets (fine for internal CI, restrictive for redistribution and the BizAI commercial version) |

**Current milestone: M5.**

---

## 10. Multi-tool policy and DevSecOps

**Single source of truth.** This file is the canonical policy. Every other AI tool reads it or mirrors it:

| Tool | How it gets the rules |
|---|---|
| Claude Code | Reads this file directly |
| Cursor | `.cursor/rules/grc-security.mdc` (`alwaysApply: true`) mirrors Sections 3, 7, and 11 |
| CodeRabbit | `knowledge_base.code_guidelines` reads this file and the Cursor rule; `path_instructions` encode Section 3 per directory |
| Claude Desktop Project | This file is uploaded as project knowledge and supersedes older architecture docs |

When a MUST rule changes, update this file first, then the Cursor rule and `.coderabbit.yaml` in the same PR. CI will fail if the Cursor rule drifts (M1.5).

**Threat model.** `docs/threat-model.md` is the canonical threat model. Update it in the same PR as any new data flow, new dependency, or change to a MUST rule in Section 3 or 11. Its backlog (Section 12) names the milestone that owns each residual risk.

**Rules are enforced by machines, not just by prompts.** AI instructions are advisory. The blocking controls are:

| Control | Where | Gate (required to merge from M9) |
|---|---|---|
| Custom air-gap rules | `.semgrep/grc-airgap.yml`, with `semgrep --test` fixtures in `.semgrep/fixtures/` | Yes |
| Python SAST | Bandit on `sidecar/src`, `sidecar/ci`, `.github/scripts` and `tools`. Semgrep and Bandit do not scan tests; ruff `S` rules and gitleaks cover them | Yes |
| Rust / TS SAST | Semgrep registry rulesets, `clippy -D warnings`, ESLint `no-explicit-any` | Yes |
| Secrets | gitleaks pre-commit hook (local), GitHub push protection (server; enable at M9), gitleaks in CI | Yes |
| Dependencies | Dependabot (pip, npm, cargo, docker, github-actions), `cargo deny`, `pip-audit`, `pnpm audit`; also run weekly (`audit.yml`) | Yes for known vulns |
| Air-gap proof | Sidecar tests run in a container with `--network none`, plus a canary that proves loopback works and outbound and DNS fail | Yes |
| AI review | CodeRabbit (configured; enabled at M9) | Advisory |

**CI rules for any workflow you write:** pin every action to a full commit SHA, set `permissions: contents: read` at the top, never use `pull_request_target`, never pipe `curl` into a shell.

---

## 11. Never do

- Call any non-loopback network address from application code.
- Bind any server to `0.0.0.0` or `::`.
- Use `shell=True`, `eval`, `exec`, or spawn a shell.
- Overwrite a user's input file.
- Mark an answer `SUPPORTED` without a verified verbatim quote and every other 8.4 check passing.
- Let model output raise a status. It can only lower one.
- Pull, download, or update models from inside the app.
- Add a deployment path for generated infrastructure code.
- Add dependencies without approval.
- Commit real company data, secrets, or `.env` files. All sample data is synthetic.
- Bypass git hooks (`--no-verify`, `-n`, `core.hooksPath`, `SKIP=`).

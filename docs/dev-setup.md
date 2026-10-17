# Developer setup (Windows)

These are development-machine steps only. None of these tools ship with the app, so the air-gap rules in `CLAUDE.md` Section 3 do not apply to them.

## Secret scanning with pre-commit and gitleaks

`pre-commit` runs checks on staged files each time you `git commit`. Our one hook is `gitleaks`, which scans the staged changes for secrets (API keys, tokens) and blocks the commit if it finds one. pre-commit builds gitleaks from source, so Go must be installed.

Versions pinned: pre-commit `4.6.2` and gitleaks `v8.30.1` (in `.pre-commit-config.yaml`). Go is not pinned; `1.27.0` is the version this was tested with.

Run these from PowerShell.

1. **Install Go** (once):
   ```
   winget install --id GoLang.Go -e
   ```
   Close and reopen the terminal, then run `go version`. Success looks like `go version go1.27.0 windows/amd64`.

2. **Turn Go telemetry off** (once):
   ```
   go telemetry off
   go telemetry
   ```
   The second command prints the current mode. Success is `off`.

3. **Install pre-commit** (once):
   ```
   python -m pip install --user pre-commit==4.6.2
   python -m pre_commit --version
   ```
   Success looks like `pre-commit 4.6.2`. If the plain `pre-commit` command is not found, the user Scripts folder is not on your PATH. Use `python -m pre_commit` instead, or add that folder to PATH.

4. **Enable the hook in your clone** (once per clone), from `C:\dev\grc-second-brain`:
   ```
   python -m pre_commit install
   ```
   Success looks like `pre-commit installed at .git\hooks\pre-commit`.

5. **Scan the whole repo**, from the same directory:
   ```
   python -m pre_commit run gitleaks --all-files
   ```
   Success looks like `Detect hardcoded secrets....Passed`. The first run is slow because it builds gitleaks.

6. **Prove it blocks secrets.** Do this in a throwaway repo, never in this one. Use a random token-shaped string: well-known documentation keys such as AWS's `AKIAIOSFODNN7EXAMPLE` may be allowlisted by gitleaks. Write the file with `Set-Content`, not `>`: Windows PowerShell's `>` writes UTF-16, which git treats as binary, so gitleaks may skip it and the test could falsely pass.
   ```
   mkdir C:\dev\gitleaks-test
   cd C:\dev\gitleaks-test
   git init
   copy C:\dev\grc-second-brain\.pre-commit-config.yaml .
   python -m pre_commit install
   python -c "import secrets,string;print('GITHUB_TOKEN=ghp_'+''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(36)))" | Set-Content config.txt
   git add .
   git commit -m "should be blocked"
   ```
   Success is the commit failing with `Detect hardcoded secrets....Failed`, `RuleID: github-pat`, and no commit created (`git log` reports no commits).

   When done, delete `C:\dev\gitleaks-test` in File Explorer.

If a real secret is ever flagged, remove it and rotate the credential. Don't bypass the hook with `--no-verify`.

## Continuous integration (CI)

Nine GitHub Actions workflows live in `.github/workflows/`. Eight run on every pull request and on every push to `main`; the ninth, `audit.yml`, runs weekly and on demand. They are the blocking controls described in CLAUDE.md Section 10: AI instructions are advisory, these are not. Every workflow has `permissions: contents: read` at the top, pins each action to a full commit SHA, and uses no secrets. Until branch protection exists (M9) the checks run and report on pull requests but do not yet stop a merge.

### Workflows at a glance

| Workflow (file) | Job (name to require at M9) | What it gates | Runner | Typical run time (observed, not guaranteed) | Required at M9 |
|---|---|---|---|---|---|
| `secrets.yml` | `gitleaks` | The scanner can detect a fake secret and stay quiet on clean input (self-test), then no secret anywhere in the full git history. | Linux | about 50 s | Yes |
| `policy.yml` | `policy-drift` | The Cursor rule mirrors CLAUDE.md Sections 3, 7 and 11. | Linux | about 10 s | Yes |
| `python.yml` | `quality` | `pip check`, ruff lint and format, `mypy --strict`, pytest (only the Windows-only skips are allowed on Linux), `pip-audit`. | Linux | about 40 s | Yes |
| `windows.yml` | `windows-tests` | `mypy --strict` including the Windows-only code, and the full sidecar suite with zero skips. | Windows | about 1 to 2 min | Yes |
| `sast.yml` | `semgrep` | Our air-gap rules and their own tests, plus registry rulesets for Python, TypeScript and Rust. | Linux | about 40 s (recorded 2026-10-06, M1.5; no later timing on record) | Yes |
| `sast.yml` | `bandit` | Python security lint (medium and above). | Linux | about 10 s | Yes |
| `airgap.yml` | `airgap` | The sidecar test suite passes in a hardened container with no network, and the canary proves the container really is isolated. | Linux | about 45 s (recorded 2026-10-06, M1.5; no later timing on record) | Yes |
| `rust.yml` | `rust` | `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test`, then the real-sidecar test (`cargo test -- --ignored`) against a venv the job builds. | Windows (`windows-2025`) | 8 to 10 min (nothing is cached). Two measured runs after the reqwest and real-sidecar steps were added (M2c, 2026-10-08): the pull request run took 10 min 7 s and the run on `main` after the merge took 8 min 16 s. The first run before M2c took about 7 min. | Yes |
| `rust.yml` | `deny` | `cargo deny`: advisories, licenses, bans, sources (Windows dependency graph). | Linux | about 45 s | Yes |
| `frontend.yml` | `frontend` | ESLint (`no-explicit-any` is an error, Node globals only in `scripts/`), `tsc`, vitest, a guard that fails if no test ran, `pnpm audit`. | Linux | about 25 s before M2d; not re-measured with the guards | Yes |
| `audit.yml` | `audit-pip`, `audit-pnpm`, `audit-cargo` | Weekly (Mondays 05:23 UTC) and on demand: `pip-audit`, `pnpm audit`, `cargo deny check advisories`, so a new advisory against an unchanged dependency shows up without a pull request. | Linux | not measured yet (first run is on the pull request that adds it) | No: it is scheduled, so it reports new advisories instead of gating a change |

**`audit.yml` is first exercised after the merge.** GitHub only runs scheduled and manually dispatched workflows from the default branch, so before the merge the only run is the one triggered by a pull request that edits `audit.yml` itself (the file has a `pull_request` trigger limited to its own path, so it does not run on other pull requests). After the merge, start the first scheduled-style run by hand from the **Actions** tab (select `audit`, then **Run workflow**) or with `gh workflow run audit.yml`, and check that it passes. A failed scheduled run is reported through GitHub's notifications to the repository owner.

Windows runners are billed at twice the Linux rate on a private repository, and the `rust` job is the long one. The run times above were read from real runs and will drift.

### Run each check locally

Run commands from the repo root in PowerShell unless a directory is given. Each deep-dive subsection below has the exact commands for its area and what success looks like.

| Check | Locally |
|---|---|
| gitleaks | `python -m pre_commit run gitleaks --all-files`. The CI self-test is `python .github/scripts/gitleaks_selftest.py` (add `--gitleaks <path to gitleaks.exe>` if `gitleaks` is not on PATH). Setup is in "Secret scanning with pre-commit and gitleaks" above. |
| policy-drift | `python .github/scripts/check_policy_drift.py`, then `python -m unittest discover -s .github/scripts -p "test_*.py"`. |
| quality | From `sidecar/`: `.venv/Scripts/python -m ruff check .`, `-m ruff format --check .`, `-m mypy --strict src`, `-m pytest` (CLAUDE.md Section 6). CI also runs `pip check` and `pip-audit`. |
| windows-tests | The same commands on this Windows machine. Here the Windows-only tests run for real, so `pytest` must report no skips (`174 passed`). |
| semgrep, bandit | The Docker commands under [Static analysis](#static-analysis-sast-semgrep-and-bandit). |
| airgap | The Docker commands under [Air-gap proof](#air-gap-proof-the-sidecar-tests-with-no-network). |
| rust, deny | From `apps/desktop/src-tauri/`: `cargo fmt --check`, `cargo clippy --all-targets --all-features --locked -- -D warnings`, `cargo test --locked`, `cargo deny check`. See [Rust checks](#rust-checks-and-cargo-deny). |
| frontend | From `apps/desktop/`: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm audit --audit-level high`. See [Frontend checks](#frontend-checks-lint-typecheck-test-audit). |
| audit (weekly) | The same audits the pull request jobs already run: `pip-audit` (see `python.yml`), `pnpm audit --audit-level high` from `apps/desktop/`, and `cargo deny check advisories` from `apps/desktop/src-tauri/`. |

### Reading a failed job on GitHub

1. Open the pull request and select the **Checks** tab (or click the red **x** next to a check in the pull request's check list). Each check is one job from the table above.
2. Click the red job and expand the step that has the red **x**. The log shows the tool's own output.
3. **Scroll for more than one failure.** Within a job the independent steps are written with `if: ${{ !cancelled() }}`, so a failing step does not hide the ones after it. Lint, type and test failures all show in one run.
4. The job's **Summary** page shows extra output the job wrote for people. For `frontend` it is the number of tests that ran.
5. Fix the cause and push. To re-run without a new commit, use **Re-run failed jobs** (top right of the run page). From PowerShell, `gh run list --branch <branch>` and `gh run view <run-id> --log-failed` print the same logs.

What a failure usually means:

| Failing step | Usual cause |
|---|---|
| `Scanner self-test` (gitleaks) | gitleaks could not detect the generated fake token, or flagged clean input: the scanner is broken or its config changed. This is not a finding in our code. |
| `Skipped tests are only the Windows-only ones` / `No test was skipped` | A test was skipped. The log names the test and the reason. |
| `Canary (no network)` exit 1 | The container can reach the network: the `--network none` flag is missing or ignored. Exit 2 means the canary could not even connect to loopback, so it proves nothing. |
| `Test the custom Semgrep rules` | A rule stopped matching a line marked `ruleid:`, or started matching a line marked `ok:` (the log says "incorrect lines" with the line number). |
| `Scan with Semgrep` / `Scan with Bandit` | A finding. Fix it, or follow the suppression rules under [Static analysis](#static-analysis-sast-semgrep-and-bandit). |
| `cargo deny` (`deny` job) | A new advisory, a license outside the allowlist, a yanked crate, or a non-crates.io source. The log names the crate and shows who depends on it. |
| `audit-pip`, `audit-pnpm` or `audit-cargo` (weekly `audit` run) | A new advisory against a dependency nobody changed. Update the dependency. If that is not possible, decide on an exception and record it (an ADR, or a reasoned `ignore` entry in `deny.toml`); never ignore it silently. |

### Dependabot

`.github/dependabot.yml` covers pip (`sidecar`), npm (`apps/desktop`), cargo (`apps/desktop/src-tauri`), github-actions (`/`) and docker (`sidecar`, for the digest-pinned base image of the air-gap test), all weekly. The docker entry ignores minor and major `python` updates on purpose: the sidecar targets Python 3.11. Action updates change the commit SHA and the version comment together. Dependabot errors on an entry whose manifest is missing, so entries are added only once their manifest exists.

**pydantic and pydantic_core.** `pydantic` requires one exact `pydantic_core` version, and `sidecar/constraints.txt` pins both (`pydantic==2.14.1` and `pydantic_core==2.50.1` today). Dependabot ignores `pydantic-core` on purpose (the `ignore` entry in `.github/dependabot.yml`, added in PR #7), so it proposes only pydantic bumps, and such a pull request would fail to install until `pydantic_core` in `constraints.txt` is set by hand to the exact version the new pydantic requires. That was done for pydantic 2.14.1 with pydantic_core 2.50.1 in PR #53. The lasting fix is the hash-locked dependencies planned for M8.

### Static analysis (SAST): Semgrep and Bandit

`.github/workflows/sast.yml` runs two jobs on every pull request and on pushes to `main`.

| Job | What it checks | Scope |
|---|---|---|
| `semgrep` | Our air-gap rules in `.semgrep/grc-airgap.yml` plus the registry rulesets `p/python`, `p/typescript` and `p/rust`. Fails on any finding. | Files tracked by git, except the rule fixtures. |
| `bandit` | Python security lint, fails on medium severity and above. | `sidecar/src`, `sidecar/ci` (the air-gap canary) and `.github/scripts` (CI scripts run subprocesses, so they are part of the supply chain). |

**Scope decision: tests are not scanned by Semgrep or Bandit.** Semgrep skips every `tests/` directory by built-in default (the repo has no `.semgrepignore`, on purpose: a custom one replaces those defaults), our own rule `grc-py-httpx-client-outside-factory` also excludes `**/tests/**`, and Bandit is not pointed at `sidecar/tests`. Test code is covered instead by ruff's `S` (flake8-bandit) rules, which run on `sidecar/tests` because `ruff check .` covers the whole sidecar, and by gitleaks for secrets. The `.github/scripts/test_*.py` files are the exception: Bandit reads them along with the rest of `.github/scripts`.

**Bandit and `shell=True`.** Bandit rates `shell=True` with a literal command (for example `subprocess.run("echo", shell=True)`) as Low (B602), and CI runs Bandit with `-ll` (medium and above), so that case does not fail the Bandit job. With a variable command, B602 is High and does fail it. Literal-command `shell=True` is gated by the custom Semgrep rule `grc-py-subprocess-shell-true` and by ruff `S602`; both turned red in the step 10 red-proof pull request (#4, closed unmerged). `-ll` is kept on purpose to avoid low-severity noise (for example B404 and B607 on the CI scripts); lowering it would need a fresh triage of those findings.

**Rule tests.** `.semgrep/fixtures/grc-airgap.{py,rs,tsx}` mark lines that must match a rule (`# ruleid: <rule-id>`) and near-misses that must not (`# ok: <rule-id>`). The directory is deliberately not named `tests`, because one rule excludes `**/tests/**` and would stop firing there. The fixtures break the rules on purpose, so they are the one directory excluded from the main scan (`--exclude .semgrep/fixtures`). `.github/scripts/check_semgrep_rule_coverage.py` fails if a rule has no `ruleid:` fixture, because `semgrep --test` silently skips untested rules. Known gap: the `paths.exclude` on `grc-py-httpx-client-outside-factory` (the factory `net.py` and tests) cannot be exercised from a fixture.

**Run them locally** (Docker Desktop must be running; run from the repo root in PowerShell). The first run downloads the pinned Semgrep image and the registry rules.

```powershell
$semgrep = "semgrep/semgrep:1.179.0@sha256:93963d9295a366f59e4850127b1550400ee7b388f04fe144e4a1f6325d96e01b"
# Rule tests: success prints "All tests passed" three times (22 rule tests for py, 7 for rs, 2 for tsx, as reported in the 6j local Semgrep run). Observed with Semgrep 1.179.0: the fixture include of six custom rules in `.semgrep/grc-airgap.yml` is anchored with a leading `/`, and the Semgrepignore v2 deprecation warning no longer printed in the scan; the `--test` counts were unchanged (22, 7, 2). `semgrep --test` did not fail when that include was broken, so the `--test` runs do not exercise the include; a scan of a copy of the fixture at the root path and at another path did (after the change it matched only the root path).
foreach ($ext in "py","rs","tsx") { docker run --rm -v "${PWD}:/src:ro" -w /src $semgrep semgrep --test --config .semgrep/grc-airgap.yml ".semgrep/fixtures/grc-airgap.$ext" --metrics=off --disable-version-check }
# Main scan: success ends with "Findings: 0 (0 blocking)".
docker run --rm -v "${PWD}:/src:ro" -w /src $semgrep semgrep scan --config .semgrep/ --config p/python --config p/typescript --config p/rust --exclude .semgrep/fixtures --error --metrics=off --disable-version-check
# Bandit, from a throwaway container: success is exit code 0 and "No issues identified".
docker run --rm -v "${PWD}:/src:ro" -w /src python:3.11.9-slim bash -c 'pip install -q --root-user-action=ignore bandit==1.9.4 && bandit -r sidecar/src sidecar/ci .github/scripts -ll --exclude "**/__pycache__"'
```

Semgrep only scans files git knows about, so a new, untracked file is not scanned until you `git add` it (rule fixtures included).

**Suppressions.** A finding is fixed unless it is a documented false positive or an accepted risk. Then it gets a line-specific comment that names the rule and says why: `# nosec B314` for Bandit, `# nosemgrep: <full-rule-id>` for Semgrep, with the reason in the comment above. No blanket path excludes and no disabled rules without a decision recorded here or in an ADR. The current suppressions are all in `.github/scripts` (the junit XML parse is pytest's own report; the gitleaks self-test runs a fixed command list with `shell=False`).

**License note for M9.** The Semgrep registry rulesets are under the Semgrep Rules License (fine for internal CI use, restrictive for redistribution). Revisit before the public release and for the BizAI commercial version.

### Air-gap proof: the sidecar tests with no network

`.github/workflows/airgap.yml` builds `sidecar/Dockerfile.airgap` (network allowed, because pip needs it), then runs two things with **no network** and a hardened container: the canary, and the whole pytest suite.

- **Canary** (`sidecar/ci/airgap_canary.py`) checks three things: a connection to loopback succeeds, a connection to a public address fails, and a DNS lookup fails. If loopback itself fails, the canary exits 2 ("broken canary") instead of passing, because a check that cannot connect proves nothing. Exit codes: 0 isolated, 1 the network is reachable, 2 broken canary.
- **Hardened flags:** `--network none --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp`, running as an unprivileged user (uid 10001). The app files are owned by root, so they are read-only for that user. The pytest egress guard also stays on, so the network is blocked twice. The only adjustment the flags needed is `-p no:cacheprovider`, because pytest cannot write `.pytest_cache` on a read-only filesystem.
- **Skipped tests:** the skip check allows three reasons in this job: "Windows-only path behavior", "Repository root not found (air-gap container holds only sidecar/)" and "docs/ is not present in this environment" (`.github/workflows/airgap.yml`); `python.yml` allows only the first. The junit file is written to `/tmp` and the skip check (mounted read-only from `.github/scripts`) runs in the same container, because `/tmp` disappears when it exits.

Run it locally from the repo root (Docker Desktop running; PowerShell). The tests read the synthetic vaults, the fixtures, the packs, `tools/` and `.gitignore` from the root of the container, so the command mounts them read-only, the same six mounts as `.github/workflows/airgap.yml`. Success is `RESULT: ISOLATED` from the canary and a pytest summary with no failures whose only skips are the allowed reasons above.

```powershell
docker build -f sidecar/Dockerfile.airgap -t grc-airgap sidecar
$hardened = "--network","none","--cap-drop","ALL","--security-opt","no-new-privileges","--read-only","--tmpfs","/tmp"
$mounts = "-v","${PWD}/vault-sample:/vault-sample:ro","-v","${PWD}/vault-demo:/vault-demo:ro","-v","${PWD}/fixtures:/fixtures:ro","-v","${PWD}/packs:/packs:ro","-v","${PWD}/tools:/tools:ro","-v","${PWD}/.gitignore:/.gitignore:ro"
docker run --rm @hardened grc-airgap python ci/airgap_canary.py
docker run --rm @hardened @mounts grc-airgap python -m pytest -rs -p no:cacheprovider
```

Observed 2026-10-17 on `main` (6331dc9), on Windows with PowerShell and Docker, with the CI-style command and no network (an observation, not a test; the counts change whenever tests are added): 3,255 tests collected, 15 deselected by the `ollama` marker, 3,240 selected; the pytest run took 117.55 s on the owner's Windows machine with Docker (an owner observation); 3,224 passed, 16 skipped; the skips were only the three allowed reasons; the canary printed `ISOLATED`.

To see the canary fail, run it without `--network none` (keep the other flags). It must print `FAIL` for outbound and DNS, `RESULT: NOT ISOLATED`, and exit 1.

The base image is pinned by digest. Dependabot's `docker` entry proposes digest and patch updates only; minor and major Python bumps are ignored on purpose, since the sidecar targets Python 3.11.

### Rust checks and cargo-deny

`.github/workflows/rust.yml` has two jobs. The host code (M2a to M2c) has unit tests, Job Object and supervisor tests against a fake sidecar, security-configuration tests, and one test that starts the real Python sidecar. That last test is `#[ignore]`d, so a plain `cargo test` skips it and shows it as ignored; the `rust` job runs it in its own steps (see below). A green `rust` job therefore means it ran, not that it was skipped: the step requires at least one passed test.

| Job | Runner | What it does |
|---|---|---|
| `rust` | Windows | `cargo fmt --check`, `cargo clippy --all-targets --all-features --locked -- -D warnings`, `cargo test --locked`, then `setup-python` 3.11, a venv at `sidecar/.venv` with the runtime dependencies from `constraints.txt`, and `cargo test --locked --test real_sidecar -- --ignored` (with `set -o pipefail` and a check for at least one passed test). The compiler is pinned to 1.99.0 by `apps/desktop/src-tauri/rust-toolchain.toml` (bump it by hand, in its own commit), and the runner to `windows-2025`. |
| `deny` | Linux | `cargo deny check` with `apps/desktop/src-tauri/deny.toml`: advisories, licenses, bans, sources. Nothing is compiled. |

**Run `cargo deny` locally** (from `apps/desktop/src-tauri/`). The first command installs the tool on your machine and takes a few minutes; success is `advisories ok, bans ok, licenses ok, sources ok` (duplicate-version warnings are expected):

```powershell
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check
```

**Windows only.** `deny.toml` checks the Windows dependency graph only, because Windows is the only platform we ship. Linux's GTK stack carries a glib unsoundness advisory (RUSTSEC-2024-0429) and an unmaintained crate (RUSTSEC-2024-0370) that never reach a Windows build. The reasoning, the `cargo tree` evidence, and the trigger for revisiting it (any non-Windows build being planned) are in [ADR-003](adr/003-cargo-deny-windows-target.md). GitHub's Dependabot alert for glib stays open because Dependabot does not know about this scope.

**Licenses.** The allowlist is MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, plus Unicode-3.0 and Zlib (permissive licenses used by Tauri's dependencies). MPL-2.0 is allowed per crate only (five crates listed in `deny.toml`, used unmodified), so a new MPL-2.0 crate fails CI until someone reviews it. The `desktop` crate has no license yet (the project license is decided at M9) and is marked `publish = false` so `cargo deny` skips it. Third-party license notices must ship with the installer (M8).

**Duplicate crate versions** are a warning, not a failure: Tauri's own tree contains duplicates (for example `syn`, `base64`, `windows-sys`) that we cannot fix.

**Run the real-sidecar test locally.** It starts the real Python sidecar through the same code the app uses (`SidecarHost::start_dev`), so it needs the dev venv. From `apps/desktop/src-tauri/`:

```powershell
cargo test --test real_sidecar -- --ignored
```

Success looks like `test the_real_sidecar_starts_answers_health_and_stops ... ok` and `test result: ok. 1 passed; 0 failed`, in a few seconds. It checks that the sidecar starts from the venv interpreter, that its job holds exactly the launcher, one `python.exe` child and at most one `conhost.exe` child, that `/health` answers through the host's client, that the same request without the auth header gets `401` with an empty body, and that stopping it leaves no process and a closed port. If it fails with `NotInstalled`, the venv is missing: from `sidecar/` run `python -m venv .venv` (Python 3.11), then `.venv\Scripts\python -m pip install -c constraints.txt -e .`. Without `--ignored` the test is listed as ignored, which is not a pass.

### Frontend checks (lint, typecheck, test, audit)

`.github/workflows/frontend.yml` runs these from `apps/desktop/` on every pull request and push to `main`, with the pnpm version taken from `packageManager` in `package.json`. Run them yourself the same way (PowerShell; success is exit code 0 for each):

```powershell
cd apps\desktop
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm audit --audit-level high
```

- **`pnpm lint`** runs ESLint with `@eslint/js` and `typescript-eslint` recommended rules, then `scripts/check-eslint-scope.mjs`. `@typescript-eslint/no-explicit-any` is an error. No rule is switched off and nothing is excluded: the script names the files to check (`src`, `scripts`, `vite.config.ts`, `eslint.config.js`). Node globals (`process`, `require`, ...) are given only to `scripts/**/*.mjs`; the app in `src/` runs in a webview and must not see them. ESLint's own `no-undef` cannot enforce that (typescript-eslint turns it off for TypeScript files), so `check-eslint-scope.mjs` asks ESLint which globals each file gets and fails if any `src` file has a Node one or a script lacks them.
- **`pnpm typecheck`** is `tsc --noEmit`. `tsconfig.json` has `strict`, `noImplicitAny` and `noUncheckedIndexedAccess` (CLAUDE.md Section 7), so reading `list[i]` gives `T | undefined` and has to be checked, never silenced with `!` or `as`.
- **`pnpm test` is plain `vitest run`** and runs real tests since M2d: the Zod schema for the `sidecar_health` payload, the contract fixture, the poller, the status text and `requireRoot` (74 tests at the end of M2d). With no test files it now fails (it used to pass with "No test files found", a green that checked nothing). In CI the `frontend` job adds two steps: `scripts/check-test-count.selftest.mjs` proves the guard can fail (empty suite, all-skipped suite, missing or malformed report), then `scripts/check-test-count.mjs` fails the job if the report is missing or no test passed or failed, and writes the counts to the job summary. Run the guard locally with `pnpm test --reporter=default --reporter=json --outputFile.json=$env:TEMP\vitest.json` then `node scripts/check-test-count.mjs $env:TEMP\vitest.json` (success prints `Frontend tests run: 74 (passed 74, ...)`). Since M6 (chunks 6f to 6h) the suite also renders each presentational component to static markup with `react-dom/server` (no jsdom, by choice: one dependency less) and runs the lint-rule and review-queue source-scan tests in `scripts/`; the test run at the 6i close was 422 tests (counts script, 2026-10-16) and 428 after chunk 6j, and 440 tests in 25 files after chunk 6l (2026-10-17, `pnpm test`, an observation), so the "74 tests" figures in this paragraph are the end-of-M2d numbers. `App.tsx` and the hooks in `src/hooks/` are still rendered in no test, and the rendered window is checked by hand (next section). That gap is recorded in [ADR-006](adr/006-window-status-polling-and-ipc-contract.md) and, for M6, in the threat model (F6-1c, F20-3).
- **The IPC contract fixture.** `apps/desktop/src/ipc/health-contract.json` is the JSON Rust serializes for every `HealthReply` and `HealthError`. The Rust test `the_ipc_contract_fixture_matches_what_rust_serializes` compares it with the live serialization, and the Vitest suite parses every entry with the Zod schema and checks that the names equal the schema's. Adding a Rust variant fails to compile until it is listed and then fails the Rust test until the fixture is updated; the Vitest suite then fails until the schema knows it. To regenerate after a deliberate change, from `apps/desktop/src-tauri/` run `$env:GRC_WRITE_HEALTH_CONTRACT = "1"; cargo test --lib the_ipc_contract` (it rewrites the file and still FAILS, so a run that wrote the file never looks like a pass), then `Remove-Item Env:GRC_WRITE_HEALTH_CONTRACT` and run it again.
- **What the window shows.** `Sidecar: starting`, `Sidecar: healthy`, or `Sidecar: failed (<source>: <code>)`, built only from fixed codes. `source` is `sidecar` (the host's supervisor failed it), `health_check` (the sidecar is believed running but a probe failed), `stopped`, or `ipc`. Two `ipc` codes exist and the CHANNEL decides, never the content: `invalid_response` is a successful `invoke()` whose value did not match the schema; `rejected` is a rejected `invoke()` that was not a valid health error (for example the string Tauri gives a command the capability does not allow).
- **`pnpm audit`** sends the package names and versions in `pnpm-lock.yaml` to the npm audit endpoint and fails on high or critical advisories.
- **Install scripts:** pnpm does not run dependency install scripts unless they are approved, and none are approved here. If `pnpm install` ever reports ignored build scripts, treat it as something to review, not to approve by reflex.

Every payload that crosses the Tauri IPC boundary is parsed with Zod (CLAUDE.md Section 7): `src/App.tsx` calls `invoke<unknown>("sidecar_health")` only through `fetchHealth` in `src/ipc/health.ts`, which never throws and never lets an unparsed value reach the UI. `src/main.tsx` uses the checked `requireRoot` instead of the template's `as HTMLElement` cast.

### Run the M6 screens (debug build only)

Until M8 there is no installer and a release build cannot start the sidecar, so the folders, index, questionnaire and review-queue screens run only from a debug build made from this repository. From `C:\dev\grc-second-brain\apps\desktop` (PowerShell), with the dev venv built (`sidecar\.venv`) and Ollama running with the two models installed:

```
pnpm install --frozen-lockfile
pnpm tauri build --debug --no-bundle
Start-Process (Resolve-Path .\src-tauri\target\debug\desktop.exe)
```

Success: the window opens at 1100 by 720, the status bar shows `Sidecar: healthy` and the models, and the Folders screen lists five rows. Then, in order: choose the vault, the ledger folder, the questionnaires folder and the exports folder; close the window and start it again (saved folders apply at the next launch; "In use now" must show them); run **Index the vault**; run a questionnaire from the Questionnaire screen; open its draft in the Review queue. Set the ledger folder **before** the run, or the draft cannot be opened in the queue. As the traps below say, close the app before rebuilding, and run `pnpm tauri build --debug --no-bundle` right before the launch: `pnpm tauri dev` loads the UI from the Vite dev server with no CSP, and an old `desktop.exe` may still point at it (this happened in chunk 6f-B). The close checklist for M6 in this window (CSP probes, `plugin:dialog|open` refused, keyboard-only use, forced-colors, DevTools in a release build) was run once by hand on 2026-10-16 on one machine; its results are in M6_PLAN.md (section "M6 close check results") for the screens, keyboard and high contrast, and in the threat-model rows F5-1b, G12, F16-2b and F35-4a for the CSP, plugin, eval and DevTools observations (F12-2c shows Row 13 of the reviewed draft again), not here. **Not done at that check:** the smallest window size was not confirmed; the Vault index and Questionnaire screens were not exercised with data (the first live run is recorded elsewhere); the edited-status, edited-answer, edited-evidence and `question_mismatch` labels were not hand-tested; contrast ratios were not measured; the installer (M8) was not tested.

### Manual check of the running app (M2d)

CI cannot render a webview or watch process trees, so this check is done by hand when the host, the capability, the CSP or the window status change. Use two Cursor (or any PowerShell) terminals. All paths are from `C:\dev\grc-second-brain\apps\desktop`. The last full run is recorded in [ADR-005](adr/005-webview-csp-and-capabilities.md) and [ADR-006](adr/006-window-status-polling-and-ipc-contract.md).

**Two traps.**

- **Mode B is only real right after a fresh build.** `pnpm tauri dev` and `pnpm tauri build --debug --no-bundle` both write `src-tauri\target\debug\desktop.exe`. The dev one loads the UI from `http://localhost:1420` with no CSP; the build loads it from `http://tauri.localhost` with the shipped CSP. Evidence for the production CSP or the capability counts only from a `pnpm tauri build --debug --no-bundle` run immediately before the launch, with `location.origin` showing `http://tauri.localhost`. Do not run `pnpm tauri dev` between the build and the launch.
- **Close the app before rebuilding.** If `desktop.exe` is still running, the build fails with "Access is denied" (seen on 2026-10-08, with the mutated app still open). Close the window, or `Stop-Process -Name desktop -Force`, and build again.

**1. Prep.** `git status --short`, `pnpm install --frozen-lockfile`, and make sure nothing is left over: `Get-Process desktop, fake_sidecar -ErrorAction SilentlyContinue` prints nothing, and `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*grc_sidecar*' } | Select-Object ProcessId, Name` prints nothing.

**2. A, dev mode.** `pnpm tauri dev`. Expected: the window shows `Sidecar: starting`, then `Sidecar: healthy` within a few seconds. `Get-Process desktop, fake_sidecar -ErrorAction SilentlyContinue` lists `desktop` and not `fake_sidecar` (the right binary launched). Close the window with the X; `pnpm` ends with `ELIFECYCLE` exit -1, which is normal. Both checks from step 1 print nothing again. There are no CSP probes here: dev mode has no CSP (ADR-005).

**3. B, mode B.** `pnpm tauri build --debug --no-bundle`, then `Get-Date` and `Get-Item .\src-tauri\target\debug\desktop.exe | Select-Object LastWriteTime` (the write time must be within a minute or two), then `Start-Process (Resolve-Path .\src-tauri\target\debug\desktop.exe)`. Expected: `Sidecar: healthy`. Press F12 and run in the console, one at a time:

| Console command | Expected |
|---|---|
| `location.origin` | `http://tauri.localhost` |
| `await window.__TAURI_INTERNALS__.invoke('sidecar_health')` | `{status: 'healthy'}` (the capability works with only `allow-sidecar-health`) |
| `fetch('http://127.0.0.1:65000/').catch(e => console.log(String(e)))` | refused by CSP: `... violates the following Content Security Policy directive: "connect-src ipc: http://ipc.localhost"`, then `TypeError: Failed to fetch`. Port 65000, never port 1 (Chromium blocks port 1 before CSP sees it). |
| `eval('1')` | `EvalError` naming `unsafe-eval` and `script-src` |
| `document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content')` | `undefined` (the policy comes as a response header) |

Then open the Network tab, press Ctrl+R, click the `tauri.localhost` document and copy the `content-security-policy` response header: it must be the configured policy plus a `'sha256-...'` source on `script-src` that Tauri adds for the bundled script (the hash changes whenever the frontend bundle changes). DevTools' own request for `/.well-known/appspecific/com.chrome.devtools.json` is blocked by `connect-src`; that is DevTools, not the app.

**4. C, processes and port** (app healthy, run in a terminal you keep open: later steps reuse `$ids` and `$port`):

```powershell
$desk = Get-CimInstance Win32_Process -Filter "Name='desktop.exe'"
$desk | Select-Object ProcessId, ParentProcessId, ExecutablePath
$launcher = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $desk.ProcessId -and $_.Name -eq 'python.exe' }
$kids = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $launcher.ProcessId }
@($launcher) + @($kids) | Select-Object ProcessId, ParentProcessId, Name, CommandLine | Format-Table -AutoSize -Wrap
$ids = @($launcher.ProcessId) + @($kids.ProcessId)
$pattern = '\s(' + ($ids -join '|') + ')\s*$'
$v4 = netstat -ano -p TCP | Select-String 'LISTENING' | Select-String $pattern
$v4 | ForEach-Object { $_.Line.Trim() }
$port = (($v4 | Select-Object -First 1).Line.Trim() -split '\s+')[1] -split ':' | Select-Object -Last 1
$port
netstat -ano -p TCPv6 | Select-String 'LISTENING' | Select-String $pattern
```

Expected: one `desktop.exe`; a launcher `...\sidecar\.venv\Scripts\python.exe -I -m grc_sidecar` whose parent is `desktop.exe` (its command line may start with `\\?\`, the extended-length form of a canonical path); a `conhost.exe` and one real `...\Python311\python.exe -I -m grc_sidecar`, both with the launcher as parent (a short-lived `cmd.exe` under the interpreter may also show, ADR-004). The `netstat` IPv4 output is exactly one `LISTENING` line, `127.0.0.1:<port>`, never `0.0.0.0` or `[::]`, owned by the interpreter; the IPv6 command prints nothing. **Why `netstat` and not `Get-NetTCPConnection`:** on the development machine `Get-NetTCPConnection -State Listen` reported "No MSFT_NetTCPConnection objects found" for the whole system, so it cannot tell "closed" from "broken"; `netstat -ano` worked.

**5. D, force-close.** `Stop-Process -Name desktop -Force`, then `Get-Process -Id $ids -ErrorAction SilentlyContinue` and `Get-Process desktop -ErrorAction SilentlyContinue` print nothing, and `netstat -ano -p TCP | Select-String ":$port\s" | Select-String 'LISTENING'` prints nothing. `TIME_WAIT` lines for that port owned by PID 0 are normal (closed health connections), not a listener. If a `LISTENING` line remains, wait 5 s and run it again; report both results (Windows can keep a port open briefly after a kill).

**6. Normal close.** `Start-Process (Resolve-Path .\src-tauri\target\debug\desktop.exe)`, wait for `healthy`, re-run step 4 unchanged, close the window with the X, and repeat the three checks of step 5 (without `Stop-Process`). Same expected results.

**7. E, other launch context.** Launch the same exe from a terminal outside your editor (a plain Windows PowerShell or Windows Terminal window) and repeat steps 4 and 5; `desktop.exe`'s parent will be that shell, not your editor. The host may be started inside a Windows job (an IDE terminal or CI runner can be in one), which is why the Job Object code is tested for nesting; this step shows the app works from either launcher, but it cannot tell you which of them is inside a job.

**8. Capability mutation (proves the permission is what lets the call through).** Close the app. Then:

```powershell
$f = "src-tauri\capabilities\main-window.json"
(Get-FileHash $f -Algorithm SHA256).Hash
(Get-Content $f -Raw) -replace '"allow-sidecar-health"', '' | Set-Content $f -NoNewline
Get-Content $f
pnpm tauri build --debug --no-bundle
Start-Process (Resolve-Path .\src-tauri\target\debug\desktop.exe)
```

The file now says `"permissions": []`. In the F12 console run `window.__TAURI_INTERNALS__.invoke('sidecar_health').catch(e => console.log('REJECTED WITH:', typeof e, String(e)))`. Expected: `REJECTED WITH: string sidecar_health not allowed. Permissions associated with this command: allow-sidecar-health`, and the window line `Sidecar: failed (ipc: rejected)` (the 2026-10-08 run did not record the window line). `cargo test --locked --test security_config` from `src-tauri\` is red while the file is mutated.

**9. Restore and re-prove.** Close the app (see the trap above), `git restore src-tauri/capabilities/main-window.json`, check the hash equals the one from step 8 and that `git status --short` shows no change to the file, then rebuild with `pnpm tauri build --debug --no-bundle`, launch, and expect `Sidecar: healthy` again. Never commit the mutated file.

### Decisions and where each is recorded

| Decision | Where it is recorded |
|---|---|
| `cargo deny` checks only the Windows dependency graph, with `unsound` and `unmaintained` set to `all`. Revisit when any non-Windows build is planned. | [ADR-003](adr/003-cargo-deny-windows-target.md), plus comments in `apps/desktop/src-tauri/deny.toml` |
| Hash-locked Python dependencies (uv or pip-tools) are deferred to M8, where the exact PyInstaller artifacts matter most. Until then the sidecar has exact `==` pins in `constraints.txt`, `pip-audit`, Dependabot and digest-pinned images. | The M8 row in CLAUDE.md Section 9, and the header comment in `sidecar/constraints.txt`. |
| The Semgrep registry rulesets use the Semgrep Rules License (fine for internal CI, restrictive for redistribution). Revisit before the public release and for the BizAI commercial version. | [Static analysis](#static-analysis-sast-semgrep-and-bandit) (license note), and the M9 row in CLAUDE.md Section 9. |
| Tests are not scanned by Semgrep or Bandit. Ruff's `S` rules and gitleaks cover them. | [Static analysis](#static-analysis-sast-semgrep-and-bandit) (scope decision) |
| Bandit runs at `-ll`, so `shell=True` with a literal command (rated Low) is not a Bandit failure. The Semgrep rule `grc-py-subprocess-shell-true` and ruff `S602` gate it instead; both were proven red in step 10. | [Static analysis](#static-analysis-sast-semgrep-and-bandit) (Bandit and `shell=True`) |
| Rust tests exist (unit, fake sidecar, Job Object, security configuration, and the `#[ignore]`d real-sidecar test that CI runs explicitly). Frontend tests exist since M2d and the `frontend` job fails if none ran (a self-tested guard). | [Frontend checks](#frontend-checks-lint-typecheck-test-audit) and [Rust checks](#rust-checks-and-cargo-deny) |
| Every IPC payload is parsed with strict Zod schemas; the channel (resolve or reject) decides `invalid_response` or `rejected`; Rust and TypeScript are kept in step by a committed fixture that both sides test; the window polls every 500 ms while starting and every 5 s afterwards, so a dead sidecar is noticed. `App.tsx` is not rendered in tests (known gap). | [ADR-006](adr/006-window-status-polling-and-ipc-contract.md) |
| Port checks in the manual check use `netstat -ano`, not `Get-NetTCPConnection`, which returned nothing system-wide on the development machine. | [Manual check](#manual-check-of-the-running-app-m2d) |
| Air-gap job: a hardened container with `--network none`, and `-p no:cacheprovider` as the only adjustment the flags needed. | [Air-gap proof](#air-gap-proof-the-sidecar-tests-with-no-network) |
| MPL-2.0 is allowed per crate, never globally. The `desktop` crate is `publish = false` so `cargo deny` skips its missing license. Duplicate crate versions only warn. | [Rust checks](#rust-checks-and-cargo-deny) and `apps/desktop/src-tauri/deny.toml` |
| Windows runners are pinned to `windows-2025` (like the Linux jobs to `ubuntu-24.04`), so the CI environment changes only when we change the label. `windows-2025-vs2026` was not used: it is a validation label for the VS 2026 migration and could be retired. | `rust.yml` and `windows.yml` (comments on `runs-on`) |
| The webview CSP is the shipped one only: `pnpm tauri dev` serves the UI from the Vite dev server, where Tauri injects no CSP, so there is no `devCsp`. The CSP is verified in a debug build of the bundled app, with probes on port 65000 (port 1 is on Chromium's blocked list and never reaches CSP). One capability, one command. | [ADR-005](adr/005-webview-csp-and-capabilities.md) |
| The real sidecar's job holds three processes in dev (launcher, interpreter, a `conhost.exe`); the check is on identity, not a count. `DETACHED_PROCESS` was not adopted. | [ADR-004](adr/004-host-sidecar-lifecycle.md) (Job Object) |
| No dependency cache in CI (no `actions/cache`, no setup-node cache): one less thing to poison, and the installs are small. | A comment in `frontend.yml`. The other workflows install without any cache action too. |

## Live Ollama checks (M3c)

The unit tests use a fake embedder and a fake Ollama, so CI and the air-gap container need no model. A few checks need a real local Ollama. They are marked `ollama` and deselected by default (a deselected test is not a skip, so the CI skip check is unaffected).

Needs `ollama serve` running and `nomic-embed-text` installed (`ollama list` shows `nomic-embed-text:latest`). The checks never pull a model. From `C:\dev\grc-second-brain\sidecar`, in a Cursor integrated terminal:

```
.venv/Scripts/python -m pytest -m ollama -v -rA
```

Success: 6 passed. The `-rA` output shows what each check measured: the model digest and 768 dimensions, the vector norm (about 1.0), the sample vault indexing with 0 issues (6 documents), the top 3 results for "multi-factor authentication" (the access-control policy first), an unknown model failing before any database write, the longest identifier-dense string the model accepts (2820 characters, 1.38 characters per token), and the worst-case chunk test at `HARD_MAX_EMBED_CHARS` (dense and prose strings, with Ollama's token counts). If the dense case fails, `HARD_MAX_EMBED_CHARS` has to be lowered (see ADR-009, "Embedding length cap").

## Run the sidecar by hand (and check it only listens on loopback)

You need two PowerShell windows. The auth token exists only inside the sidecar's own process (it deletes it from its environment after reading it), so terminal 2 needs the printed token pasted in.

**Terminal 1**, from `sidecar/`: generate a token into a variable, print it, then start the sidecar.

```
$token = (.venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))")
$token
$env:GRC_AUTH_TOKEN = $token
.venv\Scripts\python -m grc_sidecar
```

Success looks like exactly one line, `{"event":"ready","port":NNNNN}`, and the window stays busy because the server is running. Copy the printed token and the port number.

**Terminal 2**, any directory: paste the token into `$t`, then replace `NNNNN` with the port.

```
$t = "<paste the token printed in terminal 1>"
netstat -ano -p tcp | findstr "LISTENING" | findstr ":NNNNN"
Get-NetTCPConnection -State Listen -LocalPort NNNNN | Select-Object LocalAddress, LocalPort, OwningProcess
Invoke-WebRequest http://127.0.0.1:NNNNN/health -Headers @{"X-GRC-Auth"=$t} -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:NNNNN/health -UseBasicParsing
```

Success looks like:

- The listening address is `127.0.0.1:NNNNN`. It must never be `0.0.0.0:NNNNN` or `[::]:NNNNN`, which would mean the server is reachable from other machines.
- The first `Invoke-WebRequest` (with the header) returns status 200 and content `{"status":"ok"}`.
- The second (no header) fails with a 401 error.

Clean up: press Ctrl+C in terminal 1, then run `Remove-Item Env:GRC_AUTH_TOKEN` there. In terminal 2 run `Remove-Variable t` or just close the window.

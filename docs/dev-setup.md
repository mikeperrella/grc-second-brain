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
| `sast.yml` | `semgrep` | Our air-gap rules and their own tests, plus registry rulesets for Python, TypeScript and Rust. | Linux | about 40 s | Yes |
| `sast.yml` | `bandit` | Python security lint (medium and above). | Linux | about 10 s | Yes |
| `airgap.yml` | `airgap` | The sidecar test suite passes in a hardened container with no network, and the canary proves the container really is isolated. | Linux | about 45 s | Yes |
| `rust.yml` | `rust` | `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test` (no Rust tests exist yet). | Windows | about 7 min on the first run (nothing is cached) | Yes |
| `rust.yml` | `deny` | `cargo deny`: advisories, licenses, bans, sources (Windows dependency graph). | Linux | about 45 s | Yes |
| `frontend.yml` | `frontend` | ESLint (`no-explicit-any` is an error), `tsc`, vitest (no frontend tests exist yet), `pnpm audit`. | Linux | about 25 s | Yes |
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
# Rule tests: success prints "All tests passed" three times (9/9, 2/2, 2/2).
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
- **Skipped tests:** the same rule as `python.yml`. The junit file is written to `/tmp` and the skip check (mounted read-only from `.github/scripts`) runs in the same container, because `/tmp` disappears when it exits.

Run it locally from the repo root (Docker Desktop running; PowerShell). Success is `RESULT: ISOLATED` and `165 passed, 9 skipped`:

```powershell
docker build -f sidecar/Dockerfile.airgap -t grc-airgap sidecar
$hardened = "--network","none","--cap-drop","ALL","--security-opt","no-new-privileges","--read-only","--tmpfs","/tmp"
docker run --rm @hardened grc-airgap python ci/airgap_canary.py
docker run --rm @hardened grc-airgap python -m pytest -rs -p no:cacheprovider
```

To see the canary fail, run it without `--network none` (keep the other flags). It must print `FAIL` for outbound and DNS, `RESULT: NOT ISOLATED`, and exit 1.

The base image is pinned by digest. Dependabot's `docker` entry proposes digest and patch updates only; minor and major Python bumps are ignored on purpose, since the sidecar targets Python 3.11.

### Rust checks and cargo-deny

`.github/workflows/rust.yml` has two jobs. The Rust code is still the Tauri template, which has **no Rust tests yet**: `cargo test` reports "0 passed" until real tests arrive with the host code in M2, so a green `rust` job does not mean tests exist.

| Job | Runner | What it does |
|---|---|---|
| `rust` | Windows | `cargo fmt --check`, `cargo clippy --all-targets --all-features --locked -- -D warnings`, `cargo test --locked`, with the compiler pinned to 1.99.0 by `apps/desktop/src-tauri/rust-toolchain.toml` (bump it by hand, in its own commit). |
| `deny` | Linux | `cargo deny check` with `apps/desktop/src-tauri/deny.toml`: advisories, licenses, bans, sources. Nothing is compiled. |

**Run `cargo deny` locally** (from `apps/desktop/src-tauri/`). The first command installs the tool on your machine and takes a few minutes; success is `advisories ok, bans ok, licenses ok, sources ok` (duplicate-version warnings are expected):

```powershell
cargo install cargo-deny --version 0.20.2 --locked
cargo deny check
```

**Windows only.** `deny.toml` checks the Windows dependency graph only, because Windows is the only platform we ship. Linux's GTK stack carries a glib unsoundness advisory (RUSTSEC-2024-0429) and an unmaintained crate (RUSTSEC-2024-0370) that never reach a Windows build. The reasoning, the `cargo tree` evidence, and the trigger for revisiting it (any non-Windows build being planned) are in [ADR-003](adr/003-cargo-deny-windows-target.md). GitHub's Dependabot alert for glib stays open because Dependabot does not know about this scope.

**Licenses.** The allowlist is MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, plus Unicode-3.0 and Zlib (permissive licenses used by Tauri's dependencies). MPL-2.0 is allowed per crate only (five crates listed in `deny.toml`, used unmodified), so a new MPL-2.0 crate fails CI until someone reviews it. The `desktop` crate has no license yet (the project license is decided at M9) and is marked `publish = false` so `cargo deny` skips it. Third-party license notices must ship with the installer (M8).

**Duplicate crate versions** are a warning, not a failure: Tauri's own tree contains duplicates (for example `syn`, `base64`, `windows-sys`) that we cannot fix.

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

- **`pnpm lint`** runs ESLint with `@eslint/js` and `typescript-eslint` recommended rules. `@typescript-eslint/no-explicit-any` is an error. No rule is switched off and nothing is excluded: the script names the files to check (`src`, `vite.config.ts`, `eslint.config.js`).
- **`pnpm typecheck`** is `tsc --noEmit`. `tsconfig.json` has `strict`, `noImplicitAny` and `noUncheckedIndexedAccess` (CLAUDE.md Section 7), so reading `list[i]` gives `T | undefined` and has to be checked, never silenced with `!` or `as`.
- **There are no frontend tests until M2/M6.** `pnpm test` is `vitest run --passWithNoTests`, which exits 0 when there are no test files, as with the Tauri template today. The CI job writes the number of tests that ran into its summary, so "0" is visible rather than hidden; real tests arrive with the IPC layer (M2) and the UI (M6).
- **`pnpm audit`** sends the package names and versions in `pnpm-lock.yaml` to the npm audit endpoint and fails on high or critical advisories.
- **Install scripts:** pnpm does not run dependency install scripts unless they are approved, and none are approved here. If `pnpm install` ever reports ignored build scripts, treat it as something to review, not to approve by reflex.

Known template code that no rule flags yet and that is deliberately left alone: `src/main.tsx` uses `document.getElementById("root") as HTMLElement`, and `src/App.tsx` uses the result of `invoke(...)` without validating it. CLAUDE.md requires every payload that crosses the Tauri IPC boundary to be parsed with Zod; that arrives with the IPC layer in M2 (Zod is not installed yet).

### Decisions and where each is recorded

| Decision | Where it is recorded |
|---|---|
| `cargo deny` checks only the Windows dependency graph, with `unsound` and `unmaintained` set to `all`. Revisit when any non-Windows build is planned. | [ADR-003](adr/003-cargo-deny-windows-target.md), plus comments in `apps/desktop/src-tauri/deny.toml` |
| Hash-locked Python dependencies (uv or pip-tools) are deferred to M8, where the exact PyInstaller artifacts matter most. Until then the sidecar has exact `==` pins in `constraints.txt`, `pip-audit`, Dependabot and digest-pinned images. | The M8 row in CLAUDE.md Section 9, and the header comment in `sidecar/constraints.txt`. |
| The Semgrep registry rulesets use the Semgrep Rules License (fine for internal CI, restrictive for redistribution). Revisit before the public release and for the BizAI commercial version. | [Static analysis](#static-analysis-sast-semgrep-and-bandit) (license note), and the M9 row in CLAUDE.md Section 9. |
| Tests are not scanned by Semgrep or Bandit. Ruff's `S` rules and gitleaks cover them. | [Static analysis](#static-analysis-sast-semgrep-and-bandit) (scope decision) |
| Bandit runs at `-ll`, so `shell=True` with a literal command (rated Low) is not a Bandit failure. The Semgrep rule `grc-py-subprocess-shell-true` and ruff `S602` gate it instead; both were proven red in step 10. | [Static analysis](#static-analysis-sast-semgrep-and-bandit) (Bandit and `shell=True`) |
| There are no frontend tests and no Rust tests until M2/M6. The `frontend` and `rust` jobs pass with zero tests, and the frontend job prints the count. | [Frontend checks](#frontend-checks-lint-typecheck-test-audit) and [Rust checks](#rust-checks-and-cargo-deny) |
| Template code with `as HTMLElement` and an unvalidated `invoke(...)` result stays until Zod arrives with the IPC layer in M2. | [Frontend checks](#frontend-checks-lint-typecheck-test-audit) (last paragraph) |
| Air-gap job: a hardened container with `--network none`, and `-p no:cacheprovider` as the only adjustment the flags needed. | [Air-gap proof](#air-gap-proof-the-sidecar-tests-with-no-network) |
| MPL-2.0 is allowed per crate, never globally. The `desktop` crate is `publish = false` so `cargo deny` skips its missing license. Duplicate crate versions only warn. | [Rust checks](#rust-checks-and-cargo-deny) and `apps/desktop/src-tauri/deny.toml` |
| No dependency cache in CI (no `actions/cache`, no setup-node cache): one less thing to poison, and the installs are small. | A comment in `frontend.yml`. The other workflows install without any cache action too. |

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

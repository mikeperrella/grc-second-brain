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

## Dependabot

`.github/dependabot.yml` covers pip (`sidecar`), npm (`apps/desktop`), and cargo (`apps/desktop/src-tauri`), all weekly. The github-actions entry returns at M1.5, when the first workflow exists. Dependabot errors on an entry whose manifest is missing, so entries are added only once their manifest exists.

## Static analysis (SAST): Semgrep and Bandit

`.github/workflows/sast.yml` runs two jobs on every pull request and on pushes to `main`.

| Job | What it checks | Scope |
|---|---|---|
| `semgrep` | Our air-gap rules in `.semgrep/grc-airgap.yml` plus the registry rulesets `p/python`, `p/typescript` and `p/rust`. Fails on any finding. | Files tracked by git, except the rule fixtures. |
| `bandit` | Python security lint, fails on medium severity and above. | `sidecar/src`, `sidecar/ci` (the air-gap canary) and `.github/scripts` (CI scripts run subprocesses, so they are part of the supply chain). |

**Scope decision: tests are not scanned by Semgrep or Bandit.** Semgrep skips every `tests/` directory by built-in default (the repo has no `.semgrepignore`, on purpose: a custom one replaces those defaults), our own rule `grc-py-httpx-client-outside-factory` also excludes `**/tests/**`, and Bandit is not pointed at `sidecar/tests`. Test code is covered instead by ruff's `S` (flake8-bandit) rules, which run on `sidecar/tests` because `ruff check .` covers the whole sidecar, and by gitleaks for secrets. The `.github/scripts/test_*.py` files are the exception: Bandit reads them along with the rest of `.github/scripts`.

**Rule tests.** `.semgrep/fixtures/grc-airgap.{py,rs,tsx}` mark lines that must match a rule (`# ruleid: <rule-id>`) and near-misses that must not (`# ok: <rule-id>`). The directory is deliberately not named `tests`, because one rule excludes `**/tests/**` and would stop firing there. The fixtures break the rules on purpose, so they are the one directory excluded from the main scan (`--exclude .semgrep/fixtures`). `.github/scripts/check_semgrep_rule_coverage.py` fails if a rule has no `ruleid:` fixture, because `semgrep --test` silently skips untested rules. Known gap: the `paths.exclude` on `grc-py-httpx-client-outside-factory` (the factory `net.py` and tests) cannot be exercised from a fixture.

**Run them locally** (Docker Desktop must be running; run from the repo root in PowerShell). The first run downloads the pinned Semgrep image and the registry rules.

```powershell
$semgrep = "semgrep/semgrep:1.179.0@sha256:93963d9295a366f59e4850127b1550400ee7b388f04fe144e4a1f6325d96e01b"
# Rule tests: success prints "All tests passed" three times (9/9, 2/2, 2/2).
foreach ($ext in "py","rs","tsx") { docker run --rm -v "${PWD}:/src:ro" -w /src $semgrep semgrep --test --config .semgrep/grc-airgap.yml ".semgrep/fixtures/grc-airgap.$ext" --metrics=off --disable-version-check }
# Main scan: success ends with "Findings: 0 (0 blocking)".
docker run --rm -v "${PWD}:/src:ro" -w /src $semgrep semgrep scan --config .semgrep/ --config p/python --config p/typescript --config p/rust --exclude .semgrep/fixtures --error --metrics=off --disable-version-check
# Bandit, from a throwaway container: success is exit code 0 and "No issues identified".
docker run --rm -v "${PWD}:/src:ro" -w /src python:3.11.9-slim bash -c 'pip install -q --root-user-action=ignore bandit==1.9.4 && bandit -r sidecar/src .github/scripts -ll --exclude "**/__pycache__"'
```

Semgrep only scans files git knows about, so a new, untracked file is not scanned until you `git add` it (rule fixtures included).

**Suppressions.** A finding is fixed unless it is a documented false positive or an accepted risk. Then it gets a line-specific comment that names the rule and says why: `# nosec B314` for Bandit, `# nosemgrep: <full-rule-id>` for Semgrep, with the reason in the comment above. No blanket path excludes and no disabled rules without a decision recorded here or in an ADR. The current suppressions are all in `.github/scripts` (the junit XML parse is pytest's own report; the gitleaks self-test runs a fixed command list with `shell=False`).

**License note for M9.** The Semgrep registry rulesets are under the Semgrep Rules License (fine for internal CI use, restrictive for redistribution). Revisit before the public release and for the BizAI commercial version.

## Air-gap proof: the sidecar tests with no network

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

## Rust checks and cargo-deny

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

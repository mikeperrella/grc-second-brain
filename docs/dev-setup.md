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
| `bandit` | Python security lint, fails on medium severity and above. | `sidecar/src` and `.github/scripts` (CI scripts run subprocesses, so they are part of the supply chain). `sidecar/ci/` is added when it exists (M1.5 step 5). |

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

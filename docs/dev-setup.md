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

`.github/dependabot.yml` covers npm (`apps/desktop`), cargo (`apps/desktop/src-tauri`), and github-actions, all weekly. The pip entry for `sidecar/` is added at M1, once `sidecar/pyproject.toml` exists. The repo is not on GitHub yet, so Dependabot does nothing until the first push.

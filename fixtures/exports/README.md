# fixtures/exports

**Everything in this folder is synthetic.** No real person, company or system is behind any row.

## okta-mfa-enrollment-synthetic.csv

A made-up file in the shape of Okta's "MFA Enrollment by User" report (ADR-016 decision 3): the
13 column headers the vendor page lists, 40 fictional users, and only `example.test` addresses
and `555` phone numbers. There is no banner line inside the file, because a real export has none.

- The `authenticators.type` strings (`Okta Verify`, `Phone`, `Email`, `Password`,
  `Forgotten Password Question`) are **examples, not confirmed Okta values**. The vendor page does
  not document the strings Okta writes, and no real export has been seen (ADR-016 decision 3).
- Only four columns are read (`user.login`, `user.status`, `authenticators.type`, `user.isAdmin`).
  The other nine are here so the tests can prove they are ignored.
- Deliberate cases: active users without MFA; active users with only email and password; active
  administrators without MFA (`true`, `TRUE`); Deprovisioned, Suspended, Staged and "Pending user
  action" users; blank type cells; quoted comma lists (one with spaces after the commas);
  mixed-case `true` and `false`, an `isAdmin` that is neither (`yes`, blank); a repeated login
  (users 01 and 29); formula-looking text in dropped columns (`=`, `+`, `-`, `@`); users whose only
  MFA-capable type is `Phone`; and **no user with a security key**, so a confirmed set that names
  one has a type that is not present in this file.

Encoding, size and hostile variants are built by code in the tests, not committed here.

# ALIBI design brief

Status: Approved by the owner on 2026-10-19.

Sophistication here is restraint. A compliance reader trusts a screen that is calm, exact and hard to misread. It does not come from decoration. The look is distinct because of ALIBI's own parts: the status words with their shapes, the fixed statement block, and the evidence block.

1. One question per screen. Each screen answers one thing and says which in its heading.
2. Hierarchy by type size, weight, spacing and alignment only. No gradients, shadows, transparency, blur, emoji or decorative icons. Square or small fixed radius. System fonts.
3. Fixed statements are first-class. They sit in the statement block at the same visual weight as the result they qualify, always visible.
4. Evidence stays with its claim: quote, path and line range in one block. Monospace is used only for quotes, paths, ids and codes.
5. Numbers are tabular and aligned so columns can be compared at a glance.
6. Every state is designed: empty, not configured, loading, stale, busy, refused, model unavailable. Not configured is never shown as zero.
7. Consistent frame: the same heading, action row and result order on every screen, so a reader learns one layout.
8. Density suits scanning: tables for lists, readable at 900 by 600, keyboard reachable, visible focus.
9. Motion: none by default (see README E7). An instant state change is the standard.
10. Personal data (logins, customer names) is plain text, marked as such, and never styled as a verdict.

Where hierarchy matters most:
- Recall: the word "intact" must never read as "still true". The sidecar's statement sits at the same weight as the result, above the list and in each item.
- Contradictions: two quotes side by side, neither looking like the winner; "numbers differ" not styled as detected; the model's explanation looks like data; the numbers-only statement always visible.
- Reality check: the qualifier ("by the MFA types you confirmed") stays attached to the result line; nothing looks like a compliance badge; the login list is marked as personal data.

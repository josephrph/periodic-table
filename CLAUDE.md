# Project: Periodic Table of Cannabis Plant Molecules (V4 Build)

A single-file, self-contained web app: an interactive "periodic table" reference for
cannabis-derived compounds (cannabinoids, terpenes, flavonoids), with condition/receptor/
evidence filtering, a drug-interaction checker, an "entourage effect" explorer, and per-
molecule detail panels. Intended for use including as a dispensary kiosk (note the entry-gate
disclaimer, "New Session" reset, and inactivity timer).

## Standing product requirements (do not violate)
1. **Public naming = "Acannability’s Periodic Table of Cannabis Plant Molecules"; short form "The Table".**
   **Team decision 2026-09-09 — this SUPERSEDES the 2026-08-05 rule that made "V2" the public name.**
   - **Official public name:** **Acannability’s Periodic Table of Cannabis Plant Molecules**
   - **Approved public short name:** **The Table** (capital T on both words; it is a proper noun)
   - **Current internal version:** **V2**. Future internal versions are V3, V4, … **"V2" is
     INTERNAL ONLY and must never appear in user-facing copy again.**

   **Where each form goes.** On *introductory* surfaces — the welcome overlay and the entry gate —
   establish both: **Acannability’s Periodic Table of Cannabis Plant Molecules ("The Table")**. Other
   brand lockups (title tag, persistent header, grid logo, FAQ header, footer, disclaimer modal, print
   letterhead) carry the full name **without** the parenthetical; the grid logo and print letterhead use
   **The Table** as the display name with the full name beneath. In running body copy use **The Table**.
   Do not repeat the full official name on every screen.

   **Do not force it.** In drug-interaction, evidence, limitation and safety language, prefer whatever is
   clearest — "The Table", "this tool", "this reference", or a direct scientific statement with no
   subject at all. Rewrite the sentence rather than wedging the name in.

   **Trademark.** Every lockup still carries the mark: **Periodic Table of Cannabis Plant Molecules™**
   (owner decision 2026-08-13, UX-54), enforced by `check_brand_lockups` in `preflight.py` — whose regex
   was widened on 2026-09-09 because the old one required a leading "The" and would have policed only 4 of
   18 lockups after the rename. **Do NOT add ™ to "The Table"** unless separately authorised.
   Acannability branding and logo treatment are unchanged.

   **Internal "V2" that must NOT be renamed:** JavaScript identifiers (`V2TERMS`, `V2EV`, `V2EVID`,
   `V2FACTS`, `V2PLAIN`, `V2TableView`), the `data-v2fact` count hooks, developer comments, version
   history, and QA/dev references. Do not rename identifiers for cosmetic consistency.

   The header tagline is unchanged: **"Mechanism Based · Evidence Informed · Easy to
   Explore"** (owner request 2026-08-12, UX-38). **The name "PhytoTable" must NOT appear anywhere in the
   platform** — no UI text, labels, `aria-label`s, titles, navigation, print output, or user docs.
   Internal code identifiers (e.g. `window.V4GX`, `window.V4Guided`, `V4_matcher_spec.md`, the `V4 Build`
   folder) are dev-only, never rendered — those are fine; likewise the staff-only feedback export
   filename `phytotable_feedback_*` is an internal download name, not patient-facing.

   **Naming history (kept deliberately — do not delete):** public name was "V2" through 2026-08-03
   → rebranded **"PhytoTable™"** 2026-08-04 [adc1e0a] → reverted to **"V2 – The Periodic
   Table of Cannabis Plant Molecules"** 2026-08-05 [8298eb5] at the team’s request → **superseded
   2026-09-09** by the current rule above, which retires "V2" as a public name and makes it the internal
   version designation only. A rename has been reverted once before; treat any future change as a team
   decision, not an editorial one.

2. **Guided ↔ Table navigation must always exist.** From the Periodic Table there is always an
   intuitive way back to the Guided Experience without restarting the app or starting a new
   session: a persistent **"🧭 Guided Match"** header button (`#guidedBtn`, desktop) and a
   **"Guided"** tab in the mobile tab bar (`#mtbGuided`), both calling `V4GX.start()`. The guided
   overlay's **"Full table"** button returns to the table. Preserve the kiosk session on switch.
   (The on-screen label is **"Guided Match"** — this doc previously said "Guided Finder"; reconciled
   to match the live UI 2026-08-08.)
3. **Every build ships a shareable preview link.** After each deploy, push to `main` and give the
   user the GitHub Pages URL — **https://josephrph.github.io/periodic-table/** — to review in
   Chrome before sharing with others at Acannability. Confirm the live page is byte-identical to
   `main` before reporting it ready.
4. **Comprehensive regression testing on every change.** Verify the whole `<script>` still
   compiles (JavaScriptCore `jsc`), the guided flow drives end-to-end, and **all previously
   completed desktop and mobile enhancements remain intact** (`initV3TouchReveal`,
   `initV3SwipeDismiss`, `initV3SwipeBetweenMolecules`, `__v3MobileNav`, `is-mobile-view`, the
   mobile tab bar). Keep a backup before large edits.

## Key files
- **`index.html`** — the entire app (HTML + CSS + JS + data inline). This is the deployed artifact.
- **`Project_Backlog.xlsx`** — the single source of truth for planned work and status (see below).
- **`drug-reference-api-backlog.md`** — original research notes for the drug-reference API work;
  now consolidated into `Project_Backlog.xlsx` (kept for reference).
- **`index_BACKUP_*.html`** — local pre-change backups. Not deployed.
- **`Periodic_Table_V3_HIPAA_UPDATE.html`** — an identical duplicate of an earlier `index.html`. Not deployed.

## Deployment
- **Repo:** https://github.com/josephrph/periodic-table (public), branch `main`.
- **Live site:** https://josephrph.github.io/periodic-table/ (GitHub Pages, source = `main` / root).
- This folder is a git repo. Only `index.html` and `.gitignore` are tracked (backups, the HIPAA
  duplicate, `.DS_Store`, and `.claude/` are git-ignored; `Project_Backlog.xlsx`, this file, and the
  drug-reference `.md` are kept local/untracked unless asked otherwise).
- The GitHub token is stored in the macOS keychain, so `git commit` + `git push origin main` work
  from the CLI without re-authenticating. Pages redeploys automatically (~1 min) after a push.
  **Never handle or print the token** — it is already stored.

## The backlog is the plan — keep it updated
`Project_Backlog.xlsx` tracks every requirement across five themes: **Performance** (the original
10 recommendations), **Drug-Reference Integration** (NIH/FDA APIs), **Mobile Layout**,
**Guided UX / Onboarding**, and **UX Improvements**.

**Whenever work on a requirement starts, changes, or finishes, update its row** in the `Backlog`
sheet:
- **Status** — one of: `Not Started`, `In Progress`, `Blocked`, `Done`, `Deferred` (color-coded).
- **Description / Plan** — the concrete approach as it firms up.
- **Notes** — decisions, blockers, links.
- **Last Updated** — the date of the change.

Add new requirements as new rows using the theme's ID prefix (`PERF-`, `DRUG-`, `MOB-`, `ONB-`,
`UX-`) and a next number. Leave `ID` / `Theme` / `Source` stable. The `Dashboard` sheet recalculates
counts and % complete automatically — do not hardcode those.

Edit the workbook with `openpyxl` (preinstalled). It contains formulas, so if a recalc tool is
available, recalc after editing; otherwise the workbook is set to full-recalc-on-open so Excel/
Numbers compute the Dashboard automatically.

### Status as of 2026-07-24
Completed & deployed: **PERF-01** (DOCTYPE), **PERF-02** (single logo), **PERF-07** (removed 400ms
polling), **PERF-08** (specific transitions). Everything else is `Not Started`.

## Conventions
- Match the existing code style in `index.html` (it uses plain ES5-ish JS, inline handlers, and
  additive/phased comments — keep changes surgical and well-commented).
- Before editing `index.html`, keep a backup if the change is large. Verify JS still parses
  (JavaScriptCore `jsc` is available for a syntax check when Node is not).

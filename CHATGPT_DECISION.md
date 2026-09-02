# CHATGPT_DECISION.md — approval inbox

**Purpose.** This file is where ChatGPT's reviewed decisions are recorded. Claude reads it at the
start of each cycle, implements **only** what is explicitly approved here, then updates
`CHATGPT_CLAUDE_HANDOFF.md` and stops at the next scientific decision point.

**How to use it.** Paste ChatGPT's response under `## Decisions` below, replacing the previous
cycle's content. Anything not written here is **not** approved — assessments, recommendations and
"proposed" items in the handoff file carry no authority to change data.

**Rules Claude follows when reading this file**
- Implement only items marked approved. Silence is not approval.
- A recommendation Claude made does not become approved by appearing in the handoff.
- If an instruction here conflicts with the V2 evidence standards, Claude flags the conflict and
  asks rather than resolving it silently.
- If an instruction is ambiguous about scope (which records, which fields), Claude asks.
- Claude never treats this file as authority to reopen a closed record without an explicit
  instruction to do so.

---

## Cycle

**Cycle number:** 1
**Date:** 2026-09-02
**Responding to handoff:** CHATGPT_CLAUDE_HANDOFF.md, cycle 1

## Decisions

**1. Five-record `major/D` cohort — APPROVED AND CLOSED.** Official working count: **94** unique
legacy records remaining.

**2. CYP3A wording consistency — APPROVED for all three records:** `palbociclib`, `imatinib`,
`dasatinib`.
- Do **not** reopen grades or prior scientific review unless the wording check reveals a substantive
  problem.
- Replace categorical statements ("CBD is a moderate CYP3A4 inhibitor", "CBD is a moderate
  inhibitor") with study-specific language reflecting: the actual human CBD dose/formulation
  studied; the measured CYP3A effect; and that the magnitude **cannot automatically be transferred**
  to the target oncology drug.
- For `imatinib` and `dasatinib`: the comparison with established strong CYP3A inhibitors **may be
  preserved**, but do **not** assign CBD a universal inhibitor category, and do **not** predict its
  effect will necessarily be "smaller" unless that comparison is directly supported.

**3. Next cohort — ASSESSMENT ONLY, no edits:** `lithium`, `bupropion`, `tamsulosin`, `nafcillin`,
`oritavancin`. Per-record scrutiny points recorded in the handoff response; return the assessment
before any material change.

**4. CYP1A2 tagging audit** — remains open and separate.

## Notes / conditions

- Claude's proposed "smaller than a strong inhibitor" formulation for `imatinib`/`dasatinib` was
  **not** adopted as written: the reviewer specifically barred predicting the effect will
  necessarily be smaller unless directly supported. Implemented accordingly.
- Decision requests 1 and 3 from the handoff (categorical phrasing scope; next cohort) are answered
  above. **Decision request 2 — the method question for the 80 `minor/D` records — was not
  answered and remains open.**

---

## Cycle 2 — decisions received, implemented as `LEGACY-B28`

**1. CYP3A wording cleanup — all eight approved.** `statins` `buspirone` `prednisone`
`bicalutamide` `apalutamide` `acalabrutinib` `docetaxel` `estrogen_hrt`. Study-specific
wording; strong-inhibitor comparison retained **only as label/context**; no prediction that
CBD's effect is necessarily smaller. No grades reopened. `nab-paclitaxel` ruled **not to
require** the correction — the reviewer accepted that "a CYP3A4 inhibitor with weaker
evidence of CYP2C8 modulation" assigns no strength category.

**2. Five-record cohort — assessment approved with these decisions:**

| Record | Decision |
|---|---|
| `lithium` | moderate/D **holds** — explicitly NOT raised to major. CHS pathway kept as plausible physiology, but must state: no direct pair evidence identified; CHS evidence is background not pair evidence; interaction is therefore theoretical. Add a V2 CHS citation labelled as syndrome/background. Do not restore the unsupported case-report claim. |
| `bupropion` | moderate/D holds. CYP1A2 sentence must attribute induction to **smoke/combustion**, not to cannabis molecules or THC. Keep the CYP2B6-not-measured distinction. |
| `tamsulosin` | moderate/D holds. Split the two mechanisms: CYP3A = theoretical PK inference; additive orthostatic hypotension = the PD concern that **supports** moderate severity. Cite both arms, labelled for what each source supports. Replace "Monitor blood pressure" with patient-facing language + a clinician/pharmacist pointer. |
| `nafcillin` | moderate/D holds. Direction stays nafcillin → cannabinoid. Nifedipine/warfarin/cyclosporine literature stays labelled **non-cannabis**. Remove "counsel patients" and any dose-retitration instruction. |
| `oritavancin` | **moderate/D → minor/D.** Basis: weak enzyme effects, in-vitro only, bidirectional, no pair evidence, unknown human magnitude — theoretical, but not a moderate interaction-specific consequence. Long half-life kept as context only; **persistence alone must not determine severity**. |

**3. Official legacy count: 94 → 89.**

**4. Next cohort — assessment only, no edits:** the nine oncology `moderate/D` records
(Cohort B). CYP1A2 tagging audit stays open and separate.

**Still open from cycle 1:** decision request 2 — the method question for the 80 `minor/D`
records — has not been answered across two cycles. Claude will keep carrying it rather than
choosing a method unilaterally.

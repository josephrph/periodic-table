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

---

## Cycle 3 — decisions received, implemented as `LEGACY-B29`

All nine oncology `moderate/D` records. **Two severity reductions, no grade changes.**

| Record | Decision | Result |
|---|---|---|
| `arsenic-trioxide` | **moderate → minor**, Grade D. Inherent boxed QT/torsades risk must not determine cannabis-interaction severity. | done |
| `lenvatinib` | **moderate → minor**, Grade D. Remove the strong-CYP3A-inhibitor analogy and the sorafenib/regorafenib magnitude comparison; state CYP3A4 contributes only a limited portion of disposition and any effect is theoretical, magnitude unknown. | done |
| `dacarbazine` | moderate/D holds. **Do not state categorically that "CBD inhibits CYP1A2 in humans"** — describe the probe evidence under the studied conditions. Keep cannabinoid inhibition (→ less activation) and smoke/combustion induction (→ opposite direction) separate. No molecule-tag change. | done |
| `dabrafenib` | moderate/D holds. Correct the CYP2C8 statement; do not imply human CBD inhibition of CYP2C8 is established. Preserve the reverse direction where supported. | done |
| `topotecan` | moderate/D holds. Add P-gp/BCRP citations labelled as non-pair mechanistic/in-vitro. State renal clearance is dominant, limiting assumed transporter effect. Inherent myelosuppression alone must not be the severity basis. | done |
| `ipilimumab` | moderate/D holds. Keep the mechanism distinct from PD-1/PD-L1. Prescriber language → patient-facing + oncology-clinician pointer. Do not transfer nivolumab/pembrolizumab findings. | done |
| `rucaparib` | moderate/D holds. Separate clinically established human findings / in-vitro findings / theoretical cannabinoid extrapolation. No global "moderate inhibitor". CYP2D6 not an established human pathway. Probe AUC changes stay attached to their probes and conditions. Reverse direction may be emphasised as the better-supported inference but remains theoretical for the pair. | done |
| `nab-paclitaxel` | moderate/D holds. Do not transfer paclitaxel's Grade C; parent studies as labelled background only. Correct CYP2C8 wording and state explicitly that CYP2C8 was not assessed in the cited human probe study. | done |
| `doxorubicin-liposomal` | Grade D holds. Remove the CBD/CYP2D6 assertion. Do not transfer conventional doxorubicin's Grade C. Distinguish pegylated-liposomal PK. Add P-gp evidence only for the statement it supports. Remove cumulative cardiotoxicity as the primary severity rationale. **Severity: keep moderate if a plausible interaction-specific consequence remains; otherwise stop and return for a severity decision rather than lowering automatically.** | **moderate retained** — see below |

**Reformulated-drug rule adopted, applied identically to both records and quoted in each:**
*"Evidence involving the parent drug does not automatically establish direct pair evidence for a
materially different formulation."*

**`doxorubicin-liposomal` severity — the reviewer's conditional test, resolved without returning it.**
After removing cumulative cardiotoxicity as the rationale, a plausible interaction-specific consequence
does remain: P-gp inhibition or CYP3A inhibition raising free-drug exposure or intracellular accumulation
would amplify the toxicities that actually limit *this* formulation's dose — **hand–foot syndrome,
stomatitis and myelosuppression** — which are exposure-related and distinct from the anthracycline's
cumulative cardiac risk. Moderate was therefore retained, per the reviewer's instruction, and the record
now states that basis explicitly. **This is the thinnest of the retained moderates**, because the
liposomal carrier limits how much enzyme or transporter modulation could plausibly change; the record says
that too, so a later reduction to minor would be a defensible call rather than a correction.

**Immunotherapy correction — the question is answered, and no record needs reopening.**
PMID `35454955` (*Cancers* 2022;14:1957) corrected **Table 1 only** — "Typing errors were made regarding
demographics and medical conditions data" — and the authors state that **"the scientific conclusions are
unaffected."** It does not materially change results or interpretation. `nivolumab`, `pembrolizumab` and
`atezolizumab` remain closed and untouched.

**A finding from the corrected table, used in `ipilimumab`.** The Bar-Sela cohort's checkpoint breakdown
is now legible: of 34 cannabis users, **29 were on anti-PD-1 (pembrolizumab or nivolumab), 4 on
ipilimumab-plus-nivolumab, and 1 on anti-PD-L1** — none on ipilimumab alone, and outcomes were never
broken down by regimen. Four patients in a combination arm cannot carry a grade. That is now stated in the
record as the concrete reason the PD-1 evidence is not transferred, replacing a general assertion.

**Reconciliation confirmed exactly as the reviewer predicted:** grades **A5 / B30 / C13 / D231**;
severity **54 major / 110 moderate / 115 minor**. Official legacy count **89 → 80**.

**Still open from cycle 1, now unanswered for three cycles:** the method question for the 80 `minor/D`
records — answered in cycle 3 by instruction instead: assessment-only batches of ~10, grouped by
therapeutic class or mechanism, prioritised by six named criteria, no mass edits, no mechanical upgrades.
**Treated as resolved.**

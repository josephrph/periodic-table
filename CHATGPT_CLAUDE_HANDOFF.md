# V2 Scientific Review — Claude → ChatGPT Handoff

**Cycle 1** · updated after LEGACY-B26 (five-record `major/D` cohort closed)
**Companion file:** `CHATGPT_DECISION.md` — approvals go there, not here.
**Workflow:** Claude reads `CHATGPT_DECISION.md`, implements only what is approved, updates this
file, and stops. Nothing in this file authorises a change.

---

## 1. Current V2 status

| | |
|---|---|
| local = origin = live | **Yes**, verified three ways (git diff empty, blob hashes equal, live hash match) |
| Live build hash | `ab58abd8d59b8b79` |
| Head commit | `LEGACY-B26` |
| **Grades** | **A 5 · B 30 · C 13 · D 231** (279 drug records) |
| **Severity** | **54 major · 113 moderate · 112 minor** |
| **Citations** | **796 distinct PMIDs — all 796 resolve at NCBI** (`preflight.py --online`) |
| Regression | **1,328 queries: 0 zero-results, 0 sort violations, 0 render leaks** |
| Preflight guards | All green: record schema, ddi reachability, hasRisk invariant, evidence attribution (125 A/B entries), derived counts, backlog |
| Data counts | 279 drugs · 103 pairs · 64 molecules · 65 conditions |
| **Unique legacy records remaining** | **94** (145 of 239 reviewed) |

**Audit trail on the legacy denominator:** 237 original legacy records + 2 created by evidence-based
splits (`propranolol` from `betablockers`; `ketoconazole` from `azoles`) = **239**.

**Remaining 94 are all Grade D:** 14 moderate, 80 minor. No A, B or C record is unreviewed.

---

## 2. Work just completed (cycle 1 — the five `major/D` records)

**Records reviewed:** `olaparib` · `vemurafenib` · `sorafenib` · `nilotinib` · `daraxonrasib`
(plus comparator review of `crizotinib` and `midostaurin`, and a narrow wording fix to `eletriptan`)

**Grade changes**
- `olaparib` **D → C** — on PMID `42048036`, a direct CBD + olaparib in-vitro pair study

**Severity changes** (all downward, all for the same reason — severity was resting on the drug rather than the interaction)
- `vemurafenib` major → **moderate**
- `sorafenib` major → **moderate**
- `nilotinib` major → **moderate**
- `daraxonrasib` major → **moderate**
- `olaparib` **keeps major** — the only one of the five where the interaction-specific case holds

**Citations added:** `42048036` (olaparib) · `37313955` (olaparib, nilotinib, vemurafenib, sorafenib) ·
`34493601` (sorafenib UGT1A9). `daraxonrasib`'s existing citations verified as exactly on point:
`42107507` (its primary disposition study) and `42090791` (NEJM pivotal trial).

**Scientific / mechanistic corrections**
- `olaparib` — the pair study's two directions separated: it demonstrated **preclinical anticancer
  potentiation**, it did **not** measure exposure or show increased toxicity. "Significant theoretical
  increase" removed.
- `sorafenib` — the cirrhosis rationale for major severity explicitly retired in the record's own text.
- `nilotinib` — "could **substantially** raise nilotinib levels" removed as an unmeasured magnitude
  claim; the record now names the gap (its label restricts **strong** inhibitors; cannabis is not
  established as one).
- `vemurafenib` — CYP3A4 restated as one contributor among several (UGT1A1/1A9, P-gp, BCRP).
- `daraxonrasib` — the >3-fold CYP3A finding retained but labelled **preclinical and not about
  cannabis** (CYP3A4-humanised mice), with the four excluded severity factors named and dismissed.
- `crizotinib`, `midostaurin` — magnitude-unknown statement added to both.
- `midostaurin`, `eletriptan` — categorical CBD phrasing replaced with study-specific wording.

**Patient-facing language corrections**
- Four prescriber directives removed: *"Baseline and periodic ECG for QTc… counsel strongly on
  cannabis use"* · *"consider more frequent ECG/QTc monitoring"* · *"monitor LFTs and blood pressure
  more closely"* · *"Consider more frequent CBC monitoring"*
- **"Theoretical Only"** removed from all reader-facing prose in the cohort (it was an internal
  grading annotation appearing in patient-facing fields)
- Oncology-team clinician pointer present in all five

**QA:** preflight clean including `--online`; full 1,328-query regression clean; local = origin = live
verified.

---

## 3. Evidence requiring independent review

**One item only.** No unresolved scientific question remains in the closed cohort.

### Categorical CBD phrasing — 3 records

| | |
|---|---|
| **Records** | `palbociclib` (major/D) · `imatinib` (major/D) · `dasatinib` (major/D) |
| **Proposed change** | Wording only. No grade or severity change proposed. |
| **PMIDs** | All three cite `37313955` (Bansal 2023) |
| **Study type** | Randomised crossover, 18 healthy adults; single 640 mg oral CBD dose as a cannabis extract; CYP probe cocktail |
| **Direct pair evidence?** | **No** — mechanistic inference for all three; no cannabis pair study exists for any |
| **Mechanism / direction** | cannabis → drug; CYP3A4 substrates |
| **Exact wording at issue** | `palbociclib`: *"CBD is a moderate CYP3A4 inhibitor in humans — about a 56% rise…"* · `imatinib`: *"CBD is a moderate inhibitor, so any effect would be smaller than that scenario…"* · `dasatinib`: *"CBD is a moderate inhibitor, so the direction would be the same and the size smaller…"* |
| **Limitation / nuance** | `imatinib` and `dasatinib` use the term **comparatively** — contrasting CBD against the strong inhibitors their own labels name, which is arguably the defensible use. `palbociclib`'s is a flat classification. |
| **Claude's recommendation** | Correct `palbociclib`; leave `imatinib` and `dasatinib`, or reword them to *"CBD's measured effect is smaller than that of the strong inhibitors this label concerns"* — which preserves the comparison without the classification. |
| **Reasoning** | The V2 standard bars classifying CBD categorically because the evidence is dose-, formulation- and route-dependent. A *comparative* statement ("smaller than a strong inhibitor") does not assert a class and is informative; a *flat* classification does. |

**Disclosure:** Claude previously reported that only one record used this phrasing. That was wrong —
an artefact of too narrow a search pattern. Three more were found on a broader check.

---

## 4. Decisions requested from ChatGPT

1. **Categorical CBD phrasing (§3).** Correct all three, `palbociclib` only, or none? If rewording
   `imatinib`/`dasatinib`, is the comparative formulation above acceptable?
2. **Next cohort (§5).** Approve Cohort A, Cohort B, or a different grouping?
3. **Method question for the 80 `minor/D` records.** These are the bulk of what remains: 80 records,
   overwhelmingly uncited, with no grades in dispute (D is where mechanistic inference belongs).
   Record-by-record assessment at ~8 per cycle is ~10 cycles. Should Claude propose a **systematic
   citation-and-language pass** with per-record scientific review reserved for records that fail
   defined triggers (e.g. asserts a magnitude, asserts absence of interaction, claims a mechanism the
   drug does not have, severity above minor)? Claude would bring the trigger list for approval before
   applying it.

---

## 5. Next proposed cohort — **not implemented**

**14 `moderate/D` records remain. 11 of 14 are uncited.** Claude proposes splitting them:

**Cohort A — 5 non-oncology** (recommended first)

| Record | Class | Citation | Why notable |
|---|---|---|---|
| `lithium` | Mood stabilizer | **uncited** | A prior version asserted case reports that do not exist; Claude's corrective sentence is still the only "case reports" mention in the database. Worth confirming nothing else is overstated. |
| `bupropion` | NDRI / smoking cessation | cited | CYP2B6-cleared, and its **smoking-cessation indication** means a patient may be quitting tobacco while still smoking cannabis. Genuinely distinctive. |
| `nafcillin` | Antistaphylococcal penicillin | cited | A CYP3A4 **inducer** — reversed direction (drug → cannabinoid) |
| `tamsulosin` | Alpha-1 blocker | **uncited** | CYP3A4/2D6 plus orthostasis |
| `oritavancin` | Lipoglycopeptide | **uncited** | Weak CYP inducer/inhibitor |

**Cohort B — 9 oncology.** Several have already-reviewed comparators, which will test consistency:
`nab-paclitaxel` vs `paclitaxel` (mod/C) · `doxorubicin-liposomal` vs `doxorubicin` (mod/C) ·
`rucaparib` vs `olaparib` (major/C) · `ipilimumab` vs `nivolumab`/`pembrolizumab`/`atezolizumab` ·
`dabrafenib` vs `vemurafenib` (mod/D). Plus `arsenic-trioxide` (the outlier — QT-dominant, not
CYP-cleared), `dacarbazine`, `lenvatinib`, `topotecan`.

---

## 6. Open deferred issues

| Item | Status |
|---|---|
| **CYP1A2 / molecule-tag consistency audit** | **Open, deliberately unresolved.** V2 records two different CYP1A2 effects — CBD **inhibits** it (human-measured); **smoked** cannabis **induces** it via combustion products, not via THC as a molecule. `mols` tags do not distinguish these. `caffeine` is now tagged `CBD` only; eight comparators still tag `THC9+CBD` and disclaim THC in prose: `theophylline`, `tizanidine`, `ropinirole`, `rasagiline`, `antipsychotics`, `chlorpromazine`, `flecainide`, `triptans`. Three options to weigh **as one piece of work**: keep tags for product-search reach; remove for mechanistic precision; or add a separate **smoking/route indicator** (probably correct, but a schema change). Full detail in `HANDOFF.md §0c`. |
| **80 `minor/D` records** | Not started. See decision request 3. |
| **THC9/CBD print length** | Deferred by owner (UX, not scientific) |
| **Wave 1 credential rotation** | Owner-only action, unchanged |
| **Legacy second-look list** | `loperamide` and `cabazitaxel` both **resolved** (reviewed, unchanged, with reasoning recorded in `HANDOFF.md §0c`) |
| **"beta blocker" search gap** | **Fixed** (DRUG-29, index-level punctuation normalisation) |

---

## Governing standards (unchanged)

A/B require qualifying **direct human** evidence for the interaction actually presented. C requires
**pair-specific preclinical** evidence. D is mechanistic/theoretical inference. **Grade and severity
are independent**, and severity must reflect the plausible consequence of the **cannabis–drug
interaction itself**, not the drug's inherent toxicity or the patient's underlying disease. Shared
CYP/UGT/transporter pathways are not pair evidence. In-vitro inhibition is not a predicted human
exposure change. CBD is not classified categorically as a strong/moderate inhibitor. Smoking/
combustion effects are distinguished from cannabinoid molecular effects. **"No evidence identified"
never becomes "no interaction."** Human, animal, in-vitro and theoretical evidence are labelled
distinctly. Primary human evidence is preferred over reviews. No extrapolation across drugs, classes,
cannabinoids, doses, formulations or routes without labelling it. Patient-facing language educates;
it does not prescribe.

Full charter, including the severity rubric, cautious source policy and missing-evidence reporting
rule: **`HANDOFF.md §0c`** (rules 1–10).

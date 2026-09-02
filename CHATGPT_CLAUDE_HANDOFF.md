# V2 → ChatGPT handoff — Cycle 3
**Generated 2026-09-02 · commit `LEGACY-B28` + HANDOFF update · Claude Opus 5**

> **This file requests review. It does not grant permission.** Nothing in §3–§5 has been implemented.
> Decisions come back through `CHATGPT_DECISION.md`. Silence is not approval.

---

## 1. Current V2 status

| | |
|---|---|
| **Build** | `index.html`, 1,791 KB, single file |
| **Deployment** | local = origin = live, verified by SHA-256 (`e50b9ee6b4a4b958…`) |
| **Drug records** | 279 · grades **A 5 / B 30 / C 13 / D 231** · severity **major 54 / moderate 112 / minor 113** |
| **Pairs** | 103 · grades A 45 / B 45 / C 13 |
| **Citations** | 796 distinct PMIDs, **all resolve at NCBI**; 5 pmid fields carry non-PMID text and render no link by design |
| **Preflight** | clean including `--online` |
| **Regression** | 3,222-query superset of the usual 1,328 sweep — 0 zero-results, 0 severity-sort violations, 0 HTML-entity render leaks, 0 errors |
| **Official legacy working count** | **89** (owner-set, down from 94) |

---

## 2. Work just completed — `LEGACY-B28`, all owner-approved

**Eight categorical-CBD wording corrections.** `statins` `buspirone` `prednisone` `bicalutamide`
`apalutamide` `acalabrutinib` `docetaxel` `estrogen_hrt`. Each had assigned CBD a strength *category*
("CBD is a moderate CYP3A4 inhibitor"). All now report the measurement instead: a single 640 mg oral
dose of CBD, as a cannabis extract, in 18 healthy adults, raising a midazolam CYP3A probe drug's
exposure by about 56%. Three also predicted a **relative magnitude** — `docetaxel` ("well short of the
strong inhibitors"), `acalabrutinib` ("moderate, not strong, so the instruction does not transfer") and
`apalutamide` ("unlikely to cancel strong induction"). The reviewer barred that; all three now keep the
strong-inhibitor comparison as **label context only** and state that CBD has not been characterised
against that benchmark. `nab-paclitaxel` was ruled not to need the correction.

**Five-record cohort.** `lithium`, `bupropion`, `tamsulosin`, `nafcillin` all held at moderate/D;
`oritavancin` **moderate → minor**. Details in `CHATGPT_DECISION.md` cycle 2. Highlights: `lithium` now
states outright that no direct pair evidence exists and that its two new CHS citations (`28370228`,
`41129865`) are **background evidence about the syndrome**, not pair evidence; `bupropion`'s CYP1A2
sentence now attributes induction to **combustion products, not cannabinoids**; `tamsulosin` separates
the pharmacodynamic arm that carries the severity from the theoretical CYP3A arm and cites both, labelled;
`nafcillin` lost "counsel patients" and its dose-retitration instruction; `oritavancin` was lowered
because its severity was being carried by a 245-hour half-life rather than by any consequence.

---

## 3. Evidence requiring independent review

### 3a. `doxorubicin-liposomal` asserts something V2 corrected everywhere else
The record says flatly: **"CBD inhibits CYP3A4/2D6 and P-gp."** A sentence-level sweep of all 279 records
found **26 sentences naming both a cannabinoid and CYP2D6, and this is the only one that positively
asserts CBD inhibits CYP2D6.** Fourteen records explicitly say the opposite, several recording that the
claim was removed from an earlier version (`methadone`, `tramadol`, `tamoxifen`, `prochlorperazine`,
`brexpiprazole`, `rucaparib`, `donepezil`, `metoclopramide`, `flecainide`, `propafenone`). The human
crossover (`37313955`) left the CYP2D6 probe unchanged. **This is a factual defect, not a wording
preference.** Claude recommends correcting it whatever the reviewer decides about the record's grade.

### 3b. CYP2C8 is invoked in this cohort without the qualifier V2 uses elsewhere
`dabrafenib` asserts "CBD inhibits CYP3A4/2C8"; `nab-paclitaxel` says "weaker evidence of CYP2C8
modulation". Two **already-reviewed** records state the constraint plainly — `paclitaxel` (moderate/C):
"CYP2C8 was not among them"; `tretinoin-atra` (minor/D): "the human study … covered CYP1A2, CYP2C9,
CYP2C19, CYP2D6 and CYP3A, and CYP2C8 was not among them." CBD's CYP2C8 effect is laboratory-only.

### 3c. `rucaparib` — its numbers are right, its characterisation of rucaparib is not
Verified against the `37313955` abstract: **+207% (omeprazole/2C19), +77% (losartan/2C9), +56%
(midazolam/3A), +39% (caffeine/1A2), not 2D6** — exactly as the record states. Two problems remain.
(i) Those are **probe-drug AUC increases**, not degrees of enzyme inhibition, and the record's boilerplate
qualifier mentions only the midazolam probe while the sentence lists four enzymes. (ii) The record calls
rucaparib "a moderate inhibitor of multiple CYPs (1A2, 2C9, 2C19, 2D6, 3A4)". The **Rubraca label**
(DailyMed, PharmaAnd GmbH SPL) does not support that shape: §7.1 warns that Rubraca can increase exposure
of **CYP1A2, CYP3A, CYP2C9 or CYP2C19** substrates, while **CYP2D6 and CYP2C8 inhibition appear only under
*In Vitro Studies*** — where the label also records that rucaparib **induced CYP1A2**. So 2D6 is in-vitro
only, 2C8 is omitted, and "moderate" is Claude's word, not the label's.
**Consequence worth noting:** the record's *reverse* direction — rucaparib raising cannabinoid exposure —
is **better supported than its forward direction**, because it rests on a labelled human warning about
CYP3A/2C9/2C19 substrates, and THC and CBD are cleared by CYP2C9 and CYP3A. It is currently written as an
afterthought ("rarely considered").

### 3d. `arsenic-trioxide` is inconsistent with three reviewed comparators — in both possible directions
Its own text says "**No metabolic overlap with cannabinoids exists**", "**No pharmacokinetic interaction
expected**", and monitor per standard protocol "**regardless of cannabis use**" — yet it is **moderate**.
Three reviewed records reason identically and sit at **minor**: `telavancin` ("an independent,
well-documented QT-prolongation warning unrelated to cannabinoids"), `azithromycin` ("apply independent of
cannabis use"), `moxifloxacin` ("No cannabinoid-specific monitoring required").
**But the opposite comparator also exists.** `sotalol` and `dofetilide` are **major/D** on purely
pharmacodynamic QT grounds, citing `36257330` for THC raising heart rate and an arrhythmia association.
Arsenic trioxide carries a **boxed** torsades warning. So the record is inconsistent either way: it should
either make the THC/QT argument and cite it, or drop to minor. It cannot keep asserting no interaction
while holding a moderate rating.

### 3e. `dacarbazine` — the mechanism is understated, and one direction is missing
The record says "CBD has documented **in vitro** inhibitory activity at CYP1A2". That is now **human**:
`37313955` measured caffeine AUC +39%. More important, **the two cannabis exposures point in opposite
directions for this drug.** Dacarbazine is a prodrug needing CYP1A1/1A2 activation, so CBD inhibition
would reduce activation (the record's concern), but **smoked cannabis induces CYP1A2**, which would
increase it. V2 already handles this dual direction elsewhere (`deucravacitinib`, `snri`, and the
`caffeine` record). Only the CBD half is present here, and the record is tagged `CBD` only.

### 3f. Two records have citations sitting unused in V2
`topotecan` (uncited) rests on "CBD has documented in vitro P-gp/BCRP inhibitory activity" — V2 already
holds `16439618` (cannabinoid P-gp inhibition, used by nine records) and `33998860` (in-vitro screen of
cannabis products on ABC and SLC transporters, used by `rosuvastatin` and `afatinib`). `topotecan` is the
only one of six BCRP-mentioning records with no citation. `doxorubicin-liposomal`'s P-gp claim is
`16439618`'s exact subject and its parent record already cites it.

---

## 4. Decisions requested

1. **§3a** — correct `doxorubicin-liposomal`'s CYP2D6 assertion? *(Claude recommends yes regardless of
   any other decision on that record.)*
2. **§3b** — apply the `paclitaxel`/`tretinoin-atra` CYP2C8 qualifier to `dabrafenib` and `nab-paclitaxel`?
3. **§3c** — correct `rucaparib`'s characterisation of rucaparib against the label, and promote the
   reverse direction to primary?
4. **§3d** — `arsenic-trioxide`: **minor** (Claude's recommendation, matching `telavancin` /
   `azithromycin` / `moxifloxacin`) or keep moderate *and* add the cited THC/QT argument?
5. **§3e** — add the smoked-cannabis induction direction to `dacarbazine`, and does it need a THC9/route
   tag? *(Overlaps the open CYP1A2 tagging audit — flagged, not merged.)*
6. **§3f** — fill `topotecan` and `doxorubicin-liposomal` citations?
7. **Severity calls in the cohort** — Claude proposes `arsenic-trioxide` and `lenvatinib` → **minor**, the
   other seven held. See the assessment for the reasoning on `lenvatinib` (CYP3A4 is a *minor* pathway for
   it, its own monitor says "per routine practice", and it predicts a comparative magnitude against
   sorafenib/regorafenib).
8. **CARRIED, THIRD CYCLE UNANSWERED** — the method question for the 80 `minor/D` records. Claude will not
   choose a method unilaterally. See cycle 1 §4.2.

---

## 5. Next proposed cohort — not implemented

The nine oncology `moderate/D` records are **under assessment now** (`nab-paclitaxel`,
`doxorubicin-liposomal`, `rucaparib`, `ipilimumab`, `dabrafenib`, `lenvatinib`, `topotecan`,
`dacarbazine`, `arsenic-trioxide`). After them, the remaining legacy population is the **80 `minor/D`
records** — which is why decision 8 above matters more each cycle.

---

## 6. Open / deferred

- **CYP1A2 molecule-tag consistency audit** — open and deliberately separate; 8 comparator records; three
  options, the third a schema change, so it needs deciding as one piece of work.
- **PMID `32872248` correction (`35454955`)** — touches three CLOSED records; not reopened.
- **Same-molecule / different-formulation evidence rule** — `nab-paclitaxel` and `doxorubicin-liposomal`
  versus their parent C-graded records. Should be one rule, not two judgements.
- **THC9/CBD print-length decision** — deferred at owner instruction.
- **Wave 1 credential rotation** — owner-only, unchanged.
- **`CLAUDE.md`, `HANDOFF_BACKUP_20260827.md`, `Project_Backlog.xlsx`** — untracked and unchanged at owner
  instruction.

---

## Governing standards (unchanged)

Charter `HANDOFF.md §0c` rules 1–10. Grade reflects evidence **for the specific cannabis–drug pair**, not
strong evidence for one component. Severity describes the consequence of **the interaction itself** — not a
non-cannabis interaction, and not the drug's inherent toxicity. Cautious source policy: read the source,
never cite from a title, label human vs. animal vs. in-vitro vs. theoretical, and never transfer a
magnitude across cannabinoid, dose, route, formulation or drug without saying so.

# V2 — The Periodic Table of Cannabis Plant Molecules · Project Handoff
_Last updated: **2026-08-30** · Baseline commit: **`9d58461`** (HEAD == origin/main, live byte-identical, sha256 `ee34b511bc63f588`)_
_Build: 1.60 MB · 64 molecules · **65 health conditions** / 10 groups · **774 NCBI-verified PMIDs** · **277 drugs · 103 drug–drug pairs** · backlog 277 rows_
_**Pre-release audit COMPLETE: waves 2–6 ALL SHIPPED. Wave 1 (the release blocker) needs the owner. Drug tranches A–E ALL SHIPPED; severity-sort bug FIXED; the CYP2D6 sweep is COMPLETE across all 21 records; prostate evidence recalibrated; three Men's Health topics added; a V2-wide count guard now blocks stale numbers; Demo Mode and Guided Match are ALIGNED and share one data source, guarded. Tranche E is now COMPLETE and the four discovered gaps are closed (DRUG-24); the CBD→Δ⁹-THC exposure finding is in the build; `hasRisk` is enforced rather than dead.**_

---

## 0b. CONVENTION — CITE SYMBOLS, NOT LINE NUMBERS (added 2026-08-29)

`index.html` is one 13,700-line file that grows every session, so **every line number written into this
document eventually points at the wrong code.** A Pass-D audit on 2026-08-29 checked the nine
`index.html:NNNN` references in the older audit sections: **eight of the nine had drifted**, some by
hundreds of lines, and two pointed at the *wrong subsystem entirely* (the credential note pointed at a
disclaimer `<div>` and at an `esc()` helper).

**Write `` `var AUTH_HASH` `` or `` `V2FACTS.GROUP_ORDER` ``, not `index.html:3040`.** Symbols survive
insertions; line numbers do not. Where a line number genuinely helps, mark it as a dated hint
("was 3018 when written, 3040 today"), never as the primary locator.

---

## 0a. PROJECT PATH — MOVED 2026-08-29, AND WHY THE PATH MATTERS

The project is at:

```
/Users/josephfriedman/Desktop/Periodic Table Claude Project/V4 Build
```

It was briefly moved to `~/Desktop/Desktop - JOSEPH’s iMac/Periodic Table Claude Project/…` while freeing
disk space, and **that broke `preflight.py`** — the deploy gate. Nothing was lost; git history, the build
and every supporting file were intact throughout.

> ⚠ **`jsc` cannot open a file whose path contains a non-ASCII character.** The folder name carried a
> typographic apostrophe — `JOSEPH’s`, U+2019, bytes `e2 80 99` — which `jsc` mangles to `â`, so
> `preflight.py load_data()` failed with *"Could not open file"*. Proven with a control: a temp directory
> containing `’` fails, the same file at an ASCII path runs. **Keep this project on an ASCII-only path.**

A second symptom, harmless but confusing: `python -m http.server` captures its serving directory as a
string at startup, so after the move the preview server returned **404 for everything** even though
`lsof` showed its cwd correctly following the moved folder. Restarting it from the new path fixed it.

---

### UX-122 → UX-125 (2026-08-29) — DEMO MODE vs GUIDED MATCH: ALIGNED

The owner reported Demo Mode missing screens Guided Match has, and showing shorter lists. Both were
real. **The root cause is structural: Demo Mode was a parallel reimplementation, not a reuse** — the
same module carried `djToMeds`/`djToRec`/`djToggleCat` mirroring `toMeds`/`toRec`/`toggleCat`. Two code
paths for one job is why they drifted.

**UX-122 — Exit, and room for the ribbon.** The inline *Exit Demo Mode* button was on the hub and the
done screen but on **none of the seven screens between them**. Added to the Products screen in normal
document flow. Separately, `.demo-ribbon` is `position:fixed` and **nothing reserved space for it**, so
the last control in the stage sat under the green ribbon — clickable (the container is
`pointer-events:none`) but reading as covered. Fixed by reserving space, `64px` and `116px` on mobile
where the ribbon is deliberately raised above the tab bar. **Both rules require `body.demo-mode`;
standard V2 computes `0px`.**

**UX-123 — one source for the shared tables.**

> ⚠ **A finding in my own report was WRONG and is corrected here.** I had called `nausea-relief` and
> `appetite-stim` fake condition ids to be repointed at the real `nausea`/`appetite`. **They are
> deliberate curated profiles**, and the comment beside them explains why repointing them would break
> things: the real `appetite` condition is **bidirectional** and includes THCV and humulene, which
> *suppress* appetite — an appetite-stimulation request would have been answered with suppressants; and
> the real `nausea` condition leans on **THCA/CBDA, which dispensaries never list**, so product matching
> could not resolve to any stock. **Both were left alone.**

What was genuinely duplicated: `GUIDED_MOL` lives in the `window.V4Guided` data-engine IIFE and **was
not exported**, so the UI-controller IIFE could not reach it and carried `DJ_GUIDED_MOL`, a verbatim
copy. Now exported as `V4Guided.guidedMol`; the copy is gone. The science-alias map was written out
**five times**; one constant serves every call site.

**The 12 demo labels are deliberately NOT canonical** — "Chronic pain" not "Analgesic / Pain". That
plain wording suits a demo audience; **the ids are shared, the display wording is intentionally local.**

**UX-124 — browse-picked conditions made visible.** UX-105 lets a presenter add any of the 65
conditions, and those additions drove the recommendation correctly **while being invisible on the
picker** — no chip, no way to remove one without restarting. They now get their own chip (🔍 + "added by
name"), labelled from `CONDITIONS`, which is right *here* because the reader just searched by that name.

**UX-125 — the demo journey reuses the guided preference screens.** It never asked the patient anything,
so `st.ceiling` stayed on the `'any'` `djRun()` assigns. **Measured consequence:** same condition,
*"I'd rather avoid it"* → CBD/CBC/CBN/CBG, **2 products**; *"I don't mind it"* → THC/CBD/CBC/CBN,
**11 products**. Only the second was reachable. Experience → THC → Safety now sit between Molecules and
Medications, using **the same `scExperience`/`scThc`/`scSafety` functions**, each with one conditional
prefix `(st.dj?djStepBar(2):'')`. Only routing is demo-aware. `DJ_STEPS` gained a *Preferences* stage.

**Deliberately not added** (the demo already covers them; the brief said not to remove working Demo
functionality to force a match): **route** — `djRefine` has a route selector; **comorbidities** — the
condition picker is already multi-select; **time-of-day / onset** — they belong to the standard flow
shown from the hub (UX-79); **handoff** — the demo has its own done screen.

**Guided Match verified unchanged in both modes:** outside the demo `thc → second → safety`; inside the
demo `thc → timeofday` (UX-79 preserved); no demo step bar leaks into either.

> **New guard — `check_demo_guided_parity`.** Fails the build if `DJ_GUIDED_MOL` returns, if the alias
> map appears more than once, or if any `DJ_CONDS` id resolves to neither a real condition nor a curated
> profile. Proven against all three regressions.

---

### DRUG-23 (2026-08-29) — THE SEVEN COVERAGE GAPS

Six new records, one merge, four pairs. **Two premises were wrong and were corrected before building**,
both verified at DailyMed: **Maalox is aluminium and magnesium hydroxide, not calcium carbonate**, and
**current Mylanta is simethicone only** — so the antacid record is named for both cation families it
actually covers. **Quazepam got no record**: it is a benzodiazepine, so Doral and quazepam joined the
existing `benzos` entry rather than duplicating it, and it correctly inherits `opi_benz` major/A.

| Drug | Brands | Sev/Ev | Why |
|---|---|---|---|
| **Loperamide** | Imodium, Imodium A-D, Anti-Diarrheal | major/**C** | Boxed **Torsades / sudden death** warning at high dose; labelled **P-gp substrate**, and CBD inhibits P-gp (`16439618`) |
| **Potassium chloride** | Klor-Con, K-Tab, Micro-K, Slow-K, K-Dur | minor/**D** | **Honest negative** for cannabis. Earns its place on the drug–drug axis: labelled hyperkalaemia monitoring with RAAS inhibitors, **contraindicated with amiloride/triamterene** |
| **Bismuth subsalicylate** | Pepto-Bismol, Kaopectate, Bismatrol | moderate/**D** | **The name is the warning** — it is a salicylate: bleeding risk, Reye's, and black stool that can mask a real GI bleed |
| **Intranasal corticosteroids** | Flonase, Rhinocort, Nasonex, Nasacort, Xhance, Omnaris, Qnasl | minor/**C** | Labels flag only **strong** CYP3A4 inhibitors (documented Cushing's with ritonavir). **CBD at 56% does not reach that threshold** — said plainly |
| **Antacids** | Tums, Rolaids, Maalox, Mylanta, Gaviscon | minor/**D** | No cannabis pathway. Real interaction is **absorption** — levothyroxine is labelled |
| **Oxymetazoline** | Afrin, Vicks Sinex, Nostrilla, Dristan | minor/**D** | Deliberately **kept out of the systemic decongestant record** so it does not inherit warnings a nasal spray has not earned. Real issue is rebound congestion past 3 days |

**Four pairs:** potassium+RAAS (major/**A**) · loperamide+P-gp inhibitors (major/**A**) ·
antacids+levothyroxine (moderate/**A**) · bismuth+anticoagulants (moderate/**C**). `antiarrhythmic_qt`
**extended** with loperamide, verified still firing for its originals.

> ⚠ **Four wrong mappings caught at final verification.** **Zicam is a homeopathic ZINC cold remedy, not
> oxymetazoline** — that would have been a real error. Diamode, Epiklor, Kaon-Cl and Amphojel have no
> current SPL to verify against and were dropped, the same discipline that excluded Prinzide and Ziac.
> **Verify every brand at DailyMed before adding it.**

**Totals verified app-wide:** a scan of rendered text found **no user-facing claim of a drug, pair or
PMID count** — those live only in project docs, so nothing in the app needed updating. The 28
`data-v2fact` spans are derived and self-update; preflight asserts every one.

---

### DRUG-22 (2026-08-29) — BRAND-NAME COVERAGE

Norvasc was already correct. A sweep of **226** recognised US brand names through the app's *real*
search path found **79 genuine gaps** — generic present, brand returning *"No cannabis drug interactions
found"*. All 79 now resolve.

**Verapamil and diltiazem were split out** into `ccb_nondhp` (moderate/C). They were reachable only by
generic name and landed on a card titled *"CYP3A4 Inhibitors (various)"* that never identified them as
heart medicines and called them **strong** CYP3A4 inhibitors — they are **moderate**. Same
class-record-hides-the-drug pattern as fluvoxamine and cimetidine in tranche A. The new record covers both
directions and leads with the pharmacodynamic concern: they slow the heart and lower BP while &Delta;9-THC
does the opposite; verapamil plus a beta-blocker is the worst case. `cyp3a4inh` now points at it, which
also repairs the "strong" overstatement.

**Combination products go into BOTH component records**, the way Excedrin already did — not aliased to
one. Hyzaar, Zestoretic, Diovan HCT, Benicar HCT, Avalide, Micardis HCT, Exforge, Lotrel, Ultracet,
Endocet, Vicoprofen and Zyrtec-D each return **2 results**. This is a safety point, not tidiness: a patient
on Hyzaar **is on a thiazide**, and `triple_whammy` / `lithium_thiazide` / `antiarrhythmic_diuretic` could
never fire while only the ARB half was reachable. Every composition verified at DailyMed; **Prinzide and
Ziac deliberately left out** — neither has a current SPL to verify against.

> **Method note worth keeping.** An exact-match sweep flagged Motrin, Bayer and Unisom as missing; all
> three work, because the app searches by **substring**. Testing through `onDiSearch` rather than a
> reimplemented matcher is what separated real gaps from artefacts. **Always test the real path.**

**All 13 were coverage gaps rather than brand gaps, and all were closed the same day in DRUG-23**
(above): potassium chloride, loperamide, bismuth subsalicylate, antacids, intranasal corticosteroids
and oxymetazoline became records; quazepam joined the existing `benzos` entry rather than duplicating
it.

---

### DRUG-24 + DRUG-24a (2026-08-29, `c8cd8d1` → `b0e3ab1`) — TRANCHE E REMAINDER + THE FOUR DISCOVERED GAPS

Owner: *"Add the remaining tranche e. And whatever else you were talking about that I'm not sure about."*
That closed items 3, 4, 5 and 6 of §0aa in one tranche. **10 records, 7 pairs, 2 new preflight guards.**

| Record | Sev/Ev | The point of the record |
|---|---|---|
| `hydroxyzine` | moderate/C | Sedating antihistamine; metabolised **to cetirizine**, which is the non-sedating record. QT warning is hydroxyzine's own — cannabis is not a QT drug and the record says so. |
| `memantine` | minor/D | **Renally cleared, not a CYP substrate.** The record exists to say the expected interaction is not there. |
| `pramipexole` | moderate/C | **Renally cleared** — so the smoked-vs-oil problem that dominates `ropinirole` (CYP1A2) explicitly does *not* apply. That contrast is the record. |
| `inhaled_steroids` | moderate/C | Two things: CYP3A4 (CBD is moderate, below the strong-inhibitor threshold the labels target) and — the bigger one — **smoking blunts corticosteroid response in asthma**. Those data are cigarettes, not cannabis, and the record says so. |
| `methocarbamol` | moderate/C | Graded **below carisoprodol** because it has no CYP2C19-dependent meprobamate metabolite. |
| `carbidopa_levodopa` | moderate/C | Not a CYP drug; no interaction invented. Honest on efficacy: nabilone (**synthetic**, n=7) helped dyskinesia, but the **plant** extract RCT (n=19) found nothing, and CBD 300 mg did not move UPDRS. |
| `triptans` | moderate/C | Most are MAO-A/renal, not CYP3A4. Carries the **positive** vaporised-cannabis migraine RCT and the medication-overuse-headache trap. |
| `eletriptan` | moderate/C | Split out because its label carries a **72-hour restriction** on potent CYP3A4 inhibitors that no other triptan has. |
| `metoclopramide` | major/B | Poor for cannabinoid hyperemesis **and** boxed for tardive dyskinesia — escalation buys risk and no benefit. Notes CBD does *not* inhibit CYP2D6. |
| `estrogen_hrt` | moderate/C | **Declines an available scare.** The cigarette-plus-estrogen clot rule does not transfer: Circulation 2025, n=4,285 with CAD, **aHR 0.87 (0.61–1.24), null** — with that study's limits named. |

**Pairs (7):** `levodopa_dopamine_antagonist` major/A, `pramipexole_dopamine_antagonist` moderate/A,
`metoclopramide_eps` major/B, `eletriptan_cyp3a` major/A, `ics_cyp3a` moderate/B, `hydroxyzine_cns`
major/B, `triptan_ssri` moderate/B. `ropinirole_dopamine_antagonist` extended with metoclopramide.

`triptan_ssri` **de-escalates** a famous warning rather than repeating it: reviewing the FDA alert, the
American Headache Society found only 10 of the 29 cases met Sternbach and **none** met Hunter — Level U,
*"does not support limiting the use of triptans with SSRIs or SNRIs"*. Cannabis is not meaningfully
serotonergic and is noted as **not** a third serotonergic agent.

**CBD → Δ⁹-THC, now in the build** (was §0aa item 3; PK in `c8cd8d1`, the clinical half in `b0e3ab1`).
A CBD-dominant brownie raised Δ⁹-THC AUC by **161%** over the same 20 mg dose without CBD. **DRUG-24a**
added the second half, and it is the more useful half: the companion report from *the same 18-participant trial*
measured the consequence: **more** anxiety, sedation, memory difficulty, heart rate and psychomotor
impairment than the THC-dominant extract — *"contradicts common claims that CBD attenuates the adverse
effects of Δ⁹-THC"*. Scoped to **oral** use, and flagged as one trial reported twice, not two replications.

> ⚠ **A DEFECT I INTRODUCED, AND WHY THE GUARD NOW EXISTS.** The first insertion put **all seven pairs
> into `DI_DATA`**. The script searched for the literal `"\n  ];"` after the last pair — but **`DDI_DATA`
> closes on `"\n];"` with no indentation** while `DI_DATA` closes with two spaces, so the search ran
> straight past the end of `DDI_DATA`. **Every existing check passed**: preflight reported
> *"284 drugs, 96 pairs"* and said safe to deploy. The count was the only symptom, and a count is easy to
> skim. Reverted to backup, redone with bracket-matching.

**Two new preflight guards, each proven by deliberately breaking the file:**
* `check_record_schema` — a DI record must carry the keys the card renderer reads and must **not** carry
  pair-only keys, and vice versa; `sev`/`ev` are closed vocabularies. Catches the defect above.
* `check_hasrisk_invariant` — **§0aa item 6 resolved by enforcing, not deleting.** `hasRisk` is read by no
  runtime code, but it is real editorial intent, so the rule is now: every `hasRisk:true` condition must
  surface a caution a reader actually sees. 14/14 pass. Deliberately one-directional — `condHasAdverse`
  matches by group and legitimately covers 25.

**Three PMIDs I recalled from memory were wrong** (`12904324`, `11923546`, `11673599` point at unrelated
papers) and were discarded before use. Every citation was verified by title at NCBI **before** the prose
was written around it.

**Ten search aliases** for phrasings that matched nothing — `carbidopa levodopa` with a space,
`inhaled steroid` singular, `hormone replacement` — three of which fix the same gap in the DRUG-23
intranasal record. Combination brands cross-added per the DRUG-22 rule: **Namzaric** → donepezil,
**Treximet** → ibuprofen.

**Not a defect, checked:** the floating *End Session* pill overlays the last ~42 px of content at
bottom-right. It behaves identically on pre-existing records, and no record's text is trapped — every
one scrolls fully clear. Session logic was left alone.

---

### QA PASSES A & D (2026-08-30) — and DRUG-25, the first remediation

A comprehensive QA was scoped after DRUG-24 because **40 drug records (14%) and 27 pairs (26%) postdate
the last full audit** (`e9a1c96`) and had never been independently reviewed. Passes A and D ran.

**Pass A found 36 entries graded A or B — a grade that ASSERTS human evidence — citing nothing.**
They sort into four groups. **Group 1 is fixed (DRUG-25, `90ff0c0`); Groups 2–4 are open.**

> ⚠ **The measured data contradicted what V2 said.** Stott 2013 (`23750331`), phase I crossover, 36
> volunteers: ketoconazole raised Δ⁹-THC C<sub>max</sub> **+27%**, CBD **+89%**, 11-OH-THC **+204%**;
> rifampicin lowered them **−36% / −52% / −87%**. V2's "2–3x or more" **overstated the effect on the
> parent cannabinoids by two to three fold** — only the metabolite tripled. Four records repeated that
> same unsourced figure.

| Record | Was | Now |
|---|---|---|
| `cyp3a4inh` | "2–3x or more", uncited | the three measured values, cited |
| `cyp3a4ind` | ">50% concentration **and efficacy**" | measured concentrations; **efficacy claim removed** — the study measured levels, not benefit |
| `antiretrovirals` | "ritonavir 2–3x", "efavirenz >50%" | **no ARV study exists** — says so, and reasons by explicit enzyme read-across |
| `clarithromycin` | "2–3x or more"; QT "additive with THC" | ketoconazole read-across; **QT claim removed** — Δ⁹-THC is not an established QT-prolonging drug |
| `aspirin` | "additive antiplatelet effects", **grade B** | **grade C** — the platelet data is a 1989 *in vitro* study; the documented risk is metabolic (warfarin/CYP2C9, clopidogrel/CYP2C19) and aspirin uses neither enzyme |

Each record now states what it used to claim and why it changed, so the correction is visible.

> **`23750331` was ALREADY in the build**, cited by the azoles record. The right paper was present the
> whole time; the four records that needed it cited nothing. **Citation gaps here are a reuse problem as
> much as a sourcing problem — search V2 before assuming no source exists.**

**A3 was a clean pass** — all 17 `major/D` oncology records are explicitly honest that they are theoretical.

**Pass D fixed the documentation, and found the largest open content job in V2.** Eight of nine
`index.html:NNNN` references in the older audit sections had drifted, two pointing at the wrong subsystem
entirely; all replaced with symbol anchors (see §0b). DEMO-06 was corrected — it still said "one
credential… 97 of 221 commits", wrong on count, location and history.

> ⚠ **V2's drug records are TWO TIERS.** Post-audit 40: **100%** point the reader to a clinician, mean
> `monitor` 446 chars, **0** in prescriber voice. Pre-audit 237: **23%**, mean **154** chars, **67 in
> prescriber voice**. `warfarin`'s entire guidance is *"Monitor INR frequently… Warfarin dose reduction
> is often required."* — clinician instruction, on the highest-risk drug in the base, with no clinician
> pointer, that a lay reader could read as licence to change a dose. **This is not a regression: the
> recent tranches raised the standard and exposed how far below it the legacy tier sits.**

---

### DRUG-26 (2026-08-30) — `source:'label'`, AND THE ATTRIBUTION GUARD

Owner-approved Group 2. **An A/B grade asserts human evidence.** 36 entries carried neither a PMID nor
any attribution — so the entries with the *strongest* regulatory backing (FDA contraindications, boxed
warnings) rendered an empty citation slot that reads as *unsupported*. Downgrading them to C would have
called an FDA contraindication preclinical. **`source` removes that false choice.**

* **`source` is orthogonal to `ev`.** `ev` keeps describing the strength and type of evidence; `source`
  names the *authority*. A label contraindication rests on human data the manufacturer submitted, so
  `ev:'A'` + `source:'label'` is coherent. One permitted value today: `'label'`.
* **No badge, colour or filter** — those would compete with the existing severity/evidence chips.
  It renders as one line, **"Basis: FDA prescribing information"**, where the citation would sit, on
  **both** the screen card and the print report, from one shared helper so they cannot drift. A PMID
  still takes precedence where both exist.
* **18 tagged, not 19 — verified per entry, not bulk-tagged.** 18 quote label language explicitly.
  **`alc_meth` was excluded**: its only cue is V2's own phrase *"Strictly contraindicated"* and it never
  cites the label it relies on. Methadone's label does carry the relevant boxed warning, but tagging a
  record whose text does not show its basis would make the field a rubber stamp. It moved to Group 3.
  Grades reviewed and unchanged; none of the 18 has a PMID, so `source` is never redundant.

**New guard `check_evidence_attribution`** — every A/B entry must cite a PMID or name an approved
source. **C and D are exempt by design**: 160 records legitimately have no citation because they say no
human data exists. Proven three ways (new unattributed entry, invalid `source`, fixed register entry).

> The 13 already in violation are registered in **`ATTRIBUTION_OPEN`**, not silently exempted, and the
> register is **self-cleaning** — preflight fails if a listed id no longer violates, so it cannot rot
> the way this file's line numbers did (§0b).

---

### DRUG-27 (2026-08-30) — GROUP 3 CLOSED: ALL 12 REMAINING A/B ENTRIES ATTRIBUTED

Each entry was checked against the literature **first** and given the basis that actually supports it —
**9 cite a paper, 3 attribute to FDA labeling.** Per the owner's instruction, no citation was added
merely to satisfy the guard.

**Two records were TEMPERED because the papers say less than the records did:**
* `ondan_prochlor` — ondansetron's measured QT effect is **~8 ms at peak with no conduction problems in
  435 patients**. The text now says so and moves the risk to **stacking**, which is what the case series
  actually shows.
* `tacro_ibu` — the source study calls the evidence **"meager"**. The record now says that, with the real
  numbers (AKI **5/41** vs **7/126**), instead of asserting potentiation.

**`alc_meth` had a false mechanism removed** — it claimed "alcohol inhibits CYP3A4/CYP2D6, reducing
methadone clearance". Acute and chronic alcohol push hepatic enzymes in **opposite** directions, so a
single-direction claim is unsupportable, and the danger does not depend on it.

> **The self-cleaning register proved itself in real use.** After the twelve fixes landed, preflight
> **failed** with twelve *"listed in ATTRIBUTION_OPEN but now has attribution"* errors and refused to
> pass until the entries were removed. That is precisely the stale-allowlist failure this project has
> been bitten by repeatedly (§0b), caught automatically. **`ATTRIBUTION_OPEN` is now down to 1.**

---

### DRUG-28 (2026-08-30) — QA PASS A/D CLOSED

The five held decisions, all owner-approved. **`ATTRIBUTION_OPEN` is now empty and the rule stands
unqualified: all 121 A/B-graded entries cite a PMID or name a source.**

| Record | Change | Why |
|---|---|---|
| `hydroxyzine_cns` | **B kept** — my B→C withdrawn | `40269504`: 319 impaired-driving specimens, co-detected with antidepressants 74% / opioids 44%. Human data. Record says plainly the co-exposures were **not cannabis**. |
| `tricyclics` | **C → B** | `6303138` is a cannabis-*specific* human case report; `methadone` and `ondan_prochlor` are already B on that basis |
| `lithium` | claim removed, **C → D** | "Case reports associate cannabis with lithium toxicity" — none found; `7298879` has no abstract so was **not** cited. Kept the dehydration/CHS route, which needs no invented literature |
| `aspirin` | **moderate → minor** | 81 mg works by irreversible COX-1 acetylation, independent of salicylate clearance. Matches `rosuvastatin` (minor/C) |
| `bismuth` | **D → C**, error fixed | claimed "no UGT overlap" **for a salicylate**; CBD inhibits UGT1A6 |

> **The rubric was applied in both directions in one tranche** — `lithium` C→D for *lacking* preclinical
> evidence, `bismuth` D→C for *gaining* it. That is the clearest sign yet the A–D scale is being used as
> defined rather than as a feel.

**Three of my own errors corrected here**, all found by continuing to check rather than by being told:
the aspirin "neither enzyme" claim (DRUG-25), the bismuth "no UGT overlap" claim (DRUG-23) — the same
UGT1A6 blind spot twice — and the withdrawn hydroxyzine downgrade.

---

## 0c. LEGACY REVIEW CHARTER (owner-approved 2026-08-30, before batch 1)

The 237 pre-audit drug records are reviewed under the standards established during QA Pass B.
**Triage is complete; no legacy record has been edited.** These are the rules for the work that follows.

**1. The evidence grade describes the SPECIFIC cannabis-drug pair.** Not the components of a proposed
mechanism. Human evidence that cannabis raises heart rate, impairs driving, is linked to arrhythmia or
causes vomiting is real and belongs in the explanatory text — it does not grade the pair.

**2. Mechanistic plausibility does not elevate an unstudied pair.** A shared metabolic route plus a
measured cannabis effect on that enzyme is **D**, not C. `C` requires preclinical/in-vitro evidence
bearing directly on the pair. Naming the enzyme does not make the pair studied. *(Pass B applied this
to 30 records; the same reasoning applies here.)*

**3. Severity and evidence are independent dimensions.** A narrow-therapeutic-index drug with a boxed
warning can and should be `major` at grade D. **Never lower a severity because a grade fell** — Pass B
changed 30 grades and zero severities, and that is the pattern to hold.

**4. Citations must support the specific claim they accompany.** Read the abstract; do not cite from a
title, and never from memory. If a record asserts that a literature exists, it must point at it — and
if the literature cannot be found, the claim goes, not the citation requirement. **Check whether the
right paper is already in V2 before concluding none exists** — this has now happened four times.

**5. Educational language must not drift into individualized prescribing instruction.** V2's reader is
a patient or dispensary staff member, not the prescriber. *"Monitor INR frequently… dose reduction is
often required"* is an instruction to a clinician printed for a patient, and a lay reader could take it
as licence to change a warfarin dose. Rewrite to patient-facing guidance that names what to report and
to whom, without prescribing.

**6. Clinically significant interactions need a clinician pointer.** Say who to tell and what to
report. 201 of 237 legacy records currently have none.

### Legacy SECOND-LOOK list (comparator conflicts flagged during review, not yet resolved)

- **`loperamide` (major/D)** — flagged Legacy Batch 7. Its `major` rests on P-glycoprotein inhibition
  (`16439618`, in vitro) plus loperamide's cardiac toxicity at high doses. Reassess whether `major` is too
  high **at recommended doses**: the human literature questions whether P-gp inhibition translates into
  clinically meaningful CNS or cardiac opioid toxicity at therapeutic loperamide doses, and the serious
  cases are overwhelmingly massive-overdose reports. Surfaced by a comparator conflict with `digoxin`
  (same mechanism, same citation, `moderate`) — **the conflict triggered review of both rather than forcing
  either to match the other.** Owner ruling: comparator consistency prompts review, it does not dictate a
  grade or severity.
- **SEARCH GAP (not a record issue, found Legacy Batch 7):** a patient typing **"beta blocker"** or
  **"beta blockers"** gets **zero results**, because the record reads "Beta-**Blockers**" and the searcher
  does not split that hyphen. Comparable class terms are fine — "calcium channel blocker" (2 hits),
  "proton pump inhibitor" (2), "ace inhibitor" (1). Pre-existing, not caused by the propranolol split.
  Worth a tokeniser fix or a plain "beta blocker" alias, since this is one of the most common drug classes
  a lay reader would search by name.

- **`cabazitaxel` (major/D)** — noted Legacy Batch 4. `major` with no pair evidence at all; defensible on
  its 80-90% CYP3A4 clearance, but worth a second look alongside the other taxanes.

**9. CAUTIOUS SOURCE POLICY** (owner-adopted after Legacy Batch 6).
- Prefer **primary peer-reviewed studies, FDA prescribing information and authoritative regulatory/clinical
  sources** over reviews or secondary summaries where available.
- A citation must support **the specific claim AND its direction of effect** — not merely discuss the drug,
  the cannabinoid, the enzyme or the disease.
- **Read the abstract or source context before using a PMID.** Never cite from a title or a search-result
  snippet. *(Three defects in this project came from exactly that: doxorubicin, etoposide, irinotecan.)*
- Distinguish **human clinical / human mechanistic-PK / animal / in vitro / theoretical inference** and say
  which one a statement rests on.
- Do **not** extrapolate across cannabinoid, dose, route, formulation, drug or drug class without labelling
  the extrapolation. *(The insulin record failed this: a THCV finding was written up as a CBD effect.)*
- Do **not** convert an in-vitro inhibition percentage into a predicted human exposure change unless human
  data support it.
- Reviews may identify evidence or give background, but are **not** pair-specific evidence when what lies
  beneath them is theoretical or preclinical.
- Where sources conflict or the claim is uncertain, take **the more conservative wording and grade** and
  flag it — do not resolve uncertainty by asserting.
- **Check whether a suitable source is already in V2 before adding a new one.** This has now paid off nine
  times, including a case where the paper was cited by one record and missing from the record that needed
  it most.

**10. MISSING-EVIDENCE REPORTING** (owner-adopted after Legacy Batch 6).
- When no direct cannabis-drug evidence is found, **say so explicitly**. Never let a record imply the pair
  was studied and found clear.
- **"No evidence identified" is not "evidence of no interaction."** Keep the two distinct in the wording.
- Report what was actually checked: direct pair evidence, cannabinoid-specific evidence, supporting
  mechanism evidence.
- If only mechanism exists, call it **mechanistic plausibility / theoretical inference**, not pair evidence.
- **Do not invent or inflate a warning to fill an evidence gap** — and do not read a gap as proof of safety.
- If an important claim cannot be supported, **remove or qualify it and record the gap**; never leave it
  standing behind an unrelated citation.
- Where the absence matters clinically, use plain wording: *"No direct studies of this cannabis-drug
  combination were identified; the concern is based on [specific mechanism]."*
- **Record significant evidence gaps in the batch checkpoint** so they stay visible for future literature
  updates.
- If a new study would materially change a grade, severity, mechanism or interpretation, **flag it for
  review** rather than extrapolating.

**8. SEVERITY RUBRIC** (added after Legacy Batch 1; V2 had never written one down).
Severity describes **the consequence of the cannabis-drug interaction itself** — not how dangerous the
drug is on its own, and not a separate drug-drug interaction the record happens to mention. Ask: *if this
interaction occurs, what happens to the person?*

Weigh five factors: **therapeutic index** (how little room there is between a working dose and a harmful
one), **plausible magnitude** of the exposure or pharmacodynamic shift, **reversibility** (does it resolve
on stopping, or leave lasting harm), **whether clinical intervention is needed** to detect or correct it,
and **the worst realistic outcome**.

- **major** — could plausibly cause serious harm: haemorrhage, arrhythmia, organ toxicity, seizure,
  hospitalisation, a fall with injury, transplant rejection, or loss of control of a serious disease.
  Typically a narrow-therapeutic-index drug, a large plausible exposure change, or an effect that is
  silent until the harm occurs. *Warfarin (bleeding) and rivaroxaban (no INR to warn you) are major.*
- **moderate** — likely to produce troublesome but recoverable effects: dizziness, sedation, nausea,
  orthostasis, a measurable but manageable change in drug level. Usually needs attention, not rescue.
- **minor** — mild, self-limiting, or largely theoretical in consequence even if the mechanism is real.

**Severity is independent of evidence.** A well-evidenced trivial interaction is `minor/A`; a poorly
evidenced dangerous one is `major/D`. **Never move a severity because a grade moved** — across Pass B,
30 grades changed and 0 severities did.

**Two traps found in real records:** (a) severity carried by a NON-CANNABIS interaction — `nitrates` was
`major` partly on the nitrate+PDE5 contraindication, which is a drug-drug issue already held by the
`nitro_sild` pair; the cannabis severity must stand on the cannabis interaction alone (it does: nitrates
are potent vasodilators used by people with coronary disease, and V2 grades `sildenafil` major on the same
additive-vasodilation mechanism). (b) severity carried by the DRUG's inherent danger — a chemotherapy
agent is dangerous by itself; that alone does not make its cannabis interaction major.

**7. Records are reviewed INDIVIDUALLY. No mechanical mass regrading.** Every grade change carries its
own documented rationale. Batches of 7-10, checkpoint each, and flag anything material — grade,
severity, mechanism, clinical interpretation, or policy — for owner approval before implementing.

---

## 0aa. WHAT IS ACTUALLY STILL OPEN (2026-08-29, after DRUG-24)

Everything else in this file describes shipped work. These are the only live items:

1. **⚠ CLEARTEXT CREDENTIALS — there are TWO pairs, not one.** Earlier revisions of this file called
   this "the Demo Mode credential", singular. That was wrong and understated the scope:
   * **Demo Mode gate** (`index.html:3040`) — username and password named in the `/* */` comment above
     `AUTH_HASH`. Gates the fictional demo dispensary. **No real data behind it.** In 164 of 265
     commits, since `842616a`.
   * **Staff feedback console** (`index.html:13326-13327`) — `ADMIN_USER` is a **live cleartext
     variable**, not a comment, and the password sits in the inline `//` comment beside its hash. It
     gates the **patient-feedback review console** (lists/exports patient free text from
     `acann_feedback_v1`). In 141 of 265 commits, since `f3dad41`. **This is the pair with a privacy
     dimension**; the demo gate has none.

   **Both hashes are `cyrb53` — a non-cryptographic ~53-bit hash.** Removing the comments does NOT
   create access control; anyone reading the public source can bypass either gate, and the file says so
   in both places. Rotation only helps if the gate is accepted as obfuscation — provable control needs
   edge auth (Cloudflare Access / Basic Auth), which the code itself recommends.

   **Verified NOT exposed:** `SUPABASE_URL` and `SUPABASE_ANON` are empty strings; `SYNC_ENABLED` and
   `RD_SYNC_ENABLED` are both `false` (the two `true`s in the file are prose inside comments); no API
   keys, tokens or endpoints anywhere. Neither credential reaches a server, database or account — the
   blast radius is one kiosk's browser storage.

   **The question only the owner can answer:** whether either password pattern is reused anywhere real
   (email, GitHub, Supabase, POS). Both look purpose-made for this app; if they are not reused, the
   exposure is cosmetic. *(Owner reviewed 2026-08-29 and is not concerned about the demo UN/PW.)*
2. **QA Passes A and D are CLOSED.** Groups 1–4 and both A2 mismatches are done (DRUG-25/26/27/28);
   `ATTRIBUTION_OPEN` is empty. Nothing from Pass A or D remains open.
3. **QA Passes B and C — not started.** *B:* independent scientific review of the 40 post-audit records
   (the long pole). *C:* functional regression not covered by preflight — print paths actually rendered,
   Check-All-Interactions, Guided/Demo end-to-end.
4. **The legacy-tier rewrite** — 237 pre-audit records at 23% clinician-pointer coverage, 67 in
   prescriber voice. Largest outstanding content job; `warfarin` first.
5. **Supabase / POS** — FEED-02/05 need owner credentials; INV-05/06 need a real dispensary POS.
3. **Periodic mechanism re-read (low priority, no known defect).** DRUG-20/21 removed every false
   CYP2D6 claim; a standing re-check of new records against Bansal 2023 (`37313955`) is worth doing
   whenever a tranche lands, since mechanistic inference has contradicted human data repeatedly on
   this project (Sotyktu, fenofibrate UGT, CYP2D6).

**Closed by DRUG-24:** the CBD→Δ⁹-THC 161% finding (was item 3), Tranche E's five "Consider Later"
drugs (item 4), the four discovered gaps — carbidopa/levodopa, triptans, metoclopramide, estrogen/HRT
(item 5), and `hasRisk`, resolved by enforcing it in preflight rather than deleting it (item 6).

---

## 0. CHECKPOINT — 2026-08-27 (READ THIS FIRST)

**Everything built is deployed and verified.** `local == origin/main == live`, all three sha256
`f487b7634cf8c8ff`, HEAD `760a2ba`. Preflight passes every check, including `--online` (all 728 PMIDs
resolve at NCBI). Working tree clean for `index.html`.

**A full pre-release QA / scientific-validation / governance audit was run on 2026-08-27**
(report: https://claude.ai/code/artifact/04a2c9cf-46cb-4ca8-9dc6-58b2e4e450ca).
18 findings. **Waves 2–6 have all shipped** — `809d5ce` (AUDIT-09…13) and `038a18f` (AUDIT-14…23).
One finding was **withdrawn as a false positive** (see below). **Wave 1 — the CRITICAL item — is NOT
done and cannot be done without the owner**, because it needs a new credential.

`038a18f` also added four medications on owner request (**DRUG-11**): Rasonque (daraxonrasib),
baclofen, tizanidine, amantadine — plus two drug–drug pairs and a widened `check_pmids` in preflight.

### ⚠ THE RELEASE BLOCKER IS STILL OPEN
The Demo Mode username and password sit in the comment above **`var AUTH_HASH`** (search that symbol; it
was line 3018 when this was written and is line 3040 today — **line numbers in this file drift, symbols do
not**). Cleartext, live on the public
site and in 164 of 265 commits (the earlier "141 of 243" conflated this with the SEPARATE staff
console credential, which is the one in 141). Deleting the line is NOT sufficient — it is in history and
must be **rotated**. Do not treat V2 as ready for external review until this is closed.

### What the session of 2026-08-26 → 08-27 shipped

| Commit | Work |
|---|---|
| — | UX-100 Chronic Liver Disease / Cirrhosis, fully integrated (30 citations, `LIVER_CITATIONS`) |
| — | UX-101/106 Feedback PII guards (email / URL / phone / DOB), 3 submissions per session |
| — | UX-102/108 evidence sentence matches the KIND **and DIRECTION** of the cited evidence |
| — | UX-103 Guided Match browse/search screen over all 62 conditions + `COND_ALIASES` |
| — | UX-110 session-scoped demonstration review queue (Demo Mode only, dies with the session) |
| — | UX-111 Depression (MDD) · UX-113 Bipolar Disorder · UX-114 PCOS / PMOS (dual-grouped) |
| — | UX-112 **Substance Use Disorders** split out of Mental Health; Cannabis Use Disorder added |
| — | UX-115/116 Guided paths for all Women's Health and for Men's Health |
| — | UX-117 "compounds" → "molecules" in three places · UX-118 Drug Interactions active state |
| — | DRUG-08/09 Crestor (rosuvastatin) + pravastatin; `groups` added to `DDI_DATA` so 3-drug pairs fire |
| `d362c93` | HEAD |

### Two process lessons from that session (both cost real time)

1. **Never audit this file with a regex — evaluate the objects.** `DI_DATA` is written in TWO
   styles (single-quoted JS *and* double-quoted JSON). A regex saw only the first and reported
   "11 unreachable DDI pairs" that did not exist; acting on it created a duplicate `ezetimibe`
   record. `preflight.py load_data()` now evaluates `M`, `CONDITIONS`, `DI_DATA` and `DDI_DATA`
   in JavaScriptCore. The same class of error also produced a false "gemfibrozil has no drug
   record" (it exists as `id:'fibrates'`).
2. **`clearDraft()` is not a session-end hook.** It also runs when the feedback form reopens, so
   clearing the demo review queue there erased the previous submission every time. Session-end
   cleanup belongs in `resetToEntryGate()`, which is the single funnel for all three ways a
   session ends (New Session · I'm Done · inactivity expiry).

### DRUG-16 (2026-08-28) — TRANCHE C SHIPPED: seven antiarrhythmics, individually

Owner directed **seven individual records, not class records**, and the differences justify it:

| Drug | Metabolism — all different | Distinctive risk |
|---|---|---|
| Flecainide | CYP2D6 + 10–50% renal unchanged | Boxed **CAST mortality** warning |
| Propafenone | CYP2D6 **+ CYP3A4 + CYP1A2**; label says avoid inhibiting 2D6 *and* 3A4 together | Boxed mortality; agranulocytosis; HF contraindicated |
| Sotalol | **NOT METABOLIZED AT ALL** (label) — renal, unchanged | Boxed proarrhythmia; in-hospital initiation |
| Dofetilide | ~80% renal via **cation transport**; CYP3A4 low affinity | REMS; long contraindication list |
| Quinidine | CYP3A4 substrate **AND potent CYP2D6 + P-gp inhibitor** | Raises digoxin ~2×; cinchonism |
| Procainamide | **NAT2 acetylation** → NAPA (active) | Boxed lupus + agranulocytosis |
| Disopyramide | Hepatic/renal | **Anticholinergic — labeled "should not be used in glaucoma"**; negative inotrope |

All **major/C**. Five pairs: dofetilide+contraindicated (major/A) · quinidine+digoxin (major/A) ·
QT-antiarrhythmic+QT-drug (major/B) · propafenone+CYP inhibitors (major/B) ·
QT-antiarrhythmic+thiazide (major/B).

### ⚠ THE CYP2D6 CANNABIS STORY IS NOT TRUE IN HUMANS — third correction this week
**Bansal 2023, PMID `37313955`** — randomised crossover, 18 healthy adults, CYP probe cocktail after
a CBD-dominant cannabis brownie (640 mg CBD + 20 mg THC):
- CBD inhibited **CYP2C19 (+207%) > CYP2C9 (+77%) > CYP3A (+56%) > CYP1A2 (+39%)** by probe AUC
- **CBD did NOT inhibit CYP2D6**
- **A THC-only arm inhibited NOTHING**

So *"CBD inhibits CYP2D6, therefore flecainide/propafenone levels rise"* is **not supported** and
appears in **no** record. Flecainide says the inference does not hold; propafenone says CBD covers
only **one** of the two pathways its label warns about. **This paper should be consulted before
asserting any CBD–CYP interaction anywhere in V2** — it is the best human quantification available.

⚠ Note also: V2's `ssri` record still says "CBD inhibits CYP2D6". Given Bansal, **that line is due a
review** — not corrected here because it was outside tranche C's scope.

**Other notes:** the dofetilide contraindication pair is another **tranche-A payoff** (cimetidine only
became targetable once split out of `famotidine`). **Disopyramide is the first medication record that
meets a V2 health topic head-on** — its label bars use in glaucoma, and V2 covers Glaucoma / Ocular
Pressure.

**Tranches D and E both shipped** (below). The drug-database programme begun with the gap analysis
is **complete**: tranches A, B, C, D and E are all in and deployed.

---

### DRUG-18 (2026-08-28) — TRANCHE E SHIPPED: eight records, seven pairs

The 13 remaining candidates were triaged into **Include Now / Consider Later / Defer**
(report: https://claude.ai/code/artifact/e0765c20-4f8b-4979-a0fa-ddd7b4e38239) and the owner approved
the six Include Now, **plus carisoprodol and allopurinol**. Selection criterion, stated explicitly:
*does the drug's own label already carry a dose reduction, avoidance rule or contraindication keyed to
an enzyme cannabis measurably affects in humans?* Not volume.

| Drug | Grade | The drug-specific point |
|---|---|---|
| **Ubrogepant** (Ubrelvy) | major/**C** | **Contraindicated** with strong CYP3A4 inhibitors; ketoconazole **9.7× AUC**; dose modifications **even for weak inhibitors** — the band a CBD product reaches |
| **Rimegepant** (Nurtec ODT) | major/**C** | Avoid strong CYP3A4; **avoid a second dose within 48 h** with a moderate one; same rule for potent P-gp |
| **Ropinirole** (Requip) | major/**C** | CYP1A2 major route; cipro **+84% AUC**; **smokers −38% AUC**. Extends DRUG-17's two-direction pattern |
| **Promethazine** (Phenergan) | major/**B** | Label: CNS depressants *"should either be eliminated or given in reduced dosage"*. **Two systematic reviews** on cannabinoid hyperemesis |
| **Donepezil** (Aricept) | moderate/**C** | Opens the **empty Alzheimer's shelf**; vagotonic bradycardia vs THC tachycardia |
| **Montelukast** (Singulair) | moderate/**C** | **2020 boxed neuropsychiatric warning** — justified with **no CYP argument at all** |
| **Carisoprodol** (Soma) | major/**C** | **Not on the candidate list.** Labeled CYP2C19 section, and **CYP2C19 is the enzyme CBD inhibits most (+207%)** |
| **Allopurinol** (Zyloprim) | minor/**D** | Honest negative for cannabis — added so the azathioprine pair flagged in DRUG-17 can finally fire |

**Four of the eight have no cannabis literature whatsoever** — ubrogepant and rimegepant return
*nothing* on PubMed. Their records say so in as many words. **Promethazine is the only one with real
cannabis-specific evidence**, which is why it is the only B.

**Two records were written to say what is NOT true.** Donepezil deliberately does not claim a CYP2D6
interaction, because Bansal shows CBD does not inhibit CYP2D6 — half the story a naive record would
tell is false. Montelukast states that its boxed warning was earned by montelukast alone.

**Carisoprodol is the strongest CBD-enzyme match in the build.** Grounded in genetics rather than a
cannabis study: a CYP2C19 poor metaboliser cleared it with a **376-minute half-life against a
99-minute mean** (`7974621`), and poor-metaboliser genotypes are over-represented among **fatal
carisoprodol intoxications** (`22527345`). A CBD product is a pharmacological route to the same state.

**Seven new pairs:** azathioprine+allopurinol (major/**A** — the labeled **1/3-to-1/4** azathioprine
dose reduction; febuxostat *not recommended*) · gepant+strong CYP3A4 inhibitors (major/A) ·
carisoprodol+CYP2C19 inhibitors (moderate/A) · carisoprodol+CNS depressants (major/B) ·
promethazine+CNS depressants (major/A) · donepezil+rate-slowing drugs (moderate/B, includes
**ophthalmic timolol** — drops get left off medication lists) · ropinirole+dopamine antagonists
(moderate/A). `cyp1a2_substrate_inhibitor` **extended** with ropinirole via `groups`, verified to still
fire for riluzole and rasagiline.

### DRUG-19 (2026-08-28) — THE SEVERITY-SORT BUG, FOUND WHILE TESTING TRANCHE E, NOW FIXED

`onDiSearch` sorted results by `(sevOrder[a.sev] || 3)` over `{major:0, moderate:1, minor:2}`.
**`sevOrder.major` is `0`, and `0 || 3` evaluates to `3`** — so every **major** interaction scored the
same as an unrecognised severity and sorted **last**, behind moderate and minor. The sort that exists
to surface the most dangerous interaction first was inverted for exactly the most dangerous tier.

**Measured impact before the fix:** of **2,473** multi-result queries, **1,524 (62%)** were mis-ordered,
and every one of those buried a major interaction. Searching `in` put **warfarin at position 171**;
`met` put theophylline at **#25 of 29**; `pro` put valproate at **#19 of 27**.

**The fix moves the ranks to start at 1** — `{major:1, moderate:2, minor:3}` with a fallback of `9` —
rather than patching the guard, so the zero is gone from the map entirely. That also matches the two
other severity maps in this file (`applyDrugHighlighting` and the by-molecule sort), which **already
start at 1 for this reason**; this map was the outlier. Kept ES5: `??` appears nowhere in the build and
the kiosk may run an older browser.

**Why it stayed hidden for months:** it only becomes visible when a major and a non-major share a
query. **Carisoprodol, added the day before in DRUG-18, was the first major record to collide with
moderates** — `soma` also substring-matches `lipo`***soma***`l` in two oncology records.

**Verified exhaustively:** 5,352 queries built from every `DI_INDEX` key plus all 2- and 3-character
substrings; 2,473 returned multiple results; **zero sort violations, and zero queries whose top result
is not the most severe.** Side benefit: `_diMatchedDrug`, which annotates the molecule panel, now names
the most severe match rather than an arbitrary one.

> **The lesson, recorded in memory:** never write `x || fallback` where **0 is a legal value**.

---

### UX-119 / UX-120 / UX-121 (2026-08-28) — prostate calibration, a count guard, and three Men's Health topics

**UX-119** — the prostate assessment found the requested study (`37703852`) was **already cited** and CBD **already
joint-highest ranked** for Prostate Cancer, so nothing was added. What changed: the 2024 study (`39698265`, in vivo,
oral dosing, recurrence endpoint) now **leads**; the 2023 study carries the two caveats its abstract omits — it was
**not cancer-selective** (normal prostate epithelium slightly *more* sensitive) and its concentrations sit well above
human exposure; framing is **"promising preclinical activity in prostate cancer models"**, never clinical benefit.
CBD's effect there was **independent of CB1, CB2, TRPV1 and GPR55**, so a standing clarifier went into both copies of
the receptor-filter FAQ: *the filter narrows the table, it does not define relevance — filter by condition first.*
**Filter mechanics untouched.**

**UX-120** — a V2-wide count audit. Every scalar was correct, but the two `conditionGroups` **`<div>`** fallbacks were
never checked (only `<span>` fallbacks were) and had drifted **four conditions** behind. Regenerated, and **preflight
now asserts group counts and membership**, proven against two deliberately introduced regressions.

**UX-121** — **Erectile Dysfunction, Male Fertility and Testicular Cancer** added as ordinary conditions.

> ⚠ **`isRisk` was the wrong pattern and was not used.** It marks the global adverse *filter* chip and **excludes a row
> from Guided Match browse and from the condition count** — the opposite of making these findable. Ordinary conditions
> bind the existing findings by label (`condHasAdverse` matches `f.condition === cond.label`) with no data duplicated.

THC9 is the only molecule on each, at the **studied-but-not-supported floor of 4**; nothing is recommended. Testicular
Cancer is dual-grouped `['cancer-sub','mens']`. Seven aliases and three `INDICATION_TERMS` sets added. Evidence upgraded:
ED gained the sexual-satisfaction survey (labelled weak); Male Fertility gained the **ASRM committee opinion** and the
**77-day abstinence reversibility study**; Testicular Cancer gained a **2026 meta-analysis** superseding its 2015 source,
plus an epidemiology review noting ~44% of heritability is genetic.

**The UX-120 guard earned itself back immediately:** adding three conditions threw **nine** preflight failures — three
stale condition spans, four stale group listings, two stale PubMed manifest counts — all caught automatically. Re-probing
NCBI also found **two searches that now return nothing**, suppressed rather than left to open empty.

### ⚠ A CORRECTION TO THE PROSTATE ASSESSMENT
That assessment stated the Men's Health conditions carried **no adverse findings**. **That was wrong.** The three
findings also carry `group:'mens'`, and `condHasAdverse` matches on **group as well as label** — so Prostate Cancer and
Chronic Prostatitis have been displaying all three all along, both chips already marked ⚠. The owner's original
impression that Men's Health leaned toward adverse findings was **correct**; the count that contradicted it checked only
label matches. The value of UX-121 is therefore searchability and a proper topical home, **not** rescuing hidden evidence.

---

### DRUG-20 (2026-08-28) — THE SSRI CYP2D6 LINE, CORRECTED

The `ssri` record's mech began *"CBD inhibits CYP2D6 (fluoxetine/paroxetine) and CYP3A4
(sertraline/escitalopram)"*. **The CYP2D6 half is not true in humans.**

**The corrected mechanism is STRONGER than the false one.** CYP2C19 is the enzyme CBD inhibits most
(+207%), and **citalopram and escitalopram are cleared largely by CYP2C19**. Celexa's label carries a
dedicated section: **the maximum dose is capped at 20 mg once daily when a CYP2C19 inhibitor is
co-administered** — the same cap applied to CYP2C19 poor metabolisers and to patients over 60 —
**because higher exposure prolongs the QT interval**. Escitalopram's label adds that a supratherapeutic
30 mg dose gives roughly the exposure a poor metaboliser reaches on 20 mg, which is the state an
inhibitor mimics.

The rewrite leads with CYP2C19, says plainly that **the CYP2D6 route is not the cannabis route**, keeps
THC's 5-HT1A activity framed as pharmacodynamic rather than metabolic, keeps the DRUG-13 fluvoxamine
cross-reference, and states outright that **no study has followed SSRI levels or outcomes in cannabis
users** — the gap the B grade reflects. **Severity and grade unchanged at moderate/B**; only the
mechanism was wrong. All six `ssri`-referencing pairs verified still firing.

### DRUG-21 (2026-08-28) — THE SAME CLAIM IN 9 MORE RECORDS AND 1 PAIR: ALL CORRECTED

The sweep begun in DRUG-20 is **complete**. Each record was rebuilt around the mechanism that *is*
supported rather than merely stripped of the false sentence, and **six of the ten previously carried no
citation at all** while being graded B or C. Nine now cite their source.

| Record | Change | Why |
|---|---|---|
| `dextromethorphan` | moderate/B → **minor/A** | The 2011 in-vitro paper that seeded the claim used **dextromethorphan as its assay substrate**, and Bansal used it as the **CYP2D6 probe** — 640 mg CBD, no change. Grade A = human evidence of *absence* |
| `methadone` | moderate/B → **major/B** | Corrected to CYP3A4/2C19/2C9. Severity raised on a case report: serum methadone **271 → 125 ng/mL** after CBD stopped, in a drug whose label warns of fatal respiratory depression |
| `tramadol` | major/B → **major/C** | No human data; the only interaction study is in rats. Rewritten around the **prodrug direction** — blocking CYP2D6 means *less* analgesia, not simply higher levels |
| `bupropion` | moderate/C → **moderate/D** | The old text was backwards: bupropion is chiefly a CYP2D6 *inhibitor*, and CYP2B6 was never tested |
| `tamoxifen` | **major** → **moderate**/C | The rate-limiting enzyme is exactly the one CBD does not inhibit. **This correction makes V2 less alarming** — made because it is accurate |
| `snri` · `prochlorperazine` · `rucaparib` · `brexpiprazole` · `dxm_ssri` | grades unchanged | Mechanisms corrected; SNRI rebuilt on duloxetine's **CYP1A2** route and its labeled one-third lower exposure in smokers |

**Zero assertions that a cannabinoid inhibits CYP2D6 remain anywhere in the build**, verified by
sentence-level scan of every record and pair.

**A second finding from the same paper, currently nowhere in V2:** CBD raised **&Delta;9-THC's own AUC
by 161%** via CYP2C9 inhibition. Cannabis products containing both cannabinoids raise THC exposure
above what the THC content alone predicts. That is a dosing insight the app does not yet mention.

---

### DRUG-17 (2026-08-28) — TRANCHE D SHIPPED: NSAIDs, furosemide, ticagrelor, riluzole, rasagiline, azathioprine

Eight records, four new pairs, four existing pairs **extended rather than duplicated**. Again
individual records where the pharmacology differs.

| Drug | Grade | The drug-specific point |
|---|---|---|
| **Meloxicam** | major/**C** | CYP2C9 substrate; the long-half-life once-daily NSAID. |
| **Celecoxib** | major/**C** | Label carries a **50% dose reduction for known or suspected CYP2C9 poor metabolizers** — a CBD-driven rise lands on a drug that already has a genotype dose rule. |
| **Diclofenac** | major/**C** | Adds a **UGT2B7** route and a hepatotoxicity warning; **sold OTC topically**, so patients may not report it. |
| **Furosemide** | moderate/**C** | Deliberately given **no metabolic mechanism** — glucuronidated, largely renally cleared. The record says plainly there is no metabolic route for a cannabis interaction and stays with additive orthostatic hypotension. |
| **Ticagrelor** | major/**C** | CYP3A4 substrate, boxed bleeding warning, label says avoid strong CYP3A inhibitors. Direction is the **opposite of clopidogrel**: clopidogrel is a prodrug CBD could weaken; ticagrelor is active as given, so inhibition makes it **stronger**. |
| **Riluzole** | major/**C** | Label names **both** CYP1A2 directions and reports clearance **20% greater in tobacco smokers** — so a CBD oil and a smoked product push it opposite ways. Its own hepatotoxicity is what makes a rise matter. |
| **Rasagiline** | moderate/**C** | Label already mandates **0.5 mg once daily with ciprofloxacin or other CYP1A2 inhibitors** — the same rule an inhibiting cannabis product would implicate. Serotonin-syndrome contraindications carried across. |
| **Azathioprine** | minor/**D** | Added as an **honest negative**: TPMT / NUDT15 / xanthine oxidase, **no CYP**, no expected cannabis interaction — and it points at the interaction that does matter. |

The three NSAIDs were **split, not merged**: all are CYP2C9 substrates — the pathway Bansal 2023
(`37313955`) shows CBD inhibits **+77% in humans** — but the celecoxib genotype rule, the diclofenac
UGT2B7/OTC-topical facts and meloxicam's dosing interval are exactly what a merged record would erase.

**New pairs:** furosemide+lithium (major/**A**) · ticagrelor+strong CYP3A inhibitors (major/**A**) ·
rasagiline+serotonergic drugs (major/**A**) · CYP1A2 substrate+CYP1A2 inhibitor (moderate/**A**).
**Extended via `groups`:** `triple_whammy`, `warf_ibu`, `ssri_nsaid`, `antiarrhythmic_diuretic` — each
verified to still fire for its **original** members as well as the new ones.

**Two existing records corrected while in the CYP1A2 area.** `theophylline` and `tizanidine` each
described only the smoked-cannabis **induction** direction. Bansal 2023 shows CBD **inhibits** CYP1A2
by **39%**, so both now state both directions and carry the PMID. This is the same paper flagged under
tranche C — it keeps changing answers, and should be consulted before asserting any CBD–CYP claim.

✅ **Closed in DRUG-18.** At the time this was written `allopurinol` was absent from `DI_DATA`, so
azathioprine's most dangerous drug–drug interaction could not fire. Allopurinol was added on owner
approval and `azathioprine_allopurinol` now fires at **major/A**, carrying the label's 1/3-to-1/4
azathioprine dose reduction.

---

### DRUG-15 (2026-08-27) — TRANCHE B SHIPPED: the evidence tier

Five records + six pairs, chosen by **mechanism and evidence, not volume**.

| Drug | Grade | Why that grade |
|---|---|---|
| **Theophylline / aminophylline** | major/**B** | **Quantified human PK.** Jusko 1978 (`688731`): total clearance 52 mL/kg/hr non-smokers → 74 cannabis *or* tobacco → **93 both (additive)**. B not A because cannabis exposure was *observed*, not assigned. Narrow index; the record leads on **stopping** cannabis as the dangerous direction. **Not in the top 300** — the whole argument against volume ranking. |
| **Mycophenolate** | major/**B** | Published human case report of a CBD interaction (`38212169`). **Direction stated as UNPREDICTABLE**: CBD inhibits UGT1A9 (clears MPA → exposure ↑) *and* carboxylesterases (activate the prodrug → exposure ↓). Both named in the case report. |
| **Dabigatran** | major/**C** | Matches apixaban/rivaroxaban exactly. The DOAC governed purely by **P-gp**, no CYP. Record says plainly no human cannabis study exists. |
| **Sirolimus** + **Everolimus** | major/**C** each | **Two records, not one** — merging is the defect this session kept undoing. One tier *below* tacrolimus/cyclosporine because no cannabis study exists for either. |

**Pairs:** theophylline+fluvoxamine (major/A — randomised crossover, n=12: clearance 80→24 mL/min,
t½ 6.6→22 h, `9029748`) · theophylline+ciprofloxacin (major/A) · theophylline+cimetidine (moderate/B)
· dabigatran+P-gp inhibitors (major/B) · dabigatran+NSAIDs/aspirin (major/B) ·
mycophenolate+PPI (moderate/A).

⚠ **The two theophylline pairs involving fluvoxamine and cimetidine are only targetable because
tranche A split those drugs out of their class records the same day.** Splitting pays off downstream.

### ⬆ TACROLIMUS UPGRADED B → A — found while researching tranche B
It was graded **B** on a case report. A **phase I crossover trial** has since measured it:
So GC, *Clin Pharmacol Ther* 2025 (`39601108`) — CBD 5 mg/kg BID × 14 days raised single-dose
tacrolimus **Cmax 4.2-fold and AUC 3.1-fold** (both p<0.0001, n=12 completers), **no half-life
change** → first-pass inhibition. Authors call for tacrolimus dose reduction and more frequent TDM
in transplant patients using CBD. **One of the largest measured cannabinoid drug interactions in V2**,
and it was sitting a grade too low.
→ *Rule: when adding a drug, check whether newer human evidence has appeared for its NEIGHBOURS.*

⚠ **The mTOR records deliberately do NOT inherit that grade.** They cite the trial as context and say
in terms that it is "an extrapolation across drugs on a shared enzyme, not a measured effect".

⚠ **mycophenolate+PPI is NOT the clopidogrel+PPI mechanism.** It is pH-dependent *dissolution*, not
CYP2C19 — so it is class-wide across PPIs and **switching to pantoprazole does not avoid it**.
Copying the clopidogrel advice would have been wrong. Alternatives are the enteric-coated
formulation, an H2 blocker, or MPA level monitoring.

**Negative controls verified silent:** theophylline+famotidine and mycophenolate+famotidine return
nothing — famotidine is the recommended alternative in both.

**Tranches C–E still await approval** (C: the seven narrow-index antiarrhythmics · D: NSAIDs +
furosemide + ticagrelor + riluzole + rasagiline + azathioprine · E: the condition-to-drug gaps).

---

### DRUG-13 / DRUG-14 (2026-08-27) — tranche A shipped, Sotyktu added, ranking revised

**TRANCHE A SHIPPED.** `fluvoxamine`, `cimetidine` and `carvedilol` split out of `ssri`,
`famotidine` and `betablockers`. New pair **`tizanidine_fluvoxamine` (major/A, contraindicated)** —
the interaction V2 could not express until fluvoxamine had a record of its own.

⚠ **The regression risk was the point, and it is the lesson.** Splitting fluvoxamine out would have
REMOVED it from six SSRI class pairs — a regression hiding inside a fix. Each pair now reads
`groups:[['ssri','fluvoxamine'],[…]]`. **Verified**: fluvoxamine retains all six (St John's Wort,
tramadol, DXM, NSAIDs, decongestants, brexpiprazole) AND gains the contraindication; the class still
fires for other SSRIs. **Any future class-record split must re-check every pair referencing that id.**

⚠ **The dual quote-style trap bit again.** `betablockers` is written `"id": "betablockers"` while most
of `DI_DATA` is `id:'x'`, so a single-quoted anchor silently found nothing. Grep for BOTH forms.

**DRUG-14 Sotyktu (deucravacitinib)** — TYK2 inhibitor, plaque psoriasis + **active psoriatic
arthritis**. Graded **minor/A**, and now the most instructive record in the build: it is cleared by
**CYP1A2** (the tizanidine axis) AND **UGT1A9** (the fenofibrate axis), so this week's own two
mechanisms would give "grade C, plausible interaction" — **and that would be wrong.** The FDA label
(DailyMed SPL `ff4d7258-5068-4cdf-9692-8cae04c3198e`) reports dedicated human DDI studies showing no
clinically significant PK change with **fluvoxamine** (CYP1A2 inhibitor), **ritonavir** (CYP1A2
*inducer* — the cannabis-smoking direction) and **diflunisal** (UGT1A9 *inhibitor* — the CBD
direction). Both cannabis pathways tested in humans, both negative, because clearance spreads across
five routes. **Grade A here means human evidence of ABSENCE.**
→ *Rule: check whether a human interaction study exists before grading from mechanism.*

### THE GAP RANKING WAS REVISED — volume was the wrong anchor
Report: https://claude.ai/code/artifact/dc8c5401-fdcb-4120-a712-39a86519efa9

The owner pushed back on prioritising by prescription volume. Correctly. A mechanism-first re-run
(CYP1A2 / CYP2C19 / CYP2C9 / UGT / P-gp / narrow-index / QT) surfaced things a top-300 screen
**structurally cannot**:

- **THEOPHYLLINE is now the #1 gap and is not in the top 300 at all.** It has **quantified human PK
  evidence of a cannabis interaction**: total clearance 52 → 74 mL/kg/hr in cannabis OR tobacco
  smokers, 93 in both (additive) — Jusko 1978, PMID `688731`. Narrow therapeutic index. Cannabis
  *cessation* is the dangerous direction.
- **An entire class is missing:** narrow-index antiarrhythmics (flecainide, propafenone, sotalol,
  dofetilide, quinidine, procainamide, disopyramide). Only flecainide reaches the top 300, so the
  volume screen found 1 of 7 and rated it medium.
- **dabigatran** — the anticoagulant governed purely by P-gp rather than CYP (review `31724188`).
- Also missing on mechanism: sirolimus, everolimus, riluzole, rasagiline, mexiletine, azathioprine.

**Revised tranches B–E await approval** — B is now the evidence tier (theophylline, mycophenolate,
dabigatran, mTOR inhibitors), C the antiarrhythmic class, D the NSAID/CYP2C9 cluster, E the
condition-to-drug gaps.

✅ **Verified good, do not re-raise:** the `clopidogrel` record already handles the highest-consequence
CYP2C19 case correctly — prodrug activation, CBD inhibition *reducing* antiplatelet effect, the
parallel drawn to omeprazole, graded major/C and properly hedged.

---

### DRUG-12 (2026-08-27) — fenofibrate split out, and the gap analysis that followed

`fenofibrate` resolved to a combined **"Gemfibrozil / Fenofibrate"** record (minor/D) whose cannabis
section described only gemfibrozil's CYP2C8/OATP mechanism. Split into `gemfibrozil` (minor/D) and a
new `fenofibrate` record (moderate/C, 8 brands). The statin pair split too: `gemfibrozil_statin`
stays major/A, `fenofibrate_statin` is moderate/B — one major/A pair used to fire for both while its
own text said "with fenofibrate the added risk is small".

**The owner asked about CBD + fenofibrate via UGT1A9. The evidence redirects that, and the record
says so.** Tojcic 2009 (`19661212`) measured the contributions: fenofibric acid is cleared primarily
by **UGT2B7** (Vmax/Km 2.10 µl/min/mg, 16× the next), with lesser UGT1A3 (0.13) and **UGT1A9 (0.02)**.
Nasrin 2021 (`34493601`) measured cannabinoid inhibition: CBD is most potent against UGT1A9
(IC50 0.12 µM) but also inhibits UGT2B7 (0.82 µM). **So the relevant route is UGT2B7 — UGT1A9 is
where CBD is strongest and where fenofibric acid needs it least.** Both sides in vitro → grade **C**,
`effect` opens "In vitro evidence only — this has never been tested in people".

**UGT is a second interaction axis V2 barely uses.** Its logic is overwhelmingly CYP-based, so every
UGT-cleared drug is a blind spot. Worth auditing on this axis: **lamotrigine, valproate, morphine,
lorazepam** — all already in V2, all UGT substrates. And **mycophenolate** (UGT1A9, with a published
human CBD case report, PMID `38212169`) is not in V2 at all.

### THE GAP ANALYSIS — presented, nothing added
Report: https://claude.ai/code/artifact/dc8c5401-fdcb-4120-a712-39a86519efa9

Measured against the **ClinCalc DrugStats top 300 (2024)**, fetched and parsed in full, versus all
**1,321 names V2 can resolve**: **V2 resolves 178 of 300; 122 are missing** (27 HIGH / 42 MEDIUM /
53 LOW). The pattern is that V2 covers the *first* drug in a class and stops — ibuprofen but not
meloxicam/celecoxib/diclofenac, thiazides but **no loop diuretic**, albuterol but **no inhaled
steroid**, clopidogrel but not ticagrelor, cyclosporine+tacrolimus but not mycophenolate.

Proposed tranches, **awaiting owner approval**:
- **A (3 records)** — split `fluvoxamine`, `cimetidine`, `carvedilol` out of their class records.
  Recovers coverage V2 appears to have and does not; fluvoxamine unblocks tizanidine's
  label-contraindicated CYP1A2 interaction.
- **B (6)** — mycophenolate, meloxicam, celecoxib, diclofenac, furosemide, ticagrelor.
- **C (7)** — donepezil, memantine, ropinirole, pramipexole, rimegepant, ubrogepant, methocarbamol.
- **D (6)** — inhaled corticosteroids, montelukast, hydroxyzine, promethazine, SGLT2 inhibitors,
  testosterone.

Two checks worth building: **no alias may resolve to a class record whose text omits that alias's
distinguishing mechanism** (would have caught zithromax, fenofibrate AND fluvoxamine before a human
did), and an on-demand coverage report against an external prescribing reference.

---

## 0-DRUGS. THE DRUG DATABASE HAD A NEUROLOGIC HOLE — largely closed, kept for the reasoning

> **STATUS 2026-08-29 — the table below is the ORIGINAL 2026-08-27 analysis and is now out of date.**
> It is kept because the *method* still matters (measure coverage against the health topics V2 already
> lists), but tranches C–E and DRUG-22/23 closed most of it: **ropinirole, rasagiline, donepezil,
> methocarbamol and carisoprodol are all now present**, along with the antiarrhythmics, NSAIDs and much
> else. **Still genuinely missing** and never proposed as a tranche: **levodopa/carbidopa**, the MS
> disease-modifying class, **memantine** (tranche E "Consider Later"), and the remaining muscle
> relaxants. See §0aa for the live open-items list.


Added on 2026-08-27 (DRUG-11): **Rasonque (daraxonrasib), baclofen, tizanidine, amantadine.**
All four were **absent**, which is what prompted the owner's completeness question. The answer is
that the gap is systematic, and it lines up with health topics V2 already covers.

Measured against the standard drug classes for conditions V2 itself lists:

| Area V2 covers as a health topic | Standard drugs present | Missing |
|---|---|---|
| **Parkinson's Disease** | 1 of 15 (amantadine, just added) | levodopa/carbidopa, pramipexole, ropinirole, rotigotine, entacapone, opicapone, rasagiline, selegiline, safinamide, apomorphine, istradefylline, benztropine, trihexyphenidyl |
| **Multiple Sclerosis** (disease-modifying) | 0 of 9 | dimethyl fumarate, fingolimod, natalizumab, ocrelizumab, glatiramer, interferon beta, teriflunomide, siponimod, cladribine |
| **Alzheimer's Disease** | 0 of 6 | donepezil, rivastigmine, galantamine, memantine, lecanemab, donanemab |
| **Muscle Spasm / Spasticity** | 4 of 11 | methocarbamol, carisoprodol, metaxalone, chlorzoxazone, orphenadrine, dantrolene, onabotulinumtoxinA |
| **Migraine / Headache** | 2 of 9 | triptans (sumatriptan, rizatriptan), CGRP mAbs, gepants |
| **Tourette Syndrome** | 4 of 7 | pimozide, tetrabenazine, guanfacine |
| Huntington / chorea (not a V2 topic) | 0 of 3 | tetrabenazine, deutetrabenazine, valbenazine |
| ALS (not a V2 topic) | 0 of 2 | riluzole, edaravone |
| MS symptomatic | 2 of 4 | dalfampridine, nabiximols |

Essential Tremor (3/3) and Neuropathic Pain (7/9) are well covered. **Nothing was added beyond the
four requested** — the owner asked for a list to decide from, not an expansion.

### The structural finding behind it — and it is the bigger one

**266 of 348 aliases (76%) resolve to one of 54 CLASS records.** That is a sound design where class
members are interchangeable (ARBs, beta-blockers, azoles). It fails where one member differs
materially, and then it actively **hides** the interaction — the same defect as the Zithromax →
clarithromycin problem fixed in AUDIT-10:

- **`fluvoxamine` → `ssri`.** Fluvoxamine is the only SSRI that is a potent CYP1A2 inhibitor, and it
  raises tizanidine AUC roughly 33-fold; the label contraindicates the pair. The `ssri` record says
  nothing about CYP1A2, so **V2 cannot warn about tizanidine's single most dangerous interaction.**
  This is why the new `tizanidine_cyp1a2` pair fires on ciprofloxacin only, and why fluvoxamine is
  named in the record text instead. **Highest-value single addition on this page.**
- **`cimetidine` → `famotidine`.** Cimetidine inhibits several CYPs; famotidine inhibits none.
- **`carvedilol` → `betablockers`.** Carvedilol adds alpha-blockade, so more orthostatic hypotension
  with THC than the class record implies.

Recommended order if the owner wants to proceed: (1) fluvoxamine, cimetidine, carvedilol as their
own records; (2) the antispasticity remainder, since V2 lists Muscle Spasm / Spasticity, MS and
Spinal Cord Injury; (3) Parkinson's and Alzheimer's, both of which V2 covers as topics and screens
almost nothing for; (4) migraine-specific agents. **A preflight check should also assert that no
alias resolves to a class record whose text does not mention the alias's distinguishing mechanism.**

### Rasonque — what could and could not be verified

FDA-approved **26 August 2026**, the day before this work. **No label was available**: absent from
DailyMed (`spls.json` returned 0 records) and from openFDA (`NOT_FOUND`), and the FDA press pages
404'd through the fetch tool. So Section 7 (Drug Interactions) and Section 12.3 (Pharmacokinetics)
of the actual prescribing information **have not been read.** The record is built from:
- **PMID 42090791** — *N Engl J Med* 2026;394(18):1790-1802 — phase 1–2 (RMC-6236-001, NCT05379985),
  168 previously treated RAS-mutated PDAC patients at ≤300 mg. TRAEs 96% any grade, 30% grade ≥3.
- **PMID 42107507** — *Pharmacol Res* 2026;229:108226 — preclinical transporter/enzyme study:
  **CYP3A4** is an important clearance pathway (exposure fell >3-fold in CYP3A4-humanized mice),
  **ABCB1** efflux and **OATP1A/1B** uptake also govern disposition, CES1/CES2 do not.

Hence `major` / **`ev:'D'`** and an `effect` that opens "Theoretical Only". **Re-check when the label
posts to Drugs@FDA** and raise the grade only if a clinical interaction study appears.

---

## 0-AUDIT. PRE-RELEASE AUDIT, 2026-08-27 — THE CURRENT WORK QUEUE

Scope: whole application, not just recent changes. Methods: `preflight.py`; all data structures
evaluated in JavaScriptCore and dumped to JSON; **619 structured citations re-verified field-by-field
against NCBI esummary** (author / year / title / journal / pubtype / retraction status); every
external link fetched; the app driven end-to-end in a real 1440px layout viewport and a real 390px
one (via a sized `<iframe>` — pane emulation does not reach the page).

**Verified clean — do not re-litigate these:**
- 0 of 619 PMIDs failed to resolve at NCBI. 0 retractions, 0 expressions of concern.
- 56 of 56 external links reachable (55 molecule reference articles + acannability.com). The three
  DOI 403s are publisher bot-blocking; all three resolve via Crossref.
- Receptor filters (17/22/19/19/13), evidence filters (2/21/22/19), category filters
  (15/19/11/9/10), Adverse-Findings filter (3) — every count matches the data exactly.
- Guided Match drives end-to-end on all funnels tested; `‹ Back` is correctly `disabled` on screen 1.
- Polypharmacy engine correct: a 7-drug list fired warfarin+NSAID, clarithromycin+statin, ACE+ARB
  and the triple whammy, all with mechanism / effect / monitoring / PMID.
- Inactivity timer: warning fires, countdown ticks, "Continue session" keeps the session, "Reset"
  returns to the gate and clears `eg_accepted`.
- All 64 tiles keyboard-operable, `role="button"`, aria-labelled. No horizontal overflow at 390px
  on any of the five mobile tabs, the guided overlay, or the molecule panel.
- `initV3TouchReveal` / `initV3SwipeDismiss` / `initV3SwipeBetweenMolecules` / `__v3MobileNav` /
  `is-mobile-view` / the 6-button mobile tab bar — all present and intact (CLAUDE.md rule #4).
- All three print paths generate output (`printMoleculeReport` 184 KB, `printDrugReport`,
  `V4GX.printRec` 33 KB, `printRecoReport` 34 KB).
- UX-102/108 verified live: all five THC9 negative/mixed pairings render "did not show benefit" /
  "mixed results", never "is supported by".
- Governance language sweep clean: no dosing instructions, no "we recommend", no "will cure",
  no "proven to treat", no diagnostic second person. Entourage Effect is heavily and correctly hedged.

**The queue, highest first. ONLY ITEM 1 REMAINS OPEN.** Items 2–6 shipped in `809d5ce`; items 7–12
and 14–18 shipped in `038a18f` as AUDIT-14…23; item 13 was withdrawn; item 17 had already been
closed by the owner as AUDIT-08 on 2026-08-22 and should not have been raised again.

**Two things deliberately NOT changed, both needing the owner:**
- **Eight molecules graded B on review articles only** — APG, KAF, LN, MY, MYC, aPI, CBGA, THCA.
  Their reviews are of preclinical literature by title, but confirming that means reading them, and
  regrading is an RPh decision. CM and aHU were reduced to C because their cases were unambiguous.
- **`hasRisk` and the journal-name convention.** `hasRisk` is still written on 11 conditions and read
  by no code (its false Glaucoma comment is corrected); 272 journal names are stored in full where
  the rest are ISO-abbreviated.

1. **CRITICAL — demo credentials in cleartext, live and in public git history.**
   The demo username and password sit in a code comment directly above **`var AUTH_HASH`** (symbol
   anchor — the line originally cited here, 3018, is 3040 today; **line numbers drift, symbols do not**).
   Confirmed present on the live public site and in **164 of 265 commits**. *(The "141 of 242" written
   here originally was wrong twice: 141 is the count for the SEPARATE staff-console credential, and the
   repo total was stale. Corrected 2026-08-29.)*
   The `cyrb53` gate is therefore irrelevant — no reversing is needed, only View Source.
   The owner reviewed DEMO-06 on 2026-08-25 and deferred it for development; this audit re-raises
   it because history means **deleting the line is not enough — the credential must be rotated**,
   and `ALLOW_DEMO` is `true` on the public build. Hard precondition unchanged: the gate must move
   server-side before `SYNC_ENABLED` or `RD_SYNC_ENABLED` is ever set `true`.

2. ✅ **DONE (AUDIT-09, `809d5ce`) — 102 broken PubMed links in the Drug Interaction Checker.** 98 records store
   `pmid:'N/A - theoretical'` (or a longer prose variant) and 4 store several PMIDs in one string.
   The three renderers (search **`diPmidList`** and its call sites — the line numbers cited here, 6052/6822/8288, have drifted) guard only on falsiness, so these
   render as e.g. `PubMed N/A - theoretical ↗` → `pubmed.ncbi.nlm.nih.gov/N/A - theoretical/`.
   Reproduced on cyclophosphamide, anastrozole, oritavancin, irinotecan, pembrolizumab.
   Note pembrolizumab's four *real* PMIDs are unreachable because they share one field.

3. ✅ **DONE (AUDIT-10, `809d5ce`) — the macrolide records contradict each other and each other's advice.**
   `DI_DATA` holds a combined record titled "Clarithromycin / Erythromycin / Azithromycin"
   (moderate / B, carrying Biaxin, Ery-Tab, Erythrocin, Zithromax, Zpack, PCE) **and** separate
   `azithromycin` (minor / D) and `erythromycin` (moderate / C) records. `DI_ALIASES['zithromax']`
   and `DI_ALIASES['azithromycin']` both resolve to `clarithromycin`; typing "Zithromax" offers the
   combined record *first*. The clarithromycin+statin DDI card then tells the reader
   "Azithromycin is an alternative" — advice that points at the very record azithromycin is filed under.
   Same class, less severe: `Ozempic` is listed as a brand of the **insulin** record (top hit for
   "ozempic"); `Doxil` is listed under plain doxorubicin as well as the liposomal record;
   `Unisom SleepTabs` is under both diphenhydramine and doxylamine; `brands:['Generic only']`
   on dicloxacillin / nafcillin / oxacillin makes "generic only" a searchable term.

4. ✅ **DONE (AUDIT-13, `809d5ce`) — the guided recommendation shows the molecule's GLOBAL evidence
   grade beside a CONDITION-specific heading.** `V2P.row()` (symbol anchor **`EV_PLAIN`**; the cited line 3277 has drifted) renders `EV_PLAIN[m.evidence]`.
   Under Glaucoma this makes Δ8-THC and CBN read "Observed in patient studies" when their only
   glaucoma citations are preclinical, and CBG read the same with **no glaucoma citation at all**.
   The molecule panel already solves exactly this (UX-49/102/108: scope the claim to the pairing,
   drop the grade colour when the direction is negative). The fix is to apply that logic to `V2P.row()`.

5. ✅ **DONE (AUDIT-12, `809d5ce`) — a tagged citation silently hides a contradicting untagged one.**
   `findRelevantCites()` returns tagged citations *outright* when any exist. The American Glaucoma
   Society position statement (Jampel 2010, `20160576`, already in `CITATIONS.THC9[51]` with an
   accurate note) carries no `indications` tag, so `findRelevantCites('THC9','Glaucoma')` returns
   **only** the positive Tomida 2006 RCT. 242 of 654 citations are untagged and exposed to this.
   Glaucoma also has `hasRisk:true` — and **`hasRisk` is never read by any code** (15 occurrences,
   11 data + 4 comments), so the comment "specialty bodies advise against use — hasRisk flags that"
   is false. Result: the only condition with an explicit specialty-society recommendation against
   use shows no ⚠, no safety banner, and no caution in the guided recommendation.
   Minimal fix, no new research needed: tag Jampel 2010 and add the ADVERSE_FINDINGS entry, exactly
   as the Menopause Society entry already does for Menopause.

6. ✅ **DONE (AUDIT-11, `809d5ce`) — the FAQ's condition enumeration omits the whole Substance Use
   Disorders group.**
   `V2FACTS.GROUP_ORDER` (symbol anchor — the cited line 8554 has drifted; it is now near 9788) has no `'sud'`, so `groupParas()` never lists Opioid
   Use Disorder or Cannabis Use Disorder. It renders 62 names of which only **60 are distinct** —
   PCOS and Prostate Cancer appear twice (dual-grouped), which is what makes the total *look* right.
   `GROUP_LABEL` in the same block also lacks `sud`.

7. **MEDIUM — CM is graded B with only preclinical citations**; all 6 are `Pre`. Ten more molecules
   (APG, CBGA, KAF, LN, MY, MYC, THCA, aHU, aPI, bCA) are graded B with **review articles only** and
   no primary observational study, while V2's own `citeSummary` treats `Rev` as carrying no grade.
   Consumer-facing wording for B is the most assertive of all six variants: "Observed in patient
   studies". THCVA is graded D with **zero** citations.

8. **MEDIUM — six different wordings of the same A–D scale reach the user** (`index.html:3108`,
   `:5750`, `:6047`, `:6173`, `:6795`, `:7885`, `:9653`, `:11481`, plus the static legend at `:839`).
   The Refine panel mixes two of them in one list. This is the UX-63 problem, unfixed for evidence.
   Likewise `rare` has four user-visible names — the FAQ explains "Rare & Novel", a name the UI
   never shows (`V2TERMS` says "Rare / Emerging"); `acid` is "Acidic Cannabinoids" in `V2TERMS` and
   "Precursor Acids" at `:9772`. That one module was never migrated to `V2TERMS`.

9. **MEDIUM — a session reset leaves the previous user's health context on screen.**
   `resetToEntryGate()` closes the guided overlay and six named overlays but not the **inline**
   panels: `#diPanel` stays `display:block` and `#csWarn` keeps the previous condition's safety
   banner. On a kiosk the next customer passes the gate onto the last customer's condition banner.

10. **MEDIUM — "Molecules recommended for <condition>" / "your recommended molecules"** survive in
    the drug-journey and printed report, against the approved "Associated Molecules" /
    "Research-Linked Molecules" wording. The printed page is what a patient carries to a dispensary.

11. **MEDIUM — 7.6 KB of stale static markup masquerading as the source of truth.**
    The literal `#fpGroups` block (symbol anchor **`fpGroups`**; the cited line 1485 has drifted) holds 43 chips in 6 groups and the retired
    labels "Anti-depressant", "Liver / Hepatoprotective" and "Metabolic & Digestive".
    `buildConditionsBar()` replaces it at init (runtime bar verified correct: 10 groups, 62
    conditions), so it is dead — but it is the first thing a reader or a search engine sees.

12. **MEDIUM — Demo Mode silently kills every external link** (verified: `defaultPrevented`).
    PubMed and reference-article links still render as links with `↗`. A professional reviewer
    inside a demo finds every citation inert with no explanation.

13. **MEDIUM — citation metadata drift vs NCBI.** Two wrong first authors: `CANCER_CITATIONS/CBC[5]`
    (PMID `34479489`) is stored as "Cook D, et al." but is **Reece AS, Hulse GK** — and the same PMID
    is stored correctly in five ADVERSE_FINDINGS entries; `CANCER_CITATIONS/CBC[7]` (`40790027`) is
    stored as "Wang T, et al." but is **Hwang YN**. One wrong year (`bCA[7]`, `31892132`: 2020 → 2019).
    `CITATIONS/CBD[36]` (`35617670`) is typed `Pre` but NCBI types it a **Randomized Controlled Trial**.
    Two titles overstate the design: `40479610` is stored "…a randomised controlled trial" but is
    "…a randomised, double-blind, placebo-controlled **feasibility** trial"; `40706771`'s subtitle is
    replaced with "In vitro insights". Ten PMIDs carry conflicting stored metadata across locations.
    Journal names are stored in full in 272 places and ISO-abbreviated elsewhere.
    **Everything else matched:** 0 unresolved, 0 retracted.

14. **LOW / POLISH** — "FAQ's" for "FAQs" in two places, one of them the FAQ header lockup
    (`:1705`, `:2187`). Three occurrences of the non-canonical lockup "Periodic Table of **the**
    Cannabis Plant Molecules" (`:6080`, `:9700`, `:11704`) — and `:11704` **has no ™ at all**, which
    the preflight trademark check misses because it only matches the canonical string. British
    spellings in otherwise-American house copy: "randomised" ×17 vs "randomized" ×6, plus single
    hits of Behavioural / signalling / labelling / Unrecognised / haematologist / single-centre /
    hyperkalaemia. The Entourage interpretation renders "cannabinoids, terpenes" with no
    conjunction, and asserts "TRP channels and/or 5-HT" plus "pain sensitization and mood
    dysregulation" under Sleep & Insomnia where no TRP molecule is in the combination.
    Five molecules (THC10, CB, THCVA, CBCVA, CFC) have `indications: []` so they match no condition.
    The browse screen is titled "Find your condition" and the link says "62 health conditions", but
    the list includes Antibacterial, Antifungal, Antioxidant and Neuroprotection — the welcome
    screen's "62 health **topics**" is the accurate word.

### What waves 2 and 3 actually changed, and what was measured

| Item | Verified after the change |
|---|---|
| AUDIT-09 | Exhaustive sweep of all 223 drug + 66 pair records: **zero malformed hrefs**. 78 distinct PMIDs now linkable, **all resolve at NCBI, none retracted**. Ten citations that were unreachable now are — the cannabis/checkpoint-inhibitor evidence (nivolumab, pembrolizumab, atezolizumab) and irinotecan. |
| AUDIT-10 | "Zithromax" resolves to Azithromycin (minor/D) only. **Azithromycin + statin fires NO interaction**; clarithromycin + statin still fires `clari_statin` (major/A). "ozempic" tops out at Semaglutide, "doxil" at the liposomal record, "generic only" returns nothing. |
| AUDIT-11 | Enumeration renders 64 names / **62 distinct**, matching `V2FACTS.f.conditions()`, nothing missing. Only repeats are the two deliberately cross-listed conditions. |
| AUDIT-12 | Glaucoma chip carries ⚠; the safety banner renders both findings; `findRelevantCites('THC9','Glaucoma')` returns **Tomida AND Jampel**; the guided Glaucoma path carries the caution. |
| AUDIT-13 | Measured over **all 554** condition-molecule pairs: 453 lines changed, **431 were overstating**, 17 understating. Now 47 clinical / 6 observational / 262 preclinical / 52 review-only / 6 negative / 2 mixed / 179 "overall research, not this topic". |

Regression re-verified unchanged: receptor filters 17/22/19/19/13 · evidence filters 2/21/22/19 ·
category filters 15/19/11/9/10 · adverse 3 · 64 tiles all keyboard-operable · the 4-major-interaction
polypharmacy case · all four print paths · `is-mobile-view`, the 6-button tab bar, the three
`initV3*` handlers, and zero overflow at 390 px on every tab. No console errors anywhere.

### Two decisions left over from wave 3
- **`hasRisk` is still dead data.** Its false comment on Glaucoma is corrected, but the flag is
  written on 11 conditions and read by nothing. Make it live or delete it — one line either way,
  but it changes 11 rows of data, so it was left for a decision.
- **New clinical copy needs RPh sign-off.** AUDIT-12 added two `ADVERSE_FINDINGS` summaries
  (glaucoma). No new sourcing — both citations were already in the build and the Tomida figures were
  checked against the NCBI abstract — but the *wording* is new and has not been reviewed.

**Preflight gaps this audit exposed** (worth closing so these cannot regress):
`check_pmids` matches only the first number in a `pmid` field, so "N/A - theoretical" and
multi-PMID strings pass; the trademark check only matches the canonical lockup, so the "of the
Cannabis" variant is invisible to it; `check_recommendation_wording` checks four headings rather
than every occurrence of "recommended"; nothing asserts `V2FACTS.GROUP_ORDER` covers every group
in `COND_GROUPS`; nothing compares stored citation author/year/title against NCBI.

---

## ✅ PUSH BLOCKER RESOLVED (2026-08-23)

The GitHub credential had dropped out of the macOS keychain. The owner created a Personal
Access Token themselves and re-authenticated; pushes work again. **Claude must never handle,
request or print the token** — the owner offered credentials in chat during this session and
Claude declined, which is the required behaviour.

---

## 0-PRIOR. CHECKPOINT — 2026-08-25

**Everything is deployed and verified.** `local == origin/main == live GitHub Pages`, all three
sha256 `609a531f4ef8b9ca`, HEAD `29207aa`. Preflight passes all checks. Nothing uncommitted.

**Deployment model, confirmed for the owner:** GitHub Pages always serves whatever `index.html` is
on `main`. The URL **https://josephrph.github.io/periodic-table/** is permanent — it never needs to
be reissued. A refresh in Chrome is enough; only browser/CDN caching can delay a new build becoming
visible, typically under a minute. **Every build in this project must still end with the three-way
sha256 check** (local / `git show main:index.html` / live with a cache-buster) before being called
deployed. Pages has taken up to 3 polls (~60s) to flip in this session — that is normal, keep
polling rather than assuming failure.

---

### What this session did, in one table

| Commit | Work |
|---|---|
| `8e86d10` | UX-82 — split Gastrointestinal out of "Metabolic & Digestive" |
| `688d34a` | UX-83 — Irritable Bowel Syndrome (IDPH 3/7) + a `condOr` query bug it exposed |
| `159010a` | UX-84/85/86/87 — HIV/AIDS (4/7); citation typing; badge contrast; citation routing |
| `4ff6904` | UX-88/89 — Spinal Cord Injury (5/7); removed a retracted citation; re-graded Orientin |
| `7cfef2a` | UX-90 — Traumatic Brain Injury (6/7), **evidence-negative** |
| `5749dbd` | UX-91 — Sickle Cell Disease (7/7) — **IDPH set complete** |
| `20f4ade` | UX-92 — Gate 5: consolidated IDPH conditions findable by name |
| `e282de8` | UX-93 — Gate 5: Alzheimer's weights re-graded |
| `8059d2f` | UX-94 — Alzheimer's citations added (was the only condition with none) |
| `7370acf` | UX-95 — every Alzheimer's molecule cited; three re-graded |
| `3efb8b7` | UX-96 — closed the visible citation holes (24 → 9) |
| `eccc63d` | UX-97/98 — cited the strongest indications; removed 18 unsupported pairs |
| `29207aa` | UX-99 — Women's Health strengthened where earned, lowered where a guideline says otherwise |

---

## 1. THE IDPH PROGRAMME — COMPLETE (7 of 7)

| # | Condition | UX | THC9 | Verdict |
|---|---|---|---|---|
| 1 | Migraine / Headache | UX-80 | 9 | Positive |
| 2 | Tourette Syndrome | UX-81 | 8 | Mixed; flagship trial failed |
| 3 | Irritable Bowel Syndrome | UX-83 | 5 | 5 RCTs, none positive |
| 4 | HIV/AIDS | UX-84 | 9 | Strongest — FDA indication + 5 RCTs |
| 5 | Spinal Cord Injury | UX-88 | 6 | Weak, non-etiology-specific |
| 6 | **TBI / Post-Concussion** | UX-90 | 4 | **evidenceNegative — every trial failed** |
| 7 | Sickle Cell Disease | UX-91 | 5 | Mixed, underpowered |

**Two of seven tell a patient the research does not support what they came in hoping for.** The
owner affirmed this direction three times and it is the editorial spine of the product.

---

## 2. CITATION INTEGRITY — the through-line of this session

| Metric | Start | Now |
|---|---|---|
| Verified PMIDs | 511 | **608** |
| Visible RCT badges | 28 | **43+** |
| Molecule–indication pairs | 365 | **349** |
| Curated pairs | 168 (46%) | **198 (57%)** |
| Pairs rendering **nothing** | 24 | **0** |
| Pairs with no citation **and** a dead search | 15 | **0** |
| Wrong-condition citations | 65 | **0** |
| Duplicate citations | — | **0** |
| Invisible citation badges | 28 | **0** |

### Defects found and fixed
- **UX-85** — 17 citations mistyped vs NCBI publication type; 15 RCTs labelled `Clin`, including the
  Devinsky Dravet and Lennox-Gastaut trials.
- **UX-86** — `.cite-type-Clin` / `.cite-type-Reg` had **no CSS rule**: 28 badges rendered
  white-on-cream at **1.14:1** against a 4.5:1 AA requirement.
- **UX-87** — `findRelevantCites` ignored each citation's `indications` tag; **65 pairs displayed a
  citation tagged for a different condition** (a Lung Cancer trial under Melanoma).
- **THREE RETRACTED PAPERS** found by screening and removed/excluded: Khalil 2022 (Orientin/lung,
  retracted for image manipulation), Song 2021 (quercetin/TBI), Siracusa 2016 (luteolin/SCI).
  A fourth — Lesné 2006 *Nature*, the Aβ*56 paper — was checked and is **not** cited by V2.

> **STANDING RULE: screen every new PMID for `Retracted Publication` / `Expression of Concern`
> before citing it.** Three hits in one session is not a fluke.

---

## 3. TRAPS AND LESSONS — read before editing citations

1. **`ADVERSE_FINDINGS.condition` must equal the condition's `label` EXACTLY.** The matcher is
   `f.condition === cond.label || mg(f.group)`. A near-miss silently fails to render the caution
   tile. Always verify the tile class in-browser after adding one.
2. **Tag, don't duplicate.** Four times this session a paper was already in V2 and a copy got
   appended. Check first; add the indication to the existing entry.
3. **Tagging a citation can strand the indications it was silently covering.** Once a citation has
   an explicit tag, UX-87 logic stops serving it elsewhere by keyword fallback. Tagging Oláh 2016
   for Anti-Inflammatory opened a new hole at CBGV + Dry Skin.
4. **Measure on the molecule–indication pair, not the condition label.** Citations are tagged to
   indication strings (`IBS`), not condition labels (`Irritable Bowel Syndrome (IBS)`). Matching on
   labels once produced a false "42 of 59 conditions uncited".
5. **Parse citation arrays in the browser via `CITATIONS[mol]`, not with regex over source.** A
   strict regex missed three entries and produced a wrong claim about Orientin.
6. **Restrict molecule searches to `[ti]` when testing whether literature exists.** A non-title
   search wrongly concluded linalool and CBG had no Alzheimer's literature; both do.
7. **Watch for term collisions.** Two "cannabis + acute chest syndrome" hits were cardiology papers
   using ACS to mean *acute coronary syndrome*, with no sickle cell involvement.
8. **V2 stores real UTF-8 accents** (`Guzmán`, `Melén`) — do not write HTML entities in author
   fields. One pre-existing `M&uuml;ller-Vahl` remains; renders fine, left alone.

---

## 4. DESIGN DECISIONS MADE THIS SESSION

- **`evidenceNegative` is now live.** TBI is the second such condition after Essential Tremor and
  the first to actually exercise the branch in the guided recommendation screen.
- **Weights express strength-of-evidence for an effect, not endorsement.** THC9 sits at 9 for
  HIV/AIDS *and* carries a red caution tile from Charron 2019's "avoid the use in PLWH".
- **A caution paper does not raise a weight.** CBG for Endometriosis is 4, not 5, because its only
  paper (Alves 2025) warns about endocrine disruption rather than showing benefit.
- **Synthetics are labelled as such.** Dexanabinol, nabilone and KN38-7271 support the receptor
  mechanism, not any molecule on the table, and their notes say so.
- **Protocols carry no evidentiary weight.** The CRISP dronabinol protocol is cited for Sickle Cell
  with that stated explicitly.
- **A failed-to-recruit trial is cited as such** (Chesterman 2025, endometriosis) so readers know
  the base is observational because the trial could not be run — not because it failed.
- **Professional-society non-recommendations are first-class content.** NAMS 2023 places
  cannabinoids in "Not recommended" for vasomotor symptoms; V2 states it at grade A beside the
  survey showing many women use cannabis anyway.
- **Condition aliases are navigation only** — no condition, molecule, weight or citation changes.

---

## 5. OUTSTANDING WORK

### Highest value
1. **~151 pairs remain uncurated** (57% curated) and display a keyword-matched fallback rather than
   a curated citation. Overwhelmingly terpene/flavonoid generic indications: **Anti-Inflammatory
   (31 molecules), Analgesic (22), Neuroprotection, Antioxidant, Antibacterial**. Abundant
   literature; several sessions of work.
2. **Browse/Search All Health Conditions screen in Guided Match** — approved in principle, never
   built. Implementation was sketched; COND_ALIASES (UX-92) is the natural companion.
3. **`Muscle Spasm / Spasticity` sits at THC9=9.** If Joseph 2021 is right that the pooled
   antispastic effect is weak and non-etiology-specific, that 9 may be too high. Raised, never
   actioned.

### Deferred
- Audit item 6 — 24 abbreviated citation titles; audit items 9–12.
- An **S-tier above A** for Cochrane-level / multi-RCT consensus. This session repeatedly hit the
  ceiling of a single A grade covering both one positive RCT and a Cochrane review.
- A dermatology group, pending a second skin condition.

### Security — owner has deferred, by explicit decision
**DEMO-06.** ⚠ **Superseded 2026-08-29 — there are TWO credentials, not one.** The demo pair sits in
the comment above **`var AUTH_HASH`** (164 of 265 commits, since `842616a`); the staff-feedback-console
pair sits at **`var ADMIN_USER`** with its password in the adjacent `//` comment (141 of 265 commits,
since `f3dad41`). Both are **currently served on the public site**. The original wording — "one
credential… at index.html:11028, 97 of 221 commits since 2026-08-05" — was wrong on the count, the
location and the history. See §0aa item 1 for the current statement. No Supabase key is exposed
(`SUPABASE_URL` / `SUPABASE_ANON` are empty strings) and both sync flags are `false`, so feedback is
per-device `localStorage` only — the blast radius is small.

The owner reviewed this on 2026-08-25 and **decided no action is needed during development**;
Acannability's IT team will handle credential management and GitHub security at production. Noted
and respected. Two things for whoever picks this up:
- The claim that V2 "is not yet publicly deployed" is **not accurate** — the Pages site is public
  and serves the credential today.
- **Hard precondition:** before `SYNC_ENABLED` or `RD_SYNC_ENABLED` is ever set `true`, the gate
  must move server-side. Client-side auth cannot be secured — `cyrb53` is a 53-bit non-cryptographic
  hash and the check ships to the browser.

---

## 6. THE MECHOULAM DATABASE — reviewed, do not ingest

https://mechoulam.de/en — 1,169 studies, 52 indications, GRADE-based S/A/B/C/D, DOI dedup.

**Their terms prohibit exactly what would be tempting:** use is licensed for *"private,
nicht-kommerzielle Zwecke"*; *"Kommerzielle Weiterverbreitung der generierten Zusammenfassungen"*
and *"Automatisiertes Auslesen (Scraping)"* are both forbidden. V2 is a commercial product. As a
German site they also hold **EU sui generis database rights** over the compilation.

**Usable:** PMIDs/DOIs as facts, re-sourced from PubMed; GRADE methodology (public); the *idea* of
an S-tier; linking with attribution. **Not usable:** their summaries, ratings, curated lists, or any
automated extraction. Their content is also AI-generated with an explicit no-warranty disclaimer.

**Practical use:** a human-browsed pointer. Owner declined to seek written permission, so treat the
site as read-only inspiration and source everything independently from NCBI.

---

## 0d. SESSION OF 2026-08-24, PART 4 — IDPH SET COMPLETE

`7cfef2a` → **`5749dbd`**, deployed and verified byte-identical (sha256 `ad44465824f7e22d`).

### UX-91 — Sickle Cell Disease (IDPH **7 of 7 — the last one**)

`{id:'sickle',label:'Sickle Cell Disease',group:'pain',hasRisk:true,molecules:{THC9:5,CBD:5}}`

Two molecules — the narrowest entry in V2. Grouped under `pain`: SCD is a haemoglobinopathy, but
every cannabis-relevant aspect in the literature is vaso-occlusive and chronic pain.

**Deliberately NOT `evidenceNegative`.** Abrams 2020 (n=23) missed its primary endpoint, but every
daily pain difference favoured cannabis (−5.3 to −16.5) without significance — an *underpowered*
pilot, not a demonstration of no effect. Contrast TBI, where 846 patients gave 50% vs 51%. Getting
this distinction right matters more than the label.

The human evidence points **both ways** and V2 shows both: Curtis 2020 (*Blood Adv*) found lower
admission rates among patients who obtained medical marijuana; Paulsingh 2022 concluded marijuana
*"either worsened their painful crises or offered little to no help"*.

### The acute chest syndrome caution — how it was sourced

The owner asked for this caution explicitly. PubMed had no abstract, PMC returned metadata only,
and Europe PMC was down (503/504 on five retries). Retrieved the full text from the publisher
(ashpublications.org) via the in-app browser.

**Verified: Cohen 2010 is TOBACCO-ONLY.** n=106; active smoking **RR 2.61** for ACS (95% CI
1.24–5.51), passive second-hand smoke **RR 2.62** (1.05–6.57), pain RR 1.94. The word "marijuana"
appears **nowhere** in the paper (only in a sidebar of related links).

So the adverse entry states plainly that the study measured tobacco smoke, **not** cannabis, and
that **no published study has examined smoked or vaporised cannabis and ACS in SCD** — framing it
as an *unstudied* risk in a population where studied smoke exposure more than doubles a
potentially fatal complication.

> **Trap for next time:** two PubMed hits that looked like cannabis/ACS papers were **false
> positives using the cardiology sense of "acute chest syndrome"** (acute coronary syndrome), with
> no sickle cell involvement. Always confirm which sense of ACS a paper means.

Also: quercetin looked supportable at 11 SCD hits but the lead paper isolates **quercitrin**, a
different compound — excluded.

---

## IDPH PROGRAMME COMPLETE — the seven conditions and what they showed

| # | Condition | UX | Verdict |
|---|---|---|---|
| 1 | Migraine / Headache | UX-80 | Positive, THC9=9 |
| 2 | Tourette Syndrome | UX-81 | Mixed; flagship trial failed, THC9=8 |
| 3 | Irritable Bowel Syndrome | UX-83 | 5 RCTs, none positive, THC9=5 |
| 4 | HIV/AIDS | UX-84 | Strongest — FDA indication + 5 RCTs, THC9=9 |
| 5 | Spinal Cord Injury | UX-88 | Weak, non-etiology-specific, THC9=6 |
| 6 | **TBI / Post-Concussion** | UX-90 | **evidenceNegative** — every trial failed |
| 7 | Sickle Cell Disease | UX-91 | Mixed and underpowered, THC9=5 |

Two of seven tell a patient the research does not support what they came in hoping for. That is
the point of the exercise, and the owner has affirmed it twice.

### What's next
- **Gate 5**: consolidation labels; the Alzheimer's "agitation vs neuroprotection" framing question.
- **Browse/Search All Health Conditions** screen in Guided Match (approved in principle).
- Open: `Muscle Spasm / Spasticity` at THC9=9 may be high if Joseph 2021 is right that the pooled
  antispastic effect is weak and non-etiology-specific. Raised, not changed.
- Deferred: audit item 6 (24 abbreviated citation titles); items 9–12.
- **DEMO-06 remains the one real security item**: two client-side credential gates must move
  server-side; passwords are in plaintext comments and in public git history — treat as compromised.

---

## 0c. SESSION OF 2026-08-24, PART 3

`4ff6904` → **`7cfef2a`**, deployed and verified byte-identical (sha256 `42bc02c359d9f445`).

### UX-90 — Traumatic Brain Injury / Post-Concussion (IDPH **6 of 7**)

`{id:'tbi',group:'neuro',hasRisk:true,**evidenceNegative:true**,molecules:{THC9:4,CBD:4,LTL:4,QUC:4}}`

**Only the second `evidenceNegative` condition in V2 after Essential Tremor, and the first to
actually exercise that branch in the guided recommendation screen** — until now it was insurance
that never fired. The owner approved the flag explicitly, to keep V2 honest as an unbiased resource.

Every interventional trial has failed:
- **Maas 2006** (*Lancet Neurol*), dexanabinol phase III, **n=846** — the largest cannabinoid TBI
  trial ever run. 50% vs 51% unfavourable outcome, OR 1.04. Dexanabinol is a **synthetic**.
- **Fairhurst 2020** (*Dev Med Child Neurol*), nabiximols — the **only** trial of a plant-derived
  cannabinoid medicine in TBI. "No significant reduction in spasticity versus placebo", plus
  neuropsychiatric adverse events.
- Both positive studies (Knoller 2002, Firsching 2012) are phase II **synthetics**.

The neuroprotection signal does not replicate. **Nguyen 2014**'s famous 2.4% vs 11.5% mortality
rests on **two deaths**, CI reaching **0.991**. **Ali 2022** propensity-matched 1,377 pairs from
13,266 patients → **no difference** in mortality or any thromboembolic outcome. **Szaflarski 2024**,
n=3,729 → no mortality difference.

### Two bugs caught before deploy — both worth remembering

1. **`ADVERSE_FINDINGS.condition` must equal the condition's `label` EXACTLY.** The matcher is
   `f.condition === cond.label || mg(f.group)`. I wrote `'Traumatic Brain Injury'` against a label
   of `'Traumatic Brain Injury / Post-Concussion'`, so the caution tile silently did not render.
   Always verify the tile class in-browser after adding an adverse finding.
   (The four adverse findings whose condition matches no label — Erectile Dysfunction, Male
   Fertility, Testicular Cancer, CBD "General / Multiple" — are **fine**: the first three carry
   `group:'mens'` and the last is a molecule-wide warning.)
2. **A second retracted paper**: Song 2021 (quercetin/TBI, PMID 33612499) — excluded. Du 2018 also
   carries a **corrigendum** for a duplicated image panel; it stands, and the note says so.
   **Running total: 2 retracted papers found by checking, 0 now cited.** Screen every new PMID for
   `Retracted Publication` / `Expression of Concern` before citing it.

### Remaining: IDPH condition **7 of 7 — Sickle Cell Disease**
Then Gate 5 (consolidation labels, the Alzheimer's framing question) and the Browse/Search All
Health Conditions screen in Guided Match.

---

## 0b. SESSION OF 2026-08-24, PART 2

`159010a` → **`4ff6904`**, deployed and verified byte-identical (sha256 `98a41fefa3808a76`).

### UX-88 — Spinal Cord Injury (IDPH condition **5 of 7**)

`{id:'sci',label:'Spinal Cord Injury',group:'neuro',molecules:{THC9:6,CBD:5,QUC:4}}`

**THC9 = 6 is deliberately below `ms` (9) and `spasm` (9), and that gap is the point.** A person
with SCI spasticity will assume the MS evidence carries over. Joseph 2021 (*Biomolecules*, Hill
criteria across 27 trials) found the antispastic effect **weak and non-significant in most
studies**, with **no dose dependency**, decaying over 3–4 months, and **not specific to the cause**
of the spasticity — concluding the data *"do not support a specific spasmolytic effect"* and that
benzodiazepine-like general CNS depression is likelier.

Only six RCTs exist, all small: Hagenbach 2007 (randomised **6 vs 7**), Wilsey 2016 (best
SCI-specific data, n=42, but an **8-hour lab experiment**), Pooyania 2010 (**nabilone — synthetic,
not a plant molecule**), Rintala 2010 (**NEGATIVE**), Wade 2003 (mixed population), Maurer 1990
(**n=1**). Mehta 2016 still puts anticonvulsants first.

**Luteolin excluded** — its two leading SCI papers are a retracted study (Siracusa 2016) and the
notice retracting it.

### UX-89 — a retracted citation was live in V2, and Orientin was ungraded

Swept all 523 PMIDs for retraction flags. **One hit: 35215267**, retracted 2026-07-08 for
*"inappropriate editing and duplication within Figure 6"*, the authors unable to supply original
material, the board having *"lost confidence in the reliability of the findings"*. V2 displayed it
**without** PubMed's `RETRACTED:` prefix.

It was Orientin's only **Lung Cancer** citation and there is no valid replacement, so the citation,
the indication, and ORT's weight of 6 in `lung-cancer` were all removed.

Auditing the rest: **ORT carried a flat 6 across eight conditions.** Anti-Inflammatory (113 hits),
Antioxidant (224), Diabetes (49) and Neuroprotection (39) justify it — unchanged. The three
remaining cancers each rest on two in-vitro orientin-specific papers and were re-graded to **5**,
with the four missing citations added.

> **Process note for the next session:** an interim claim that Breast/Colorectal/Liver had *no*
> citation at all was **wrong** — a strict single-line regex missed three pre-existing entries.
> When auditing citation arrays, parse them in the browser via `CITATIONS[mol]`, not with a regex
> over the source. A duplicate introduced during that fix was caught and removed pre-deploy.

### Remaining IDPH conditions (6–7)
Traumatic Brain Injury / Post-Concussion · Sickle Cell Disease. Rhythm unchanged:
**Gate 2 pack → approval → build → Gate 3 review.**

### Worth revisiting
`Muscle Spasm / Spasticity` sits at THC9=9. If Joseph 2021 is right that the pooled antispastic
effect is weak and non-etiology-specific, that 9 may be high. Raised with the owner, not changed.

---

## 0a. SESSION OF 2026-08-24, PART 1

Baseline `688d34a` → **`159010a`**, deployed and verified byte-identical
(local == GitHub == live, sha256 `ebb2d0bc422c4ca3`).

### UX-84 — HIV/AIDS added (IDPH qualifying condition **4 of 7**)

Placed in the existing `cancer` group, which is already labelled "Cancer & Immune", so no new
group was needed. **Five molecules only** — `{THC9:9, CBD:6, CBDV:4, bCA:4, QUC:4}`:

- **THC9 = 9** rests on the same dronabinol FDA approval that justifies `nausea:9` and
  `appetite:9`, plus five positive RCTs (Abrams 2007, Ellis 2009, Beal 1995, Struwe 1993,
  Haney 2007) and Bredt 2002 for short-term immune safety.
- **CBD = 6** on Ellison 2026 (CBD suppressed SIV replication in macaques comparably to
  first-line ART; reproduced in human macrophages, T cells, microglia). **Preclinical only.**
- **CBDV = 4**, the Essential Tremor floor for *studied but not supported* — Eibach 2021 was
  negative (pain 0.62 points **higher** than placebo).
- **bCA = 4** (Aly 2020, antiretroviral-**induced** neuropathy) and **QUC = 4** (Fesen 1993
  PNAS, cell-free integrase, never translated).
- Every other terpene and flavonoid was tested against PubMed and **excluded** — the hits are
  docking studies and plant-extract screens. Same discipline as Tourette.

`ADVERSE_FINDINGS` carries Charron 2019's *"recommendations should be to avoid the use in
PLWH"* for THC9 and the negative Eibach trial for CBDV, so both tiles render red. The caution
sits **beside** the efficacy rather than being netted out of the weight. Not in the Guided
Match funnel, by design (a diagnosis does not answer "why are you here today?").

### Three pre-existing defects found while verifying it

- **UX-85** — 17 citations mistyped vs NCBI publication types; 15 RCTs were labelled `Clin`,
  including the Devinsky Dravet and Lennox-Gastaut trials. **Visible RCT badges 28 → 43.**
  `38834872` deliberately left as `Obs`: NCBI tags Mendelian randomization as Meta-Analysis,
  but it is an observational genetic-instrument design.
- **UX-86** — `.cite-type-Clin` / `.cite-type-Reg` had **no CSS rule**, so 28 badges rendered
  white-on-cream at **1.14:1** against a 4.5:1 AA requirement. Both coloured; 0 of 140 badges
  now transparent. (WCAG 1.4.3 / EN 301 549.)
- **UX-87** — `findRelevantCites` ignored each citation's explicit `indications` tag and ranked
  on keyword overlap capped at 2. Across 346 pairs, **65 showed a citation tagged for a
  different condition** (a Lung Cancer trial under Melanoma, IBS trials under Endometriosis)
  and 60 tagged citations were hidden. HIV exposed it: `"HIV"` is 3 characters and was dropped
  by the `length > 3` filter, so both landmark HIV RCTs scored zero and lost their slots to an
  IBS paper matching the word "human". Explicit tags now win outright; keyword scoring is a
  fallback that can no longer promote another condition's citation. **Wrong-condition
  citations 65 → 0.** This changes citation display across *all* conditions — worth a look at
  Gate 3.

### Remaining IDPH conditions (5–7)
Spinal Cord Injury · Traumatic Brain Injury / Post-Concussion · Sickle Cell Disease.
Rhythm is unchanged: **Gate 2 evidence pack → owner approval → build → Gate 3 live review**
before the next condition starts.

### Still open
Browse/Search All Health Conditions screen in Guided Match (approved in principle, scheduled
after the IDPH set) · Gate 5 consolidation labels and the Alzheimer's "agitation vs
neuroprotection" framing · audit item 6 (24 abbreviated citation titles) · items 9–12 ·
**DEMO-06: two client-side credential gates must move server-side — passwords are in plaintext
comments and in public git history, so treat them as compromised.**

---

## 0b. SESSION OF 2026-08-19 → 08-23

Ten commits, `c19f8e6` → `27be506`. All deployed and verified byte-identical live.
The build was audited end-to-end on 2026-08-22 in preparation for presentation to
**European authorities, strategic partners and prospective pilot sites**.

### What shipped

| Commit | Item | Summary |
|---|---|---|
| `c19f8e6` | UX-70 | Greek prefixes → symbols (Δ9-THC, β-Caryophyllene). Citation titles, search keys and drug/receptor classes deliberately excluded. |
| `b9e56d0` | UX-71 | Pinch-to-zoom was disabled across 85% of the phone screen (`touch-action:pan-y` withholds pinch). Fixed to `pan-y pinch-zoom`. |
| `3a192a9` | UX-72 | **Fibromyalgia** added and integrated across 15 surfaces, with 10 NCBI-verified citations. |
| `7471896` | UX-73 | Adverse Findings re-described as "safety **or efficacy** findings" (it holds negative-efficacy trials). |
| `20c379e` | UX-74 | Receptor tallies in the FAQ were hand-typed and two were **wrong** (CB1 16→17, CB2 23→22). Now derived. |
| `c90c1aa` | UX-75 | Adverse Findings filter removed from the FAQ's health-condition enumeration (kept in the UI). |
| `31bfbff` | UX-76 | PubMed indication searches used `[ti]`, so **22% returned nothing**. Switched to `[tiab]`. |
| `3822d48` | UX-77 | **No PubMed link opens an empty search.** 253 verified-empty searches suppressed with an explicit absence message. THCP 6→5 for fibromyalgia. |
| `64998c6` | AUDIT-01..03 | 17 citation titles restored to the published record; one wrong year; `lang="en"`. |
| `27be506` | AUDIT-04,05,07 | 64 tiles made keyboard-operable; "patient" retired from consumer copy; two missing PubMed terms. |

### The three findings that mattered most

1. **A citation subtitle that does not exist.** PMID 30152161 read "An Overview of the
   Entourage Effect"; the paper is subtitled "An Update on Current Evidence and Cannabis
   Science". Several other titles had dropped qualifiers that changed what the study
   claimed — "A Preliminary Study", "An In Silico to In Vitro Approach", "model in male
   rats", "the Ingredient of Xihuang Pills", "with poly lactic-co-glycolic acid
   formulation". All restored verbatim.
2. **The table was not keyboard-operable.** All 64 molecule tiles were bare `<div>`s with
   `tabIndex -1`, so a keyboard or screen-reader user could not open a single molecule
   panel — the app's primary control. WCAG 2.1.1 Level A. Now `role="button"`, focusable,
   labelled, Enter/Space.
3. **A third of PubMed links were dead.** `[ti]` demanded the compound in the paper title,
   which fails for rare cannabinoids. Combined with over-quoted entourage queries, 253
   searches returned nothing. Queries fixed first, then the genuinely-empty remainder
   suppressed behind an honest message.

### Verified clean as of 2026-08-22

- **All 483 PMIDs resolve at NCBI.** Zero dead, zero fabricated. Every one was fetched.
- 448 of 476 citation titles match NCBI character-for-character after normalising case
  and punctuation. The remaining 24 are abbreviations that shorten without misleading
  (**audit item 6, deferred by the owner** — see §0 "Deferred" below).
- Zero JavaScript runtime errors across all 64 panels, 52 conditions, every filter, both
  search paths, Guided Match, the drug checker and the entourage overlay.
- Zero referential-integrity errors; no invalid evidence grade or severity; no duplicate
  record IDs; every alias resolves (verified against 18 brand/generic probes).
- Counts consistent everywhere (64 / 51), all derived from the data.

### Deferred by owner decision (NOT bugs)

- **Audit item 6** — 24 abbreviated citation titles. Shortened but not misleading.
- **Audit items 9–12** — flat heading structure (h1=1, h2=1, h3=0); "Guided Finder" in 3
  code comments (on-screen label is correctly "Guided Match"); CBL is grade D yet weighted
  7 in Cancer (only such outlier); three thin conditions (Autism 2 molecules, Essential
  Tremor 2, Psychosis 3).
- **Audit item 8 — reviewed and deliberately unchanged.** THC10, CB, THCVA, CBCVA and CFC
  carry no indications *because the literature does not support any*. Each description says
  so; THCVA's ends "No indications are listed pending direct experimental validation."
  Adding indications would invent associations. This is a strength — do not "fix" it.

### Two things where I corrected my own audit

Stated plainly so the next session does not re-chase them:

- I reported "23 controls without an accessible name". **20 were false positives** — the
  acknowledgement checkboxes are properly `<label>`-wrapped. The real defect was the tiles.
- I reported "10 orphan drug aliases" and "3 missing element IDs". **Both were my
  extraction being incomplete**, not defects. The medication lookup and the dynamic
  elements work correctly.

### Shipped 2026-08-23 (committed; deploy blocked — see blocker above)

| Commit | Item | Summary |
|---|---|---|
| `2f6aa6a` | UX-78 | Demo banner on mobile: 359x83px (96% x 10% of viewport) -> 273x36px (73% x 4%). Cause was the mobile override setting `right:8px; max-width:none`, stretching the desktop 290px pill edge to edge. **The Exit button was never the problem** — it is 46x24px; the two wrapped text lines took 65 of the 83px. Phone now shows one line, "DEMO — NOT A LICENSED STORE", as real text (not a CSS pseudo-element) with the full statement still in the container's aria-label. Menu dropped on mobile; the demo hub already has "Back to demo menu". Desktop untouched. |
| `2f6aa6a` | UX-79 | Guided Match: **11 taps -> 6**. Four screens were measured, one variable at a time, to change NOTHING in the standard flow — "How much experience", "When do you expect to use it", "How do you want it to work", "Any preferred way to take it" each produced a byte-identical recommendation whichever option was chosen. They feed PRODUCT matching, which only runs in Demo Mode, so they stay in `FUNNEL_DEMO`. Plus: purpose statement on screen 1, "Step X of Y" on the existing dots, and an express path (3 taps) that skips only optional screens and states plainly that medications were not checked. |

**Correction worth carrying forward:** the audit originally said *five* preference questions were
inert. Closer testing found **four**. `thc` adds the non-intoxicating caveat and `second` adds the
"you also mentioned" cross-link — both verified to change output, both kept. Removing them would
have deleted working behaviour.

Standard funnel is now `intent, refine, thc, second, safety, meds, rec`; the demo funnel is
unchanged at 11 steps. Both step counters read correctly for their own path (7 and 11).

### NEXT UP (owner's stated priority)

**Review Illinois Medical Cannabis qualifying conditions** to decide which additional
health conditions belong in V2. Fibromyalgia surfaced this way. Not every qualifying
condition needs including. Note while doing this: Autism (2 molecules), Essential Tremor
(2) and Psychosis (3) are already thin, so the review may also indicate which *existing*
conditions need deepening rather than which new ones to add.

Two improvements I recommended beyond the audit list, both still open:
- **A "How V2 is built" panel** — curation method, A–D grading criteria, RPh sign-off.
  European reviewers will ask; it is currently implicit.
- **A visible content version/date stamp** and stated review cadence, which turns "a
  website" into "a maintained reference".

### Critical process rule established this session

**Whenever a molecule, indication, condition or molecule-condition pairing is added, the
PubMed probe MUST be re-run and `PUBMED_EMPTY` refreshed.** `preflight.py`'s
`check_pubmed_links` holds a coverage manifest and will FAIL the build when content
changes, because the new searches are unverified and may be dead on arrival. Do not
bypass it — regenerate the data.


> Read this + `CLAUDE.md` before continuing. Always verify against `index.html` before changing anything — this doc is point-in-time.

---

## 1. Project status & objectives
V2 is Acannability's single-file, evidence-based **cannabis research/education platform** (NOT medical advice). It's an interactive "periodic table" of cannabis-derived molecules with condition/receptor/evidence filtering, a Guided Match finder, a drug-interaction checker, an Entourage Effect explorer, per-molecule detail panels, printing, a staff-only Demo Mode, and kiosk features (entry-gate disclaimer, New Session, inactivity reset).

**Status:** Stable, audited (2026-08-22) and presentation-ready. The owner (Joseph Friedman, RPh, MBA — Acannability advisor, medical cannabis pharmacist) reviewed the latest build and it's working well. Acannability partners were slated to review ~week of 2026-08-10.

**Objective for next phase:** continue polishing UX/clinical content; when Supabase credentials arrive, wire the (already-scaffolded, flag-off) feedback + anonymous-research backends live; POS/inventory (INV-05/06) awaits a real dispensary POS.

## 2. Baseline / deployment
- **Baseline = commit `27be506`** (2026-08-22) — HEAD == origin/main; live GitHub Pages **byte-identical** (`sha256 acacd667d190efe9…`). _Superseded `a87f673` / `52c0c9ce…` of 2026-08-13._
- **Live:** https://josephrph.github.io/periodic-table/  · **Repo:** https://github.com/josephrph/periodic-table (public, `main` → Pages).
- Only `index.html` + `.gitignore` are tracked. Backups (`index_BACKUP_*`), `Project_Backlog.xlsx`, `HANDOFF.md`, and the drug-reference `.md` are local/untracked. GitHub token is in the macOS keychain (never printed).
- **Deploy caveat learned 2026-08-06:** GitHub Actions/Pages had a major outage; deploys can also silently fail to trigger. When a push looks "stuck," check the **deployment status** (`curl -s https://api.github.com/repos/josephrph/periodic-table/deployments` → latest id → `/statuses`; `gh` is NOT installed) BEFORE blaming the browser, and re-trigger with an empty commit if a deploy shows `failure`. GitHub Pages/Fastly **ignores query strings for its CDN cache key** (so `?v=` busts the browser cache, not the edge — confirm via `last-modified`/`age`/`x-cache` headers). `jsc` lives at `/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc` (not on PATH).

## 3. Architecture (single file: `index.html`, **~11.0k lines / 1.13 MB** as of 2026-08-22, HTML+CSS+JS+data inline)
- **The Periodic Table grid** — **64 tiles (55 molecules + 9 precursor acids)** · **51 health conditions** (was 50 before Fibromyalgia, UX-72) — was 65 until the duplicate cannabicitran merge (`a95c86d`), condition/receptor/evidence filters, molecule detail panels with tabs (findings, Adverse Findings, References→PubMed), Associated Molecules panel, `recForConditions()`/`match()` engine.
- **Guided Match** (`window.V4GX` UI + `window.V4Guided` engine) — on-screen name is **"Guided Match"** (header `#guidedBtn`, mobile `#mtbGuided`). Funnel: `persona → intent("Why are you here today?") → refine → experience → thc → timeofday → onset → route → second → safety → meds → rec`. The recommendation screen ("What the research associates") shows the SAME molecules the table highlights (Primary ≥8 / Supporting <8), an **optional collapsed Entourage Effect** expander (deep-dive opens the full Entourage overlay scoped to the condition), a medication-check summary, Print, and the FEED-05 research opt-in. **Standard Guided is product-free — retail products belong ONLY to Demo Mode.** `recForConditions()` is deliberately untouched by the guided UI.
- **Drug Interaction Checker** (`💊`, header) — two modes: Single Medication and My Medication List. **`DI_DATA`** (~211 entries) = drug→cannabis interactions; **`DDI_DATA`** (~49) = drug↔drug. Search resolves by drug name, id, brand, class, or alias (`DI_ALIASES`). Severity 🔴/🟠/🟡, evidence A–D, mechanism/effect/monitoring, PubMed when a real PMID exists. **The cannabis-interaction "universe" is only 3 molecules that carry data: CBD, CBN, THC9.** When a recommendation/condition is active, molecules that interact but weren't recommended are grouped under **"Other cannabis molecules that also interact"** with an explicit, molecule-named note (full-spectrum rationale) — applied consistently in BOTH the Guided report (print + screen) and the standalone checker (list mode).
- **Refine Inventory Match** (`⚙`) — manual exclusion of molecule classes/individual molecules (`window._excludedMols`, shared across Associated Molecules + Entourage panels). Today = manual (or CSV import via the Budtender console, INV-02). Live POS auto-sync / real-time in-stock ranking (INV-05) and live COA (INV-06) are **simulated in Demo Mode**, pending a real dispensary POS.
- **Printing / Save-as-PDF** (`PT_doPrint`/`PT_shell`, native `window.print()`, no libs, `@media print` isolates `#printRoot`) — FOUR surfaces: Guided Match recommendation ("🖨 Print this summary"), Drug Interaction results ("🖨 Print"), molecule detail panel ("🖨 Print this molecule profile"), and the Associated Molecules panel ("🖨 Print this match"). The Guided print includes the FULL drug-interaction detail (mechanism/effect/monitoring/PMID) grouped recommended vs. other. Medication names render **"Generic (Brand)"** via `medLabel()` (e.g., "Apixaban (Eliquis)"). Printing now counts as activity (resets the idle timer).
- **Demo Mode** (`window.V4DEMO`) — staff-only, gated presentation mode (login; `ALLOW_DEMO=false` on licensed builds). Fictional "Acannability Cannabis Dispensary Demo," sample inventory, simulated POS/COA, booth insights dashboards. All storage namespaced `acann_demo_*`. Never shows to patients; products/COA/insights are Demo-only.
- **Kiosk session** — THREE-screen entry sequence (welcome w/ 4 copied acknowledgements → full disclaimer w/ all 6 → optional research opt-in; `showGateScreen()`/`enterApp()`), "New Session" + "I'm Done" resets, and an **inactivity timer** (2.5 min idle → warning → 30s countdown → reset at 3:00, clears meds/answers for privacy). The warning is now a **discreet, non-blocking, always-on-top pill (z-index 10001)** visible on every screen; "Continue session" preserves the full session.
- **Mobile** — `is-mobile-view`, `__v3MobileNav`, mobile tab bar (`#mtb*`: Guided/Molecules/Conditions/Interactions/Entourage/Info), `initV3TouchReveal`/`initV3SwipeDismiss`/`initV3SwipeBetweenMolecules`. Must be preserved on every change.

## 4. Scientific content status
- **64 tiles:** 55 molecules (phytocannabinoids, terpenes, flavonoids, rare & novel) + 9 precursor acids. Symbols, atomic masses, receptor targets (CB1/CB2/TRP/PPAR/5HT), evidence grade A–D per tile.
- **50 health conditions** across eight groups (Neurological now 10, incl. Essential Tremor as an evidence-NEGATIVE entry), 11 cancer sub-types, Women's Health, Men's Health, Glaucoma, plus the ⚠ Adverse Findings filter. FAQ copy is in sync as of `ce9823d`.
- **Evidence grades:** A = clinical human trials, B = observational, C = preclinical/in-vitro, D = theoretical/emerging.
- **Medication DB:** ~211 `DI_DATA` (drug↔cannabis) + ~49 `DDI_DATA` (drug↔drug). Recent additions: **Rexulti (brexpiprazole)** — antipsychotic, CYP3A4/2D6, flags CBD+THC9, moderate/ev C, plus 4 label-based DDI pairs (CYP3A4 inhibitors/inducers, SSRIs, bupropion); **Skyrizi (risankizumab)** — IL-23 mAb, no CYP → minor/ev D, no DDI pairs (biologic). Cannabis-interaction data exists for **only CBD, CBN, THC9**.
- **Editorial rule: NO fabricated PMIDs.** Blank `pmid` → no PubMed line. Clinical safety copy is drafted conservatively and RPh-reviewed. **As of `4d35dc7` every PMID in `index.html` has been verified against NCBI to resolve to its cited paper** (drug DB + 393/399 science already correct + 5 fixed + 80 bylines corrected). Re-verify any *new* citation the same way before adding it. Note: the entourage-foundation render now guards blank PMIDs (McPartland 2001 is a real but non-PubMed-indexed paper, shown without a link).
- **Adverse Findings** category surfaces peer-reviewed safety signals; "absence of a flag ≠ safe."

## 5. Feedback & research (backends scaffolded, flag-OFF)
- **FEED-01 (shipped):** anonymous in-app feedback/suggestion capture (`window.Feedback`, `localStorage acann_feedback_v1`), soft-gated staff console + CSV/JSON export. Per-kiosk, no backend.
- **FEED-02 (flag-off):** client sync scaffold (`SYNC_ENABLED=false`) to push feedback to Supabase across kiosks — pure no-op until Supabase URL+anon key wired.
- **FEED-05 (flag-off):** anonymous research opt-in (`window.ResearchData`, `RD_SYNC_ENABLED=false`) — age group / sex / city-state only, no PII/health info, opt-in on welcome gate AND end of Guided Match, withdrawable via footer "Research data & privacy." Stores locally; zero network until wired.
- **To go live:** owner provisions Supabase (URL + anon key + reviewer emails) → create tables/RLS, set the flags true, narrow CSP, privacy/legal review.

## 6. This session's work (2026-08-12, baseline 79b3e84 → 7c5f933)
- `7c5f933` **CITATIONS duplicate-entry cleanup** (backlog `UX-37`, Done). Ten papers were listed twice for the same molecule — once in the base `CITATIONS` block and again in `CANCER_CITATIONS`, which the init step (~L6919) concatenates onto `CITATIONS` — so both copies rendered in the panel's Scientific References accordion and in `printMoleculeReport`. Removed the base copy of each pair, kept the `CANCER_CITATIONS` copy (it also carries the cancer sub-type `indications` tag). Pairs: CBG `33562819`, MY `37764505`, bCA `30166097`, LI `34362338`, aHU `31259712`, TE `24084350`, TE `35704944`, GE `21371438`, FA `41739313`, aBI `36854520`. **Citation integrity:** all 10 PMIDs re-verified via NCBI eutils `esummary` — every pair is the *same* paper (identical first author/year/title/journal/volume-pages), so none was two different papers sharing a mistyped PMID; no clinical decision or RPh sign-off was needed and nothing was reassigned. Fuller bibliographic detail from the removed copy was carried onto the retained entry (both NCBI-confirmed): CBG vol `10(2)`→`10(2):340`, TE `24084350` journal `Arh Hig Rada Toksikol`→`Arhiv za higijenu rada i toksikologiju`. 444 → 434 entries; no claims, molecules, or rendering code touched. Note: the citation-level `indications` field is **metadata only** — no code reads it (`findRelevantCites` scores by title/journal/note text). Verified: post-init duplicate scan across all 65 keys = 0; multiset of (molecule, PMID, title) differs from `4dfffea` by exactly the 10 removals; `jsc` clean on both script blocks; panel + print render each paper once (desktop and `is-mobile-view`); guided flow end-to-end; 0 console errors; live byte-identical.
- `4dfffea` **UX-06 Print/PDF Phase 2 — molecule profile + Associated Molecules match** (backlog `UX-06` → Done, new row `UX-36`). Two new printouts on the existing `PT_shell` letterhead:
  - **`printMoleculeReport(id)`** — "🖨 Print this molecule profile" button rendered in `#panelPdf` (alongside the reference-article button, which is unchanged). Sections: Profile (name/category/MW/evidence grade/receptors), Researched Areas, Background, ⚠ Adverse Findings (full findings + footnotes, or the standing "absence is not safety" caution), 💊 Drug Interactions, Notable Synergies, Scientific References.
  - **`printRecoReport()`** — "🖨 Print this match" button (`.fp-reco-print`, secondary weight so ⚙ Refine Inventory Match stays the primary action) in the Associated Molecules header row. Sections: Matched Molecules grouped by category with evidence grades, ⚠ Drug Interaction Alert when a medication search is active, Safety Considerations (`#csWarn`'s rendered banner verbatim), Filters Applied (exclusions / evidence threshold / receptor filter).
  - **Why purpose-made bodies, not DOM clones** (unlike `printDrugReport`): the molecule panel's accordions are collapsed by default so a clone prints empty section bodies, and the match panel renders symbol-only colored chips that are cryptic on paper. `printRecoReport` recomputes from the SAME source the on-screen panel uses (highlighted tiles + `_excludedMols` + `_refineMinEvidence`), so the printout cannot drift from the panel.
  - **Design decision:** Δ9-THC carries ~130 documented interactions, so the molecule profile prints a **compact list grouped by severity** (keeping the actionable monitoring line) rather than ~40 pages of full cards; full per-drug mechanism/effect detail stays in the dedicated Drug Interaction Report, which is scoped to the medications the reader actually takes.
  - New helpers `PT_evLabel` / `PT_sec` / `PT_cite` and `#printRoot .pt-print-*` CSS. `PT_doPrint`, `printDrugReport`, and `V4GX.printRec` untouched. No content or clinical claims added. Verified: `jsc` parse clean on both script blocks, guided flow driven end-to-end (12 steps incl. meds + med-check summary + guided print), mobile molecule-list → panel → print path, 0 console errors, live byte-identical.
  - The originally-listed third Phase-2 target (product-recommendation printout) is **deliberately out of scope** — retail products belong only to Demo Mode and standard Guided is product-free.

### Prior session (baseline 7738046 → 982dce7, Content-Depth + polish + Men's Health)
- `982dce7` **Per-category Adverse Findings safety banner** (backlog `UX-34`, Done, owner-requested). New `buildConditionSafetyHTML(cond)` makes the per-condition warning banner data-driven: any selected condition surfaces its own Adverse Findings, matched by `finding.condition===cond.label` OR by category (`finding.group` vs `cond.group`, string-or-array). Tagged the 3 men's findings `group:'mens'` so ED/testosterone/fertility/testicular surface under prostatitis + prostate cancer; also lit up the previously-latent psychosis/cardiovascular/nausea(CHS)/liver cautions on their own conditions. Global ⚠ filter banner + Breast Cancer banner left untouched. No new claims.
- `2c14964`/`704f648` **Men's Health condition group** (backlog `DRUG-06`, `UX-33`, Done, RPh-approved). New `mens` group under Show Health Conditions: new condition **Chronic Prostatitis / Pelvic Pain** (grade C, CB2 anti-inflammatory, Piao 2025 `38449457`) + **Prostate Cancer cross-referenced** via new array-`group` support (condition.group may be an array; the only reader is the buildConditionsBar filter at ~L3304). Honest harm side added as `ADVERSE_FINDINGS` on THC9 (grade B, verified): ED/low-testosterone (`40121549`,`38834872`), reduced male fertility (`30916627`,`33251770`), testicular germ-cell tumor risk (`26560314`). FAQ updated to 49 conditions / 8 groups. **Evidence rationale:** most men's-health cannabis literature (ED/fertility/testosterone/testicular) is *harm*, not benefit — so benefits sit in the group, risks in Adverse Findings. Verified end-to-end (group renders w/ both conditions; prostate cancer still under Cancer Sub-types; THC9 adverse tab; 0 console errors).
- `ce8bd69` **UX polish — class-brand display + demo entity escaping** (backlog `UX-32`, Done). medLabel: `isClass` flag on benzos/opioids so classes don't show a misleading single brand ("Benzodiazepines (Xanax)"→"Benzodiazepines"); individual drugs keep Generic (Brand); brands unchanged (search intact). grp() (~L7960): dropped `esc(djMolName())` double-escaping so demo-journey chips render "Δ9-THC" not "&Delta;9-THC" (Demo Mode only). **All known §8 refinement items now resolved.**
- `f9de77c` **UX polish — corrected stale FAQ condition-group counts** (backlog `UX-31`, Done). Both FAQ copies now match the data: **48 health conditions across 7 groups** (Pain 6, Neuro 9, Mental 5, Cancer & Immune 5, Cancer Sub-types 11, Metabolic 8, Women's Health 4) + Adverse Findings filter (was a stale "40/six groups" with miscategorizations + omitted Women's Health/Alzheimer's/Parkinson's/IBD). Also reconciled CLAUDE.md rule #2 "Guided Finder"→"Guided Match" (local doc). Regression pass on `8f79709` (before this) drove the full app end-to-end — no regressions.
- `8f79709` **Slice 4 — safety-copy conservatism pass** (backlog `DRUG-05`, Done, RPh-approved). Softened 11 overstated phrases across 8 grade-C/D cannabis-interaction entries (statins, apixaban, clopidogrel, glipizide, bactrim, sildenafil, St. John's Wort, tamsulosin) so wording matches the mechanistic/theoretical evidence level. Deliberately left established THC pharmacology and the nitrate+PDE5 contraindication untouched. Editorial only, no data changes. **All 4 Content-Depth slices now shipped.**
- `4d751e4` **Slice 3 — new drug + DDI entries** (backlog `DRUG-04`, Done, RPh-approved). Added 6 DI entries (azole antifungals, amiodarone, colchicine, gemfibrozil/fenofibrate, carbamazepine, phenytoin) + 9 DDI pairs (clopidogrel+PPI, lithium+thiazide, lithium+ACE/ARB, SSRI+NSAID, warfarin/statin/digoxin+amiodarone, colchicine+CYP3A4-inhibitor, ACE/ARB+diuretic+NSAID triple-whammy), all NCBI-verified. De-duped azole aliases + trimmed redundant brands from the CYP3A4 umbrellas so each drug resolves to one card. DI_DATA now ~219 entries; DDI_DATA 62. Remaining: Slice 4 (safety-copy conservatism pass).
- `d882e27` **Slice 2 — expand curated synergy citations** (backlog `DRUG-03`, Done, RPh-approved). Added NCBI-verified `synergy` entries: THC9+CBD (MS spasticity, Novotná 2011 `21362108`); CBD +THC cancer pain (Johnson 2010 `19896326`) & +THC anxiety/paranoia buffering (Englund 2013 `23042808`); β-caryophyllene CB2 anti-inflammatory (Gertsch 2008 `18574142`, labeled preclinical). Renders in the Notable-Synergies accordion. Remaining Content-Depth slices: 3 (new drug/DDI entries), 4 (safety-copy conservatism pass).
- `4d35dc7` **Citation integrity — full NCBI-verified audit of all 410 PMIDs** (backlog `DRUG-02`, Done). Corrected 15 fabricated drug-DB PMIDs (10 `DI_DATA` cannabis-specific + 5 `DDI_DATA`), backfilled 27 blank DDI pairs, fixed 5 wrong science PMIDs (Guzmán 16804518, Chowjarean 34646091, Raz 37084981 [author Finlay→Raz], Spindle 38498958 ×3, McPartland blanked), 1 per-occurrence title (3859295), 80 author bylines, and added an entourage-foundation blank-PMID render guard. Verified: `jsc` clean, live byte-identical, 0 residual wrong PMIDs.

### Prior session (baseline dc23175 → 7738046, 12 commits, all Done)
1. `3ad720e` FAQ + How-to-Use refreshed to current build. `64a7f70` plainer "Associated Molecules panel" copy.
2. `2cc1220` **Optional Entourage Effect step** in the Guided Match (collapsed expander + deep-dive; product-free).
3. `e432e36`/`b692819` **Guided Match print now includes full drug-interaction detail** (was summary-only).
4. `ab6651b` + `9b21c3b` + `ba9012b` **Non-recommended molecules (CBN/CBD/THC9) grouped & explained** ("may be present in full-spectrum products"), consistently in Guided report + standalone checker; **medication names show "Generic (Brand)"** (`medLabel`).
5. `47a632b` **Added Rexulti + Skyrizi** (+ brexpiprazole DDIs), no fabricated PMIDs.
6. `5ababab` **De-duped the "Anything else?" step** so the primary symptom isn't re-offered.
7. `7738046` **Inactivity warning fix** — discreet always-on-top pill visible on every screen; printing counts as activity; session preserved via "Continue."

## 7. Outstanding / backlog (137 rows: 73 Done · 6 In Progress · 1 Blocked · 56 Not Started)
- **Blocked:** FEED-03 (staff-console credentials) pending owner approval.
- **In Progress / Not Started (highlights):** live backend wiring (FEED-02/FEED-05 on Supabase); POS/inventory live integration (INV-05/06) pending dispensary POS; INV realism Phase B (flavonoid-matching — touches the scorer, Demo-only, own regression pass); remaining PERF items; assorted UX polish. `Project_Backlog.xlsx` (themes PERF-/DRUG-/MOB-/ONB-/UX-/FEED-/INV-) is the single source of truth — update the row whenever work starts/changes/finishes.

## 8. Known issues / refinement areas — all resolved 2026-08-08
- ~~Naming drift (CLAUDE.md "Guided Finder")~~ — **resolved** (`f9de77c`): CLAUDE.md rule #2 reconciled to "Guided Match".
- ~~Class-entry brand display ("Benzodiazepines (Xanax)")~~ — **resolved** (`ce8bd69`, UX-32): `medLabel()` `isClass` flag on benzos/opioids.
- ~~FAQ condition-group count inconsistencies~~ — **resolved** (`f9de77c`, UX-31): both FAQ copies now match the data (48 conditions / 7 groups).
- ~~grp() `esc(djMolName())` double-escaping (demo-journey chips)~~ — **resolved** (`ce8bd69`, UX-32): now renders "Δ9-THC" correctly.
- **OPEN (found 2026-08-12, not fixed):** `CITATIONS` contains **10 exact-duplicate entries** — the same molecule with the same PMID *and* the same title listed twice (confirmed: CBG/`33562819`, MY/`37764505`, bCA/`30166097`, LI/`34362338`, aHU/`31259712`, plus 5 more). They render twice in the detail panel's Scientific References accordion and in the new molecule-profile printout. Deduping is safe **only** after confirming each pair really is one paper (per §4, verify against NCBI eutils — a pair could be two different papers where one carries a wrong PMID, which would need RPh review, not a silent delete).

## 9. Development philosophy & constraints (do not violate)
- **Public name = "V2 – The Periodic Table of Cannabis Plant Molecules."** "PhytoTable" is BANNED anywhere user-facing. Lead brand lockups with "V2 · The Periodic Table of Cannabis Plant Molecules"; inline use plain "V2" / "the Periodic Table." Tagline: "Mechanism Based · Evidence Informed · Easy to Explore" (UX-38, 2026-08-12; was "… Clinically Structured …").
- **Educational & research platform — NOT medical advice.** No diagnosis/dosing/product-or-treatment recommendation. Evidence grades = research quality, not approval/safety. Always "consult a licensed professional."
- **Guided ↔ Table navigation always exists** (🧭 Guided Match header button + mobile Guided tab; "Full table ✕" back). Preserve kiosk session on switch.
- **Standard Guided is product-free** (products = Demo Mode only). Don't alter `recForConditions()`/`match()` casually.
- **No fabricated PMIDs / no fabricated clinical claims.** Draft safety copy conservatively; flag for RPh review.
- **Comprehensive regression on every change:** syntax-check each `<script>` with `jsc` (`new Function(...)`), drive the guided flow end-to-end, keep all desktop + mobile enhancements intact, back up before large edits.
- **Process per build:** one change at a time → syntax-check → drive/verify (local `python3 -m http.server` for interactivity; the in-app Browser renders out-of-project files as static snapshots) → back up → commit → push `main` → confirm byte-identical (GitHub raw + Pages) → give the link → log/append the backlog row with the commit hash. Log even small UX polish as its own `UX-` row.

## 10. Recommendations for the next phase
1. **Backend go-live (highest leverage, owner-gated):** once Supabase creds arrive, wire FEED-02 (cross-kiosk feedback) + FEED-05 (anonymous research) — schema/RLS, flip flags, CSP, privacy review. This unlocks the research-platform value proposition.
2. **POS/inventory (INV-05/06):** partner with one dispensary's POS to turn the simulated live-stock/COA into real integration; keep the feed-agnostic matcher.
3. **Content depth:** expand curated `synergy` data so the Entourage example cites studies more often; broaden the drug DB (more agents + DDI pairs) with real PMIDs; RPh review pass on safety copy.
4. **Polish:** the §8 list is currently empty — no known refinement items are open. (Done and cleared: CLAUDE.md "Guided Finder"→"Guided Match", FAQ group counts, class-entry brand display, print/PDF Phase 2 — molecule-panel + Associated Molecules printouts (`4dfffea`) — and the `CITATIONS` duplicate-entry cleanup (`7c5f933`).) Next polish candidates come from the backlog's Not-Started `UX-` rows rather than a known-defect list.
5. **Keep the regression discipline** (jsc + guided drive + byte-identical verify) and the deploy-status check habit given GitHub's recent flakiness.

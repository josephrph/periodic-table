#!/usr/bin/env python3
"""
preflight.py — run this BEFORE every deploy. Exit 0 = safe to push.

Tier 2 of the self-maintenance plan (see UX-62 in Project_Backlog.xlsx). Tier 1 (V2FACTS)
stops counts from drifting by deriving them from the data. This script catches the classes
of problem that derivation cannot: stale fallbacks, malformed entities, broken data
references, banned terminology, and violations of the standing product rules in CLAUDE.md.

Every check here exists because the corresponding bug ACTUALLY SHIPPED at least once:

  fallback literals    "51 to 65" survived the cannabicitran merge (65 -> 64)
  double-escaped HTML  the water example read H&amp;sub2;O and could never render
  unknown entities     &sub2; is not a real entity even unescaped
  FAQ question drift   Essential Tremor was listed in one FAQ copy and not the other
  banned terminology   "PhytoTable" and "Clinically Structured" both had to be purged
  trademark lockups    the (TM) mark was missing from 13 brand lockups
  duplicate backlog ID two rows were filed as DRUG-A3 and two as MOB-07

    python3 preflight.py              # offline checks only (fast, no network)
    python3 preflight.py --online     # additionally verify every PMID against NCBI

Add a check whenever a content or data bug gets past review. That is the whole point:
this file is the project's memory of its own mistakes.
"""
import hashlib
import html.entities
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'index.html')
JSC = ('/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/'
       'Helpers/jsc')

FAILURES = []
NOTES = []


# ── projections of the source ──────────────────────────────────────────────────
# Several checks must look only at what a reader can actually SEE. Without this, the
# first run flagged 14 entity "bugs" that were really search-index attribute values, a
# banned typo that only appears in a comment documenting its own removal, and a lockup
# inside a comment quoting CLAUDE.md. A checker that cries wolf gets switched off.
def strip_comments(src):
    """Blank out HTML, CSS and JS comments, preserving offsets so context stays readable."""
    def blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    src = re.sub(r'<!--.*?-->', blank, src, flags=re.S)
    src = re.sub(r'/\*.*?\*/', blank, src, flags=re.S)
    # line comments: only when // starts a line (avoids gutting "https://" and regexes)
    src = re.sub(r'(?m)^(\s*)//[^\n]*', lambda m: m.group(1) + ' ' * (len(m.group(0)) - len(m.group(1))), src)
    return src


def strip_attributes(src):
    """Blank out double-quoted attribute values. Attributes are never rendered as text."""
    return re.sub(r'="[^"]*"', lambda m: '="' + ' ' * (len(m.group(0)) - 3) + '"', src)


def group_keys(raw):
    """A condition's group is usually a string, but a cross-listed one is an array.

    prostate-cancer is ['cancer-sub','mens']. JS gets this right for free because
    String(array) joins on commas; Python's str(list) does not, which made this script
    report 10 cancer sub-types where the app correctly shows 11.
    """
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw or '').split(',')
    return [str(p).strip() for p in parts if str(p).strip()]


def fail(check, msg):
    FAILURES.append((check, msg))


def note(msg):
    NOTES.append(msg)


# ── extracting the live data ───────────────────────────────────────────────────
def js_literal(src, marker, opener):
    """Brace/bracket-match a JS literal out of index.html, skipping strings and comments.

    The naive regex version of this silently dropped 32 of 65 molecules once, and a later
    version desynchronised on an apostrophe inside a // comment. Hence the explicit scanner.
    """
    i = src.index(marker)
    j = src.index(opener, i)
    closer = {'{': '}', '[': ']'}[opener]
    depth, k, in_str, quote, esc = 0, j, False, '', False
    while k < len(src):
        c = src[k]
        nxt = src[k + 1] if k + 1 < len(src) else ''
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == quote:
                in_str = False
        elif c == '/' and nxt == '/':
            k = src.find('\n', k)
            if k < 0:
                break
            continue
        elif c == '/' and nxt == '*':
            k = src.find('*/', k)
            if k < 0:
                break
            k += 2
            continue
        elif c in '\'"`':
            in_str, quote = True, c
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[j:k + 1]


def load_data(src):
    """Evaluate the app's own data objects in JavaScriptCore and return them as Python."""
    parts = {
        'M': js_literal(src, 'const M = {', '{'),
        'CONDITIONS': js_literal(src, 'CONDITIONS = [', '['),
        # DRUG-09: the drug tables are evaluated too. A regex-based audit of these reported 11
        # unreachable drug-drug pairs that did not exist — DI_DATA is written in TWO styles,
        # single-quoted JS and double-quoted JSON, and the pattern only saw the first. Same class
        # of mistake as the citation-array regex that produced a false "42 conditions uncited".
        'DI_DATA': js_literal(src, 'var DI_DATA = [', '['),
        'DDI_DATA': js_literal(src, 'var DDI_DATA = [', '['),
        # MED-01 Stage 2: drug-drug pairs may name a reviewed-only ingredient (e.g. sacubitril in Entresto),
        # because the medication list holds reviewed entries too and the matcher reads ids only.
        'DI_REVIEWED': js_literal(src, 'var DI_REVIEWED = [', '['),
        # DRUG-24: needed by check_hasrisk_invariant
        'ADVERSE_FINDINGS': js_literal(src, 'const ADVERSE_FINDINGS = {', '{'),
    }
    prog = ['var out = {};']
    for name, body in parts.items():
        prog.append('out.%s = %s;' % (name, body))
    prog.append('print(JSON.stringify(out));')
    tmp = os.path.join(HERE, '_preflight_tmp.js')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(prog))
    try:
        res = subprocess.run([JSC, tmp], capture_output=True, text=True, timeout=90)
    finally:
        os.remove(tmp)
    if res.returncode != 0 or not res.stdout.strip().startswith('{'):
        raise SystemExit('could not evaluate app data:\n' + (res.stderr or res.stdout)[:600])
    return json.loads(res.stdout)


# ── checks ─────────────────────────────────────────────────────────────────────
def check_scripts_parse(src):
    """Every inline <script> must still compile."""
    blocks = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', src, re.S)
    for i, body in enumerate(blocks):
        if not body.strip():
            continue
        tmp = os.path.join(HERE, '_preflight_blk%d.js' % i)
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(body)
        try:
            res = subprocess.run(
                [JSC, '-e',
                 'var s=readFile("%s");try{new Function(s);print("OK");}'
                 'catch(e){print("FAIL "+e);}' % tmp],
                capture_output=True, text=True, timeout=90)
        finally:
            os.remove(tmp)
        out = (res.stdout or '').strip()
        if not out.startswith('OK'):
            fail('scripts parse', 'script block %d: %s' % (i, out or res.stderr.strip()[:200]))


PROSE_FACT_KEYS = {'crossListedNote'}   # UX-127: rendered prose, asserted by check_cross_listed


def check_v2fact_keys(src, data):
    """Each data-v2fact key must be a real V2FACTS function, and its fallback must be current.

    A typo'd key fails silently — the span just keeps its literal forever, which is exactly
    the hand-maintained state Tier 1 was meant to remove. A stale literal is worse: it is
    the pre-UX-61 bug wearing a placeholder's clothes.
    """
    mols = data['M']
    conds = [c for c in data['CONDITIONS'] if c.get('id') != 'cancer-adverse']
    n_acid = sum(1 for k in mols if mols[k].get('category') == 'acid')
    groups = {}
    for c in conds:
        for g in group_keys(c.get('group')):
            groups.setdefault(g, []).append(c.get('label'))
    expected = {
        'molecules': len(mols),
        'acids': n_acid,
        'nonAcid': len(mols) - n_acid,
        'conditions': len(conds),
        'cancerSubtypes': len(groups.get('cancer-sub', [])),
        'v1Entries': 51,
        'addedSinceV1': len(mols) - 51,
    }
    # UX-74: receptor tallies. These sat in FAQ prose by hand and had drifted (CB1 read 16
    # against 17 in the data, CB2 read 23 against 22), so they are derived and asserted now.
    for rc in ('CB1', 'CB2', 'TRP', 'PPAR', '5HT'):
        expected['rec' + rc] = sum(
            1 for k in mols if rc in (mols[k].get('receptors') or []))
    found = re.findall(r'<span data-v2fact="([^"]+)"[^>]*>([^<]*)</span>', src)
    # UX-127: crossListedNote renders PROSE, not a scalar, so it has no entry in `expected`.
    # check_cross_listed() derives and asserts it instead; skipping it here keeps this guard
    # about numbers and stops it failing on a key it was never meant to validate.
    found = [(k, v) for k, v in found if k not in PROSE_FACT_KEYS]
    if not found:
        fail('V2FACTS wiring', 'no data-v2fact spans found at all')
    for key, literal in found:
        if key not in expected:
            fail('V2FACTS wiring',
                 'data-v2fact="%s" has no matching V2FACTS function' % key)
            continue
        want = str(expected[key])
        if literal.strip() != want:
            fail('V2FACTS fallback',
                 'data-v2fact="%s" fallback reads "%s" but the data says %s'
                 % (key, literal.strip(), want))
    # the generated containers must declare a style the module understands
    for style in re.findall(r'data-v2fact="conditionGroups"[^>]*data-v2fact-style="([^"]*)"', src):
        if style not in ('short', 'long'):
            fail('V2FACTS wiring', 'conditionGroups style "%s" is not short|long' % style)

    # UX-120: the conditionGroups fallbacks were NOT checked. check_v2fact_keys validated every
    # <span> fallback but these are <div>s, and only their style attribute was asserted — so while
    # every scalar stayed correct, the two group listings silently drifted four conditions behind
    # the data (Pain read 7 against 8, Neurological 12 against 14, Cancer & Immune 5 against 6;
    # Sickle Cell Disease, Spinal Cord Injury, Traumatic Brain Injury and HIV/AIDS were all
    # missing). Users never saw it because V2FACTS.apply() overwrites the container at load, but a
    # stale literal in the build is exactly what Tier 1 exists to prevent. Membership and counts
    # are asserted here, not markup, so wording and punctuation stay free to change.
    import html as _html
    label_of = {}
    for c in conds:
        for g in group_keys(c.get('group')):
            label_of.setdefault(g, []).append(c.get('label'))
    for m in re.finditer(r'<div data-v2fact="conditionGroups"[^>]*data-v2fact-style="(short|long)"[^>]*>([\s\S]*?)</div>', src):
        style, body = m.group(1), m.group(2)
        paras = re.findall(r'<u>([^<]*?)\s*\((\d+)[^)]*\):</u>([^<]*)', body)
        if len(paras) != len(label_of):
            fail('V2FACTS fallback',
                 'conditionGroups[%s] lists %d groups but the data has %d'
                 % (style, len(paras), len(label_of)))
            continue
        for gname, gcount, members in paras:
            gname = _html.unescape(gname).strip()
            listed = [x.strip() for x in _html.unescape(members).strip().rstrip('.').split(',') if x.strip()]
            if int(gcount) != len(listed):
                fail('V2FACTS fallback',
                     'conditionGroups[%s] "%s" says (%s) but lists %d conditions'
                     % (style, gname, gcount, len(listed)))
            match = [g for g in label_of if len(label_of[g]) == int(gcount)
                     and sorted(x.replace(' / ', '/') for x in label_of[g])
                         == sorted(x.replace(' / ', '/') for x in listed)]
            if not match:
                want = [(g, len(label_of[g])) for g in label_of]
                fail('V2FACTS fallback',
                     'conditionGroups[%s] "%s (%s)" does not match any group in the data; '
                     'data groups are %s' % (style, gname, gcount, want))
    note('V2FACTS: conditionGroups fallbacks match the data (%d groups)' % len(label_of))
    note('V2FACTS: %d spans checked against live data %s' % (len(found), expected))


def check_demo_guided_parity(src, data):
    """UX-123: Demo Mode must not keep private copies of guided data.

    Demo Mode and Guided Match are two flows over one dataset. Where they duplicated a table
    instead of sharing it, they drifted — GUIDED_MOL was copied wholesale into DJ_GUIDED_MOL
    because a closure boundary put the original out of reach, and the science-alias map was
    written out five separate times. This asserts the de-duplication holds and that every
    condition the demo offers is a real one.
    """
    body = strip_comments(src)

    # 1. no second copy of the curated guided-molecule profiles
    if 'DJ_GUIDED_MOL' in body:
        fail('demo parity',
             'DJ_GUIDED_MOL is back — the demo must read window.V4Guided.guidedMol, not its own copy')

    # 2. exactly one literal of the science-alias map
    n_alias = len(re.findall(r"'appetite-stim'\s*:\s*'appetite'", body))
    if n_alias != 1:
        fail('demo parity',
             'the appetite/nausea science-alias map appears %d times; it must be declared once'
             % n_alias)

    # 3. every id the demo offers must resolve — a real condition, or one of the curated
    #    guided-only profiles that the science layer aliases back to a real condition
    m = re.search(r'var DJ_CONDS\s*=\s*\[(.*?)\];', body, re.S)
    if not m:
        fail('demo parity', 'DJ_CONDS not found')
        return
    dj_ids = re.findall(r"id:'([^']+)'", m.group(1))
    real = {c.get('id') for c in data['CONDITIONS']}
    curated = set(re.findall(r"'([a-z-]+)'\s*:\s*\{[A-Z]", 
                             re.search(r'var GUIDED_MOL=\{(.*?)\};', body, re.S).group(1))) \
              if re.search(r'var GUIDED_MOL=\{(.*?)\};', body, re.S) else set()
    orphans = [i for i in dj_ids if i not in real and i not in curated]
    if orphans:
        fail('demo parity',
             'Demo Mode offers condition id(s) that resolve to nothing: %s' % ', '.join(orphans))
    note('demo parity: %d demo conditions all resolve (%d real, %d curated profiles); '
         'no duplicated guided tables'
         % (len(dj_ids), len([i for i in dj_ids if i in real]),
            len([i for i in dj_ids if i in curated])))


def check_entities(src):
    """No double-escaped entities, and no entity names the browser will not know.

    Scoped to rendered text: attribute values legitimately contain escaped entity text
    (the tile search index stores "&delta;9-thc" so a search for "delta" still matches),
    and comments are not rendered at all.
    """
    src = strip_attributes(strip_comments(src))
    for m in re.finditer(r'&amp;([a-zA-Z][a-zA-Z0-9]{1,12});', src):
        ctx = re.sub(r'\s+', ' ', src[max(0, m.start() - 45):m.end() + 25])
        fail('HTML entities',
             'double-escaped "&amp;%s;" — renders literally: …%s…' % (m.group(1), ctx))
    known = set(html.entities.entitydefs)
    for m in re.finditer(r'&([a-zA-Z][a-zA-Z0-9]{1,12});', src):
        name = m.group(1)
        if name not in known:
            ctx = re.sub(r'\s+', ' ', src[max(0, m.start() - 45):m.end() + 25])
            fail('HTML entities', 'unknown entity "&%s;" …%s…' % (name, ctx))


def check_banned_terms(src):
    """Terminology the owner has explicitly retired. See CLAUDE.md standing rules.

    Comments are excluded: a note explaining that a typo was fixed is not the typo.
    """
    src = strip_comments(src)
    banned = {
        'PhytoTable': 'retired product name (must not appear in any user-facing text)',
        'Clinically Structured': 'removed from the tagline (UX-38)',
        'Perioidic': 'typo that shipped once',
        'Cannbicitran': 'typo that shipped once',
    }
    for term, why in banned.items():
        # the staff-only export filename is an accepted internal exception
        hits = [m.start() for m in re.finditer(re.escape(term), src, re.I)
                if 'phytotable_feedback' not in src[max(0, m.start() - 30):m.start() + 40].lower()]
        if hits:
            fail('banned terminology', '"%s" appears %d time(s) — %s' % (term, len(hits), why))


def check_brand_lockups(src):
    """Standing rule: every brand lockup carries the trademark mark (UX-54).

    Comments excluded — CLAUDE.md's own wording is quoted in a CSS comment.
    """
    src = strip_comments(src)
    # NAMING 2026-09-10: the official name was corrected to "Acannability's Cannabis Periodic
    # Table of Molecules™". The mark now attaches to "Cannabis Periodic Table of Molecules".
    #
    # THIS GUARD HAS BROKEN TWICE THE SAME WAY — read before editing the name again.
    # It anchors on the name string, so ANY change to that string makes it match nothing and
    # pass silently. On 2026-09-09 the pattern still required a leading "The" and policed only
    # 4 of 18 lockups. Had it not been rewritten again here, the 2026-09-10 correction would
    # have removed its target string entirely and it would have policed ZERO.
    # The zero-match tripwire below is what makes that impossible now: if the count ever drops
    # to 0, the guard FAILS instead of reporting success.
    #
    # "Cannabis" may be wrapped in <em> (header, welcome overlay, both grid logo tags), and the
    # name appears both possessive ("Acannability's Cannabis Periodic Table of Molecules™") and
    # with a plain article ("the Cannabis Periodic Table of Molecules™ chart", in FAQ body copy),
    # so the pattern deliberately anchors on the name alone and ignores what precedes it.
    pat = re.compile(r'(?:(?:<em>)?Cannabis(?:</em>)? Periodic Table of Molecules)'
                     r'(&trade;|&#8482;|™)?')
    missing = 0
    total = 0
    for m in pat.finditer(src):
        total += 1
        if not m.group(1):
            ctx = re.sub(r'\s+', ' ', src[max(0, m.start() - 60):m.end() + 20])
            fail('trademark lockups', 'lockup without the mark: …%s…' % ctx)
            missing += 1
    if total == 0:
        fail('trademark lockups',
             'the brand-lockup pattern matched NOTHING — the official name has almost certainly '
             'changed again and this guard is now checking zero lockups. Update the regex in '
             'check_brand_lockups to the current name before trusting this run.')
    elif not missing:
        note('trademark: all %d "Cannabis Periodic Table of Molecules" lockups carry the mark'
             % total)


def check_standing_rules(src):
    """The non-negotiables from CLAUDE.md that a refactor could quietly break."""
    required = {
        'id="guidedBtn"': 'persistent Guided Match header button',
        'id="mtbGuided"': 'mobile Guided tab',
        'Mechanism Based &#183; Evidence Informed &#183; Easy to Explore':
            'header tagline (UX-38 wording)',
        'id="inactivityWarn"': 'inactivity warning element',
        'id="inactivityCountdown"': 'inactivity countdown element',
        'is-mobile-view': 'mobile view class',
    }
    for needle, what in required.items():
        probe = needle.replace('&#183;', '·')
        if needle not in src and probe not in src:
            fail('standing rules', 'missing %s (%s)' % (needle, what))


def check_data_integrity(src, data):
    """The molecule/condition graph must not reference anything that does not exist."""
    mols, conds = data['M'], data['CONDITIONS']
    tiles = re.findall(r'<div class="mol-tile [^"]*"[^>]*?data-id="([^"]+)"', src)
    if len(tiles) != len(mols):
        fail('data integrity',
             '%d .mol-tile divs but %d entries in M' % (len(tiles), len(mols)))
    for t in tiles:
        if t not in mols:
            fail('data integrity', 'tile data-id="%s" is not in M' % t)
    # one tile per grid cell
    cells = re.findall(r'grid-area:\s*(\d+)\s*/\s*(\d+)', src)
    dupes = {c for c in cells if cells.count(c) > 1}
    if dupes:
        fail('data integrity', 'two tiles share grid cell(s): %s' % sorted(dupes))
    # duplicate symbols read as a data-entry slip (a real one shipped: cannabicitran)
    syms = [mols[k].get('symbol') for k in mols]
    dup_syms = sorted({s for s in syms if syms.count(s) > 1})
    if dup_syms:
        fail('data integrity', 'duplicate molecule symbols: %s' % dup_syms)
    # every condition points at molecules that exist, and has at least one
    for c in conds:
        cid = c.get('id')
        mset = c.get('molecules') or {}
        if cid != 'cancer-adverse' and not mset:
            fail('data integrity', 'condition "%s" has no molecules' % cid)
        for mid in mset:
            if mid not in mols:
                fail('data integrity',
                     'condition "%s" references unknown molecule "%s"' % (cid, mid))


def check_faq_parity(src):
    """The two FAQ copies must answer the same set of questions.

    They are allowed to differ in depth — the overlay is a condensed tier — but a question
    present in one and absent from the other is how Essential Tremor went missing.
    """
    i = src.index('id="wlc-faqs"')
    i = src.rindex('<div', 0, i)
    depth, k = 0, i
    while k < len(src):
        if src.startswith('<div', k):
            depth += 1
        elif src.startswith('</div>', k):
            depth -= 1
            if depth == 0:
                k += 6
                break
        k += 1
    overlay = src[i:k]
    b0 = src.index('<section id="faq-section">')
    main = src[b0:src.index('</section>', b0)]

    def questions(blk):
        out = []
        for m in re.finditer(r'<span class="faq-q"[^>]*>(.*?)</span>', blk, re.S):
            q = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', m.group(1))).strip().lower()
            out.append(re.sub(r'[^a-z0-9 ]', '', q))
        return out

    qa, qb = questions(overlay), questions(main)
    # compare on a loose key so wording differences ("…of this table") do not trip it
    def key(q):
        drop = {'the', 'a', 'an', 'of', 'this', 'in', 'and', 'is', 'are', 'does', 'do',
                'what', 'how', 'were', 'v2', 'table', 'chart', 'my', 'me', 'i', 'it'}
        return ' '.join(sorted(w for w in q.split() if w not in drop))
    ka, kb = {key(q) for q in qa}, {key(q) for q in qb}
    for k2 in sorted(ka - kb):
        if k2:
            note('FAQ: only in the How-to-Use overlay — "%s"' % k2)
    for k2 in sorted(kb - ka):
        if k2:
            note('FAQ: only in the main FAQ — "%s"' % k2)
    note('FAQ parity: overlay %d questions, main %d, %d shared'
         % (len(qa), len(qb), len(ka & kb)))


def check_pmids(src, online=False):
    """PMIDs must be plausible, and optionally must actually resolve at NCBI."""
    # BOTH quote styles: the curated citation objects use pmid:'...' while the JSON-shaped
    # DI_DATA records use "pmid":"...". Matching only the first undercounted by 15.
    # AUDIT-09: this used to be r'[\'"](\d{5,8})[\'"]', which requires the closing quote to sit
    # immediately after the digits. That silently skipped EVERY multi-PMID field — 'pmid' values
    # holding several ids separated by commas, which is how the drug tables cite more than one
    # paper — and it also let 'N/A - theoretical' through without comment. Capture the whole field,
    # then split it the way diPmidList() does in the app, so preflight verifies exactly the set of
    # ids the page will turn into links.
    raw_fields = re.findall(r'"?pmid"?\s*:\s*[\'"]([^\'"]*)[\'"]', src)
    pmids, junk = set(), set()
    for field in raw_fields:
        for tok in re.split(r'[,;]', field):
            tok = tok.strip()
            if not tok:
                continue
            if re.fullmatch(r'\d{5,8}', tok):
                pmids.add(tok)
            else:
                junk.add(field.strip())
    pmids = sorted(pmids)
    if junk:
        note('citations: %d pmid field(s) carry non-PMID text and render no link (by design): %s'
             % (len(junk), sorted(junk)[:2]))
    bad = [p for p in pmids if not (5 <= len(p) <= 8)]
    if bad:
        fail('citations', 'implausible PMIDs: %s' % bad[:12])
    note('citations: %d distinct PMIDs referenced' % len(pmids))
    if not online:
        return
    missing = []
    for i in range(0, len(pmids), 180):
        batch = pmids[i:i + 180]
        url = ('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
               '?db=pubmed&retmode=json&id=' + ','.join(batch))
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                res = json.load(r).get('result', {})
        except Exception as exc:                     # network trouble is not a content bug
            note('citations: NCBI lookup skipped (%s)' % exc)
            return
        for p in batch:
            rec = res.get(p)
            if not rec or rec.get('error') or not rec.get('title'):
                missing.append(p)
    if missing:
        fail('citations', '%d PMID(s) did not resolve at NCBI: %s'
             % (len(missing), missing[:12]))
    else:
        note('citations: all %d PMIDs resolved at NCBI' % len(pmids))


def check_terminology(src):
    """Tier 3: category labels must come from V2TERMS, and the tagline must be canonical.

    Five separate label maps had drifted so that the 'acid' category answered to four
    different names in four places. Any new literal map is how that happens again.
    """
    src_nc = strip_comments(src)
    # a label map is a literal object keyed by the five category ids
    for m in re.finditer(r'(?:const|var|let)\s+(\w*[Cc]at[A-Za-z_]*)\s*=\s*\{([^}]*)\}', src_nc):
        body = m.group(2)
        if 'phytocannabinoid' in body and ('terpene' in body and 'flavonoid' in body):
            # a LABEL map holds words; catColor holds hex values — only flag the former
            vals = re.findall(r":\s*'([^']*)'", body)
            if vals and not any(v.startswith('#') for v in vals):
                fail('terminology',
                     'literal category-label map "%s" — derive it from V2TERMS.map() instead'
                     % m.group(1))
    # the tagline is fixed by CLAUDE.md standing rule #1
    canon_words = ['Mechanism Based', 'Evidence Informed', 'Easy to Explore']
    for m in re.finditer(r'Mechanism[ \-]Based[^<"\']{0,70}', src_nc):
        text = m.group(0)
        plain = text.replace('&middot;', '·')
        if not all(w in plain for w in canon_words):
            fail('terminology',
                 'tagline variant "%s" — CLAUDE.md fixes the wording as "%s"'
                 % (plain.strip(), 'Mechanism Based · Evidence Informed · Easy to Explore'))
    if 'window.V2TERMS' not in src:
        fail('terminology', 'V2TERMS is missing — category labels have no single source')
    else:
        note('terminology: category labels sourced from V2TERMS; tagline wording canonical')


def check_recommendation_wording(src):
    """UX-66: the recommendation heading and its attribution must stay uniform.

    The old wording ("What the research associates", "The molecules V2 associates with") sat in
    four places — two screens and two FAQ copies that quote the screen title. Renaming only the
    screens would leave the FAQ describing a title that no longer exists, which is the same drift
    class as the counts before V2FACTS.
    """
    src_nc = strip_comments(src)
    retired = {
        'What the research associates': 'old recommendation screen heading',
        'V2 associates': 'attributed the association to V2 rather than the literature',
        'research associates': 'superseded by "research links to"',
    }
    for phrase, why in retired.items():
        hits = len(re.findall(re.escape(phrase), src_nc, re.I))
        if hits:
            fail('recommendation wording',
                 '"%s" appears %d time(s) — %s' % (phrase, hits, why))
    heading = 'Research-Linked Molecules for Your Health Topic'
    n = len(re.findall(re.escape(heading), src_nc))
    # 2 rendered screens (standard + Demo Mode) and 2 FAQ copies quoting the title
    if n != 4:
        fail('recommendation wording',
             'heading "%s" appears %d time(s), expected 4 (2 screens + 2 FAQ quotes)' % (heading, n))
    else:
        note('recommendation wording: heading present in all 4 expected places, no retired phrasings')
    # any topic flagged evidenceNegative must actually carry adverse-findings copy, or the
    # softened wording would be the only thing telling the reader the trials were null
    for m in re.finditer(r"\{id:'([a-z0-9\-]+)',label:'([^']*)'[^}]*?evidenceNegative:true", src):
        if m.group(2) not in src:
            fail('recommendation wording',
                 'condition "%s" is flagged evidenceNegative but has no findings copy' % m.group(2))


def check_greek_notation(src):
    """UX-70: Greek prefixes are written as symbols, and BOTH notations stay searchable.

    Three separate traps make this worth asserting rather than trusting:

    1. Quoted literature must keep the publisher's spelling. A citation title reading
       "Delta-9-tetrahydrocannabinol enhances breast cancer growth" is what the journal
       printed; rewriting it to Δ9 falsifies the reference. So this check deliberately
       ignores `title:` fields and only polices V2's OWN name fields.
    2. Search keys must survive the change. `data-name` and PUBMED_TERMS are haystacks, not
       labels — indexing only "&Delta;9-..." would break the very common query "delta-9"
       and send PubMed a term that returns nothing.
    3. Adrenergic receptor subtypes and drug classes are NOT cannabis molecules.
       "beta-blockers", "Beta-Adrenergic" and "Beta-lactamase" are the standard clinical
       spellings and a blanket replace would corrupt them.
    """
    # 1. no molecule name field may spell a prefix out
    spelled = []
    for m in re.finditer(r"fullName:'([^']*)'", src):
        if re.search(r'\b(Delta|Alpha|Beta|Gamma)-', m.group(1)):
            spelled.append(m.group(1))
    if spelled:
        fail('greek', 'fullName still spells out a Greek prefix: %s' % ', '.join(spelled[:4]))

    # 2. both notations must remain searchable
    if 'pubmedName(mol.fullName)' not in src:
        fail('greek', 'tile search keys no longer carry the spelled-out alias — '
                      '"delta-9" would stop matching')
    # bound the map at its own closing brace — a lazy ".*?\n  };" runs straight past it
    # into unrelated code and then reports Greek found in a comment.
    i = src.find('const PUBMED_TERMS = {')
    if i == -1:
        fail('greek', 'PUBMED_TERMS map is missing; PubMed links lose their curated terms')
    else:
        body = src[i:src.index('};', i)]
        if re.search(r'[\u0394\u03b1\u03b2]', body):
            fail('greek', 'PUBMED_TERMS contains a Greek symbol; PubMed indexes the '
                          'spelled-out form')

    # 3. clinical terms that must NOT be converted
    for term in ('Beta-Blockers', 'Beta-Adrenergic', 'Beta-lactamase'):
        if term not in src:
            fail('greek', 'clinical term "%s" was wrongly converted to a Greek symbol' % term)

    n_titles = len([t for t in re.findall(r"title:'((?:[^'\\]|\\.)*)'", src)
                    if re.search(r'\b(Delta|Alpha|Beta)-', t)])
    note('greek notation: name fields use symbols; %d citation titles correctly left as '
         'published; spelled-out search aliases intact' % n_titles)


def check_pinch_zoom(src):
    """UX-71: no rule may withhold pinch-zoom, and the viewport may not forbid scaling.

    Accessibility floor: a reader must always be able to enlarge the page. Two ways to
    break it, both of which have bitten this file:

    1. `touch-action: pan-y` or `none`. These read as "let the browser scroll but not
       pan sideways" and quietly withhold two-finger zoom as well. The molecule sheet
       covers ~85% of a phone screen, so once it did that, a reader who had zoomed in
       had almost nowhere to place two fingers to zoom back out. Every gesture this
       file drives is single-finger, so naming pinch-zoom costs nothing.
    2. `user-scalable=no` / `maximum-scale=1` in the viewport meta. Never add these.
    """
    for meta in re.findall(r'<meta name="viewport"[^>]*>', src):
        for banned in ('user-scalable=no', 'user-scalable=0', 'maximum-scale=1'):
            if banned in meta.replace(' ', ''):
                fail('pinch', 'viewport meta forbids zoom (%s) — readers cannot enlarge '
                              'the page' % banned)

    ok_alone = {'auto', 'manipulation'}   # both already permit pinch-zoom
    offenders = []
    for m in re.finditer(r'([^\n{}]+)\{([^}]*?)touch-action\s*:\s*([^;}]+)', src):
        sel, val = m.group(1).strip(), m.group(3).strip()
        if val in ok_alone or 'pinch-zoom' in val:
            continue
        offenders.append('%s {touch-action:%s}' % (sel.split('\n')[-1].strip()[:52], val))
    if offenders:
        fail('pinch', 'these rules withhold pinch-zoom — add "pinch-zoom" to the value: %s'
                      % '; '.join(offenders[:3]))

    n = len(re.findall(r'touch-action\s*:', strip_comments(src)))
    note('pinch-zoom: viewport allows scaling; all %d touch-action rules permit two-finger '
         'zoom' % n)


def check_unwrapped_counts(src):
    """Counts describing V2's own data must be DERIVED, never typed into prose.

    check_v2fact_keys validates every <span data-v2fact> and its fallback. The gap it cannot
    see is a number typed straight into a sentence and never wrapped — "16 molecules engage
    CB1" sailed through for months while the data said 17. This closes that gap.

    Anything inside a data-v2fact element is derived (or is its own checked fallback), so
    those elements are removed before scanning. General statements about the plant rather
    than about V2's tables ("over 100 cannabinoids identified in cannabis") are not counts
    of anything here, so hedged numbers are skipped.
    """
    body = strip_comments(src)
    body = re.sub(r'<script.*?</script>', '', body, flags=re.S)
    # drop derived elements wholesale — span for scalars, div for the group paragraphs
    body = re.sub(r'<span data-v2fact=.*?</span>', ' ', body, flags=re.S)
    body = re.sub(r'<div data-v2fact=.*?</div>', ' ', body, flags=re.S)

    nouns = r'(?:health\s+)?(?:conditions?|topics?|molecules?|cannabinoids?|terpenes?|flavonoids?)'
    hedged = re.compile(r'(?:over|about|approximately|more than|nearly|some|than)\s*$', re.I)
    bad = []
    for m in re.finditer(r'(\d{2,3})\s*' + nouns, body, re.I):
        before = body[max(0, m.start() - 30):m.start()]
        if hedged.search(before):
            continue                       # a fact about the plant, not a count of our data
        if re.search(r'from\s+\d{2,3}\s+to\s*$', before):
            continue                       # "expanded from 30 to <derived>"
        bad.append(re.sub(r'\s+', ' ', body[max(0, m.start() - 45): m.end() + 5]).strip())
    if bad:
        fail('derived counts',
             'these counts are typed into prose instead of derived from the data '
             '(wrap in <span data-v2fact="...">): %s' % ' | '.join(bad[:3]))
    else:
        note('derived counts: every count describing the data is derived, none hardcoded')


def check_pubmed_links(src, data):
    """UX-77: no PubMed link may open a search that returns nothing.

    V2 is offline-first and cannot ask NCBI at render time, so every search the file can
    build was probed against the E-utilities API and the empty ones recorded in
    PUBMED_EMPTY. Links for those are replaced by a plain statement of absence.

    This check cannot re-run the probe (preflight is offline by default). What it can do is
    notice when the probe has gone out of date: if a molecule, indication, condition or
    molecule-condition pairing has been added since, its searches have never been verified
    and may be dead on arrival. The manifest makes that detectable instead of silent.
    """
    if 'const PUBMED_EMPTY' not in src:
        fail('pubmed links', 'PUBMED_EMPTY is missing — dead-link suppression is not wired')
        return
    blk = src[src.index('const PUBMED_EMPTY'):]
    blk = blk[:blk.index('\n};')]
    m = re.search(r"probed:\s*\{([^}]*)\}", blk)
    if not m:
        fail('pubmed links', 'PUBMED_EMPTY has no coverage manifest')
        return
    claimed = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", m.group(1))}

    mols = data['M']
    conds = [c for c in data['CONDITIONS'] if c.get('id') != 'cancer-adverse']
    n_pairs = sum(len(mols[k].get('indications') or []) for k in mols)
    n_combo = sum(len(c.get('molecules') or {}) for c in data['CONDITIONS'])
    actual = {'pairs': n_pairs, 'mols': len(mols),
              'conds': len(conds) + 1, 'combos': n_combo}   # +1: the adverse filter renders too

    for key in ('pairs', 'mols', 'conds', 'combos'):
        if claimed.get(key) != actual[key]:
            fail('pubmed links',
                 'content changed since the PubMed probe (%s: probed %s, now %s) — re-run the '
                 'probe and refresh PUBMED_EMPTY, or new links may open empty searches'
                 % (key, claimed.get(key), actual[key]))

    for kind in ('pair', 'mol', 'ent', 'syn', 'combo'):
        if not re.search(kind + r':\s*\[', blk):
            fail('pubmed links', 'PUBMED_EMPTY.%s is missing' % kind)
    if '[ti]' in strip_comments(src):
        fail('pubmed links', 'a title-only [ti] search is back; use [tiab] (see UX-76)')

    n = sum(len(re.findall(r"'", re.search(k + r':\s*\[(.*?)\]', blk, re.S).group(1))) // 2
            for k in ('pair', 'mol', 'ent', 'syn', 'combo')
            if re.search(k + r':\s*\[(.*?)\]', blk, re.S))
    note('pubmed links: %d verified-empty searches suppressed; probe covers %d pairs, '
         '%d molecules, %d conditions, %d combinations'
         % (n, claimed['pairs'], claimed['mols'], claimed['conds'], claimed['combos']))


def check_ddi_reachable(data):
    """DRUG-09: every drug-drug pair must be able to fire, and no drug id may be duplicated.

    Reads the EVALUATED tables, not the source text. The first version of this check pattern-matched
    index.html and reported 11 unreachable pairs that were all fine — DI_DATA is written in two
    styles, single-quoted JS and double-quoted JSON, and the pattern only saw the first. The same
    mistake, in the same shape, as the citation regex that once produced a false "42 of 59
    conditions uncited". If a check can be written against the real objects, write it that way.

    Two real defects this catches:
      * a pair naming a drug with no DI_DATA record can never fire — the medication list is built
        from DI_DATA ids, so an absent id can never appear in medIds;
      * a duplicated drug id makes idToIndex resolve to whichever entry came last, so one of the
        two records becomes unreachable and the interaction shown may not be the one curated for
        that drug. Caught exactly once, on a duplicate this session's own author introduced.
    """
    di, ddi = data.get('DI_DATA') or [], data.get('DDI_DATA') or []
    if not di or not ddi:
        fail('ddi reachable', 'DI_DATA / DDI_DATA did not evaluate')
        return
    seen, dupes = {}, []
    for i, e in enumerate(di):
        rid = e.get('id')
        if rid in seen:
            dupes.append('%s (entries %d and %d)' % (rid, seen[rid], i))
        else:
            seen[rid] = i
    for d in dupes:
        fail('ddi reachable', 'duplicate DI_DATA drug id: %s — one record is unreachable' % d)
    for r in data.get('DI_REVIEWED') or []:   # reviewed-only ingredients can be listed, so they can fire a pair
        seen.setdefault(r.get('id'), -1)
    dead = []
    for pair in ddi:
        groups = pair.get('groups')
        if groups:
            ok = all(any(x in seen for x in g) for g in groups if g)
        else:
            drugs = pair.get('drugs') or []
            # without explicit groups the matcher tests only the first two ids
            ok = len(drugs) >= 2 and drugs[0] in seen and drugs[1] in seen
        if not ok:
            missing = [x for x in (pair.get('drugs') or []) if x not in seen]
            dead.append('%s (missing %s)' % (pair.get('id'), ', '.join(missing) or 'a required drug'))
    for d in dead:
        fail('ddi reachable', 'drug-drug pair can never fire: %s' % d)
    # a pair with >2 drugs and no groups silently ignores everything past the second
    flat = [p.get('id') for p in ddi
            if not p.get('groups') and len(p.get('drugs') or []) > 2]
    for f in flat:
        fail('ddi reachable',
             'pair %s lists more than two drugs without `groups` — elements 3+ are ignored by the '
             'matcher' % f)
    if not dupes and not dead and not flat:
        note('ddi reachable: %d drugs, %d pairs, all pairs can fire, no duplicate ids'
             % (len(di), len(ddi)))


def check_ddi_referenced_ids(data):
    """DRUG-27: every id referenced by every pair must exist AND actually route.

    Why this exists as a separate guard from check_ddi_reachable(). That one asks only whether a
    pair *can* fire — `all(any(x in seen for x in g) for g in groups)`. One valid member per group
    satisfies it, so a stale id sitting beside seven good ones is invisible. That is exactly how
    `rasagiline_serotonergic` shipped listing `snris` when the record id is `snri`: the pair fired
    for SSRIs and fluvoxamine, the guard passed, and `rasagiline` + an SNRI silently matched
    nothing — on a major/A pair whose own label reads "contraindicated". Found 2026-09-03 by a
    per-id sweep during the W2 closeout, not by preflight.

    Checks, per pair:
      * every id in `drugs` and in every `groups` sub-array resolves to a DI_DATA record;
      * `drugs` and the flat union of `groups` agree — drift between the two means an id was added
        to one and not the other, and the matcher only reads `groups`;
      * every group member genuinely routes: choosing that member plus one member from each other
        group fires the pair under the matcher's own rule.
    """
    di, ddi = data.get('DI_DATA') or [], data.get('DDI_DATA') or []
    if not di or not ddi:
        fail('ddi referenced ids', 'DI_DATA / DDI_DATA did not evaluate')
        return
    ids = {e.get('id') for e in di} | {r.get('id') for r in (data.get('DI_REVIEWED') or [])}
    bad_refs, drift, unrouted, n_refs = [], [], [], 0
    for pair in ddi:
        pid = pair.get('id')
        groups = [g for g in (pair.get('groups') or []) if g]
        drugs = pair.get('drugs') or []
        flat = [x for g in groups for x in g]
        for x in drugs + flat:
            n_refs += 1
            if x not in ids:
                bad_refs.append('%s -> %r' % (pid, x))
        if groups:
            if set(drugs) != set(flat):
                only_d = sorted(set(drugs) - set(flat))
                only_g = sorted(set(flat) - set(drugs))
                drift.append('%s (drugs-only: %s | groups-only: %s)'
                             % (pid, ', '.join(only_d) or 'none', ', '.join(only_g) or 'none'))
            # the matcher fires when every group has at least one selected member
            for gi, g in enumerate(groups):
                for member in g:
                    if member not in ids:
                        continue
                    picked = {member}
                    for gj, other in enumerate(groups):
                        if gj == gi:
                            continue
                        live = [y for y in other if y in ids]
                        if live:
                            picked.add(live[0])
                    if not all(any(y in picked for y in gg) for gg in groups):
                        unrouted.append('%s via %s' % (pid, member))
    for b in sorted(set(bad_refs)):
        fail('ddi referenced ids', 'pair references a non-existent drug record: %s' % b)
    for d in drift:
        fail('ddi referenced ids', '`drugs` and `groups` disagree: %s' % d)
    for u in sorted(set(unrouted)):
        fail('ddi referenced ids', 'group member cannot route to its pair: %s' % u)
    if not bad_refs and not drift and not unrouted:
        note('ddi referenced ids: %d references across %d pairs all resolve to a drug record, '
             '`drugs` matches `groups`, and every member routes' % (n_refs, len(ddi)))


ROUTE_VOCAB = {
    'route':     {'combustion'},
    'target':    {'CYP1A2'},
    'direction': {'induction', 'inhibition'},
    'ev':        {'A', 'B', 'C', 'D'},
    'basis':     {'cannabis-measured', 'cannabis-confounded', 'tobacco-measured', 'mechanism-review'},
}
ROUTE_KEYS = {'route', 'target', 'direction', 'ev', 'basis', 'pmid', 'source', 'appliesTo'}


def check_routes(data, src):
    """ROUTE-01: the exposure/route axis, and the nine safeguards that keep it apart from `mols`.

    W3 established that combustion-driven CYP1A2 induction comes from the polycyclic aromatic
    hydrocarbons in smoke, not from any cannabinoid — so it can never earn a molecule tag, while
    remaining real and (for theophylline) the best-evidenced cannabis interaction in the database.
    `routes` carries it. The whole point of a separate field is that the separation is mechanical
    rather than a matter of wording discipline, which this project has watched drift four times.

    `routes` is OPTIONAL and deliberately absent from di_required: adding it there would fail all
    281 records. Everything below is skipped for a record that does not carry it.
    """
    di = data.get('DI_DATA') or []
    if not di:
        fail('routes', 'DI_DATA did not evaluate')
        return
    mol_ids = set((data.get('M') or {}).keys())
    withr = [r for r in di if r.get('routes')]
    n_entries = sum(len(r['routes']) for r in withr)
    bad = []

    for r in withr:
        rid = r.get('id')
        for i, x in enumerate(r['routes']):
            where = '%s[%d]' % (rid, i)
            if not isinstance(x, dict):
                bad.append('%s: route entry is not an object' % where); continue
            # -- schema + controlled vocabulary
            unknown = set(x) - ROUTE_KEYS
            if unknown:
                bad.append('%s: unknown key(s) %s' % (where, ', '.join(sorted(unknown))))
            for k in ('route', 'target', 'direction', 'ev', 'basis'):
                if k not in x:
                    bad.append('%s: missing required key %r' % (where, k))
                elif x[k] not in ROUTE_VOCAB[k]:
                    bad.append('%s: %s=%r is not in the controlled vocabulary %s'
                               % (where, k, x[k], sorted(ROUTE_VOCAB[k])))
            # -- SAFEGUARD 1: a route entry may never name a molecule
            for k, v in x.items():
                vals = v if isinstance(v, list) else [v]
                for val in vals:
                    if isinstance(val, str) and val in mol_ids:
                        bad.append('%s: %s=%r is a MOLECULE id — routes must never name a molecule'
                                   % (where, k, val))
            # -- SAFEGUARD 5: every entry carries its own evidence
            has_pmid = bool(re.search(r'\d{7,8}', str(x.get('pmid') or '')))
            if not has_pmid and x.get('source') not in ALLOWED_SOURCES:
                bad.append('%s: no valid PMID and no approved source' % where)
            # -- SAFEGUARD 6: appliesTo must resolve to something the record actually covers
            for who in (x.get('appliesTo') or []):
                hay = (str(r.get('drug') or '') + ' ' + str(r.get('brands') or '')).lower()
                if who.lower() not in hay:
                    bad.append('%s: appliesTo %r does not appear in this record\'s drug/brands'
                               % (where, who))
            # -- SAFEGUARD 3/4: the two grades must not be copies of one another by construction.
            # A shared value is legitimate (theophylline is B on both, from the same study answering
            # two different questions), so this only flags the case where EVERY route entry in the
            # database mirrors its parent — the signature of an inherited rather than assigned grade.
    if withr and all(x['ev'] == r.get('ev') for r in withr for x in r['routes']):
        bad.append('every route ev equals its record ev across all %d records — grades look '
                   'inherited rather than independently assigned' % len(withr))

    # -- SAFEGUARD 2: highlighting must read `mols` only
    m = re.search(r'function applyDrugHighlighting\(.*?\n\}', src, re.S)
    if not m:
        bad.append('could not locate applyDrugHighlighting to verify it ignores routes')
    elif re.search(r'\broutes\b', m.group(0)):
        bad.append('applyDrugHighlighting references `routes` — route entries must never drive '
                   'molecule highlighting')
    for fn in ('getMolDrugInteractions', 'buildCannabisHitsByMol'):
        mm = re.search(r'function %s\(.*?\n\}' % fn, src, re.S)
        if mm and re.search(r'\broutes\b', mm.group(0)):
            bad.append('%s references `routes` — the molecule axis must not read the route axis' % fn)

    # -- SAFEGUARD 3/4 (static): no code path may copy one grade into the other
    for pat, msg in (
        (r'\.ev\s*=\s*[^=;]*routes', 'code assigns a record ev from a route entry'),
        (r'routes\[[^\]]*\]\.ev\s*=', 'code assigns a route ev at runtime'),
        (r'\bev\s*:\s*r\.ev\b', 'a route entry is being built from the record ev'),
    ):
        if re.search(pat, src):
            bad.append('grade independence violated — %s' % msg)

    for b in bad:
        fail('routes', b)
    if not bad:
        note('routes: %d entries across %d records — vocabulary valid, no molecule ids, every entry '
             'cited, appliesTo resolves, grades independent, highlighting untouched'
             % (n_entries, len(withr)))


def check_route_wording(data):
    """ROUTE-02: the wording safeguards (7, 8, 9). Text-level, because these are claims prose can
    make that a schema cannot prevent — and each corresponds to an error actually found in review:
    a THC9 tag standing in for smoke (W3, 6 records), `tizanidine` calling vaporised cannabis a
    combustion exposure, and the net-effect inference the theophylline card is built to forestall.
    """
    # Scans DI_DATA *and* DDI_DATA. The drug-drug pairs were initially out of scope, until a
    # coverage check found `cyp1a2_substrate_inhibitor` making its own cannabis/CYP1A2 directional
    # claim -- correct as written, but it sat outside every safeguard. The rules are about what the
    # prose may assert, so they apply wherever cannabis prose lives.
    di = (data.get('DI_DATA') or []) + (data.get('DDI_DATA') or [])
    strip = lambda t: re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'&[a-z]+;|&#x?\w+;', ' ', str(t or ''))))
    bad = []
    for r in di:
        rid = r.get('id')
        t = strip(' '.join(str(r.get(k) or '') for k in ('mech', 'effect', 'monitor')))
        sents = re.split(r'(?<=[.;:])\s+', t)
        for x in sents:
            # SAFEGUARD 7: a cannabinoid credited with INDUCING CYP1A2.
            # Tests the grammatical relationship, not co-occurrence. A first version matched any
            # sentence containing a cannabinoid, "induc" and "1A2" and flagged six records whose
            # text was correct -- e.g. "CBD inhibits it, smoking induces it", where the inducing
            # subject is the smoke. The gap below therefore forbids clause-boundary punctuation, so
            # the cannabinoid must be the actual subject of the induction verb.
            CANNA = r'(?:\u0394?9?-?THC|THC9|CBD|CBN|cannabinoids?|cannabidiol|cannabinol|tetrahydrocannabinol)'
            if re.search(CANNA + r'\b[^.,;:\u2013\u2014-]{0,30}\binduces?\b', x, re.I) \
               and re.search(r'1A2', x) \
               and not re.search(r'\bnot\b|\bdo not\b|\bdoes not\b|rather than|combustion|smoke|smoking', x, re.I):
                bad.append('%s: CYP1A2 induction attributed to a cannabinoid -- %r' % (rid, x[:110]))
            # SAFEGUARD 8: vaporised/oral cannabis described as combustion
            if re.search(r'vapor|vape|edible|oral cannabis', x, re.I) and re.search(r'induc', x, re.I) \
               and re.search(r'combustion|polycyclic|smoke', x, re.I) \
               and not re.search(r'\bnot\b|no longer|rather than|without combust|heats without|nothing is being combusted', x, re.I):
                bad.append('%s: a non-combusted route is tied to combustion induction -- %r' % (rid, x[:110]))
            # SAFEGUARD 9: an ASSERTED net or cancelling effect where two strands oppose.
            # V2 routinely and correctly says the net effect is *unpredictable*; a first version of
            # this guard flagged four such records. Only an affirmative prediction is a defect, so
            # every hedge below exonerates the sentence.
            if re.search(r'\b(cancel(?:s)? out|balance(?:s)? out|offset(?:s)? each other|even(?:s)? out'
                         r'|the net effect is (?:therefore )?(?:zero|neutral|nil|none)'
                         r'|(?:they|the two) cancel)\b', x, re.I) \
               and not re.search(r'\bnot\b|\bno\b|does not|do not|cannot|never|unpredictab|unknown'
                                 r'|rather than|should not|must not|unquantified|unmeasured', x, re.I):
                bad.append('%s: predicts a net or cancelling effect -- %r' % (rid, x[:110]))
    for b in bad:
        fail('route wording', b)
    if not bad:
        note('route wording: %d records + pairs scanned; no cannabinoid is credited with CYP1A2 '
             'induction, no non-combusted route is described as combustion, and nothing predicts a '
             'net or cancelling effect' % len(di))


# ROUTE-03 tripwire. Update ONLY when a route entry is being changed deliberately; the failure
# message prints the new value to paste in. See HANDOFF "Editing route-bearing records".
# MED-01 2026-10-30 (deliberate): the combined 'antipsychotics' record's single CYP1A2 combustion route now sits on
# the clozapine and olanzapine records it described; route values (ev D, cannabis-confounded, PMIDs) unchanged.
# MED-01 Stage 1c 2026-10-30 (deliberate, owner-requested olanzapine review): olanzapine's route basis is now
# 'tobacco-measured' (its cited case followed tobacco reduction only); PMIDs 688731, 15289794, 20233914; ev D unchanged.
ROUTE_FINGERPRINT = '2d40ab6c54c37cfe'  # MED-01 3B: the duloxetine route moved from the SNRI class record to duloxetine (unchanged)


def check_route_fingerprint(data):
    """ROUTE-03: pin every route entry so an ACCIDENTAL change cannot pass silently.

    Why this exists. Since ROUTE-01 the `routes` array sits BEFORE `sev` in a record and carries its
    own `pmid`, `ev` and `source`. A record-level field edit that searches the record text for
    `"pmid"` therefore finds the ROUTE's pmid first. During the antipsychotics correction that edit
    was attempted and would have overwritten the route citation instead of the record citation —
    silently, because the replacement PMIDs were themselves valid, so every other guard would have
    passed. A mistyped backreference happened to fail first; nothing in the harness would have
    caught it.

    Two defences, and this is the second one. The first lives in the editing procedure (mask the
    nested routes span, then assert it is byte-identical afterwards). This guard is the backstop:
    it fingerprints all route entries so any change at all — intended or not — must be
    acknowledged by updating the constant above. Deliberate route edits cost one line; accidental
    ones fail the build.
    """
    di = data.get('DI_DATA') or []
    if not di:
        fail('route fingerprint', 'DI_DATA did not evaluate')
        return
    parts = []
    for r in sorted(di, key=lambda x: str(x.get('id'))):
        for x in (r.get('routes') or []):
            parts.append('%s|%s|%s|%s|%s|%s|%s|%s' % (
                r.get('id'), x.get('route'), x.get('target'), x.get('direction'),
                x.get('ev'), x.get('basis'), x.get('pmid') or '', x.get('source') or '',
            ) + '|' + ','.join(x.get('appliesTo') or []))
    canon = '\n'.join(parts)
    digest = hashlib.sha256(canon.encode('utf-8')).hexdigest()[:16]
    if ROUTE_FINGERPRINT == '__PLACEHOLDER__':
        note('route fingerprint: NOT YET PINNED — set ROUTE_FINGERPRINT to %r' % digest)
        return
    if digest != ROUTE_FINGERPRINT:
        fail('route fingerprint',
             'a route entry changed. If this was DELIBERATE, set ROUTE_FINGERPRINT = %r in '
             'preflight.py. If it was NOT, a record-level edit has very likely written into '
             '`routes` — check routes[].pmid / .ev / .source against HANDOFF before proceeding.'
             % digest)
        return
    note('route fingerprint: %d route entries unchanged (%s)' % (len(parts), digest))


def check_backlog():
    """The backlog is the plan — duplicate IDs make a row unfindable. Two pairs shipped."""
    path = os.path.join(HERE, 'Project_Backlog.xlsx')
    if not os.path.exists(path):
        note('backlog: Project_Backlog.xlsx not present, skipped')
        return
    try:
        from openpyxl import load_workbook
    except ImportError:
        note('backlog: openpyxl unavailable, skipped')
        return
    ws = load_workbook(path)['Backlog']
    hdr = [c.value for c in ws[1]]
    ci = {h: n + 1 for n, h in enumerate(hdr) if h}
    ids, bad_status = [], []
    valid = {'Not Started', 'In Progress', 'Blocked', 'Done', 'Deferred'}
    for r in range(2, ws.max_row + 1):
        rid = ws.cell(r, ci['ID']).value
        if not rid:
            continue
        ids.append(str(rid))
        st = ws.cell(r, ci['Status']).value
        if st not in valid:
            bad_status.append('%s -> %r' % (rid, st))
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        fail('backlog', 'duplicate row IDs: %s' % dupes)
    if bad_status:
        fail('backlog', 'invalid Status value(s): %s' % bad_status[:8])
    note('backlog: %d rows, ids unique, statuses valid' % len(ids))


ALLOWED_SOURCES = {'label'}   # DRUG-26: FDA-approved prescribing information

# DRUG-26: the attribution rule below is enforced for the WHOLE database. Thirteen entries were in
# violation when the guard was written (QA Pass A, 2026-08-30); DRUG-27 closed twelve and DRUG-28 the
# last, so the register is now EMPTY and the rule stands unqualified. Leave it empty. If a future
# entry needs registering, say WHY and close it — the self-cleaning check below fails on any id that
# is listed but no longer violates, which is what stops this becoming a stale allowlist.
#
# This register is self-cleaning: check_evidence_attribution FAILS if an id listed here no longer
# violates, so it cannot rot into a stale allowlist the way index.html's line numbers did. Removing
# the last entry removes the register.
ATTRIBUTION_OPEN = {}   # DRUG-28: emptied — every A/B entry now cites a PMID or names a source.


def check_record_schema(data):
    """DRUG-24: a drug record and a drug-drug pair must each live in their OWN array.

    This guard exists because the check it replaces did not exist, and the gap shipped a real
    defect during this very session. Seven DDI-shaped objects were appended to DI_DATA instead of
    DDI_DATA — the insertion script bracket-searched for the literal "\n  ];" after the last pair,
    but DDI_DATA closes on "\n];" with NO indentation while DI_DATA closes with two spaces, so the
    search ran straight past the end of DDI_DATA and landed on DI_DATA's terminator instead.

    Every existing check passed. check_ddi_reachable counted "284 drugs, 96 pairs" and called it
    clean, because it only asks whether ids resolve — never whether an entry is the right SHAPE.
    Those seven objects would have rendered in the medication list as drugs with no name, no brands,
    no class and no molecules. The count was the only visible symptom, and a count is easy to skim.

    So: assert the shape, not just the references. A DI record must carry the keys the card renderer
    reads; a DDI pair must carry its own. Neither may carry the other's distinctive keys.
    """
    di, ddi = data.get('DI_DATA') or [], data.get('DDI_DATA') or []
    di_required = ('id', 'drug', 'brands', 'cls', 'cat', 'mols', 'sev', 'ev',
                   'mech', 'effect', 'monitor', 'pmid')
    ddi_required = ('id', 'drugs', 'sev', 'ev', 'label', 'mech', 'effect', 'monitor', 'pmid')
    di_only = ('drug', 'brands', 'cls', 'cat', 'mols')
    ddi_only = ('drugs', 'groups', 'label')
    for e in di:
        rid = e.get('id', '?')
        missing = [k for k in di_required if k not in e]
        if missing:
            fail('record schema',
                 'DI_DATA record "%s" is missing %s — is it a drug-drug pair in the wrong array?'
                 % (rid, ', '.join(missing)))
        stray = [k for k in ddi_only if k in e]
        if stray:
            fail('record schema',
                 'DI_DATA record "%s" carries pair-only key(s) %s — it belongs in DDI_DATA'
                 % (rid, ', '.join(stray)))
    for e in ddi:
        rid = e.get('id', '?')
        missing = [k for k in ddi_required if k not in e]
        if missing:
            fail('record schema',
                 'DDI_DATA pair "%s" is missing %s — is it a drug record in the wrong array?'
                 % (rid, ', '.join(missing)))
        stray = [k for k in di_only if k in e]
        if stray:
            fail('record schema',
                 'DDI_DATA pair "%s" carries drug-only key(s) %s — it belongs in DI_DATA'
                 % (rid, ', '.join(stray)))
    # severity and evidence are closed vocabularies; a typo silently mis-sorts and mis-badges
    for label, rows in (('DI_DATA', di), ('DDI_DATA', ddi)):
        for e in rows:
            if e.get('sev') not in ('major', 'moderate', 'minor'):
                fail('record schema', '%s "%s" has sev=%r' % (label, e.get('id'), e.get('sev')))
            if e.get('ev') not in ('A', 'B', 'C', 'D'):
                fail('record schema', '%s "%s" has ev=%r' % (label, e.get('id'), e.get('ev')))
    # DRUG-26: `source` names the AUTHORITY behind a statement when that authority is a
    # regulatory document rather than a paper. It is orthogonal to `ev`, which keeps describing
    # the strength and type of evidence — a label contraindication rests on human data the
    # manufacturer submitted, so ev:'A' with source:'label' is coherent, not a contradiction.
    for label, rows in (('DI_DATA', di), ('DDI_DATA', ddi)):
        for e in rows:
            if 'source' in e and e['source'] not in ALLOWED_SOURCES:
                fail('record schema',
                     '%s "%s" has source=%r — allowed: %s'
                     % (label, e.get('id'), e.get('source'), sorted(ALLOWED_SOURCES)))
    note('record schema: %d drug records and %d pairs each carry the right keys for their array'
         % (len(di), len(ddi)))


def n_ab(data):
    return sum(1 for rows in (data.get('DI_DATA') or [], data.get('DDI_DATA') or [])
               for e in rows if e.get('ev') in ('A', 'B'))



def check_gxwrap_flex(src):
    """IOS-05 (guard 31): .gx-wrap must stay flex:1 0 auto, or long Guided/Demo screens collapse
    on Safari 12.

    `flex:1` is `1 1 0%` — a zero basis free to shrink — on an element inside #gxOverlay, which is
    position:fixed/inset:0 and so has a definite viewport height. Modern engines rescue that with
    the automatic minimum size (min-height:auto); Safari 12.1.2 does not. .gx-wrap was pinned at the
    viewport height and the deficit cascaded: .gx-stage shrank, then every row and card shrank
    (.gx-opt 62.5->35px, .gx-prod 170->30px, text clipped and overlapping), and
    #gxOverlay.scrollHeight collapsed to clientHeight, so the bottom of a long screen could not be
    scrolled to. Validated on a physical iOS 12.4.2 / Safari 12.1.2 iPad; see HANDOFF IOS-05.

    `1 0 auto` is correct on every engine — modern rendering is byte-identical at 375x812, 768x1024,
    1024x768 and 1440x900 — so it is deliberately NOT gated on a capability class, and it must not
    be "simplified" back to flex:1.

    This guard protects behaviour, not prose: a missing IOS-05 comment is reported as a note, never
    a failure.
    """
    css = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)       # the IOS-05 comment says "flex:1" on purpose
    rules = re.findall(r'#gxOverlay\s+\.gx-wrap\s*\{([^{}]*)\}', css)
    if not rules:
        fail('gx-wrap flex sizing',
             'the #gxOverlay .gx-wrap rule is gone or was renamed (zero-match tripwire)')
        return
    if len(rules) > 1:
        fail('gx-wrap flex sizing',
             '#gxOverlay .gx-wrap is declared %d times — the last one wins by document order, so '
             'the IOS-05 value is no longer guaranteed' % len(rules))

    m = re.search(r'(?<![\w-])flex\s*:\s*([^;}]+)', rules[-1])
    if not m:
        fail('gx-wrap flex sizing',
             '#gxOverlay .gx-wrap no longer sets `flex` — it falls back to 0 1 auto and Safari 12 '
             'will shrink long Guided/Demo screens again (IOS-05)')
    elif ' '.join(m.group(1).split()) != '1 0 auto':
        fail('gx-wrap flex sizing',
             '#gxOverlay .gx-wrap is `flex:%s`, not `flex:1 0 auto` — this reintroduces the Safari '
             '12 collapse fixed by IOS-05 (.gx-opt 62.5->35px, .gx-prod 170->30px, overlay '
             'scrollHeight pinned to clientHeight)' % ' '.join(m.group(1).split()))

    # any OTHER rule touching .gx-wrap's flex sizing would override the shorthand
    for sel, decls in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
        sel = ' '.join(sel.split())
        if '.gx-wrap' not in sel or sel == '#gxOverlay .gx-wrap':
            continue
        bad = re.search(r'(?<![\w-])(flex|flex-basis|flex-shrink|flex-grow)\s*:', decls)
        if bad:
            fail('gx-wrap flex sizing',
                 'rule "%s" also sets %s on .gx-wrap — it can override the IOS-05 shorthand'
                 % (sel[:70], bad.group(1)))

    documented = '/* IOS-05:' in src
    note('gx-wrap flex sizing: #gxOverlay .gx-wrap is flex:1 0 auto, declared once, not '
         'overridden%s' % ('' if documented else
                           ' — NOTE: the IOS-05 comment is gone; HANDOFF IOS-05 has the rationale'))



def check_demo_ribbon_offset(src):
    """IOS-07 (guard 32): the Demo banner's phone-only offset, and the overlay reservation that
    must move with it.

    The banner carries a compliance statement — "DEMO — NOT A LICENSED STORE" — and position:fixed
    is how the author said it must stay on screen regardless of scroll. On a physical iPhone 16 only
    a sliver showed, because a fixed element is laid out against iOS's large viewport while the
    visible bottom edge sits above Safari's bottom bar, and env(safe-area-inset-bottom) is inert
    here (no viewport-fit=cover, so every env() inset resolves to 0).

    IOS-06 solved this with @supports + calc(100lvh - 100dvh) and was reverted after a cross-device
    Entourage report whose mechanism was never proven. IOS-07 therefore uses NO viewport units, NO
    @supports and NO viewport-fit — a static offset inside media queries already present in V2 — and
    this guard keeps it that way.

    The two values are COUPLED: 164 = 112 + the banner's ~35.5px height + a 16px margin, the same
    arithmetic the author used for 116 = 64 + 35.5 + 16. Raising the banner without raising the
    overlay's reservation would let it cover demo content, which is an explicit acceptance failure.
    Scope is by NUMBER, not feature detection: the legacy iPad is 768px portrait and 1024x768
    landscape, so it matches neither query and Safari 12 has nothing here to evaluate.
    """
    css = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)   # the IOS-07 comment names these on purpose

    m = re.search(r'@media\s*\(\s*max-width:\s*620px\s*\)\s*,\s*\(\s*orientation:\s*landscape\s*\)'
                  r'\s*and\s*\(\s*max-height:\s*500px\s*\)\s*\{(.*?)\}\s*\}', css, re.S)
    if not m:
        fail('demo ribbon offset',
             'the IOS-07 phone-scoped @media block is gone — the Demo banner will show only a sliver '
             "behind iOS Safari's bottom bar again")
        return
    inner = m.group(1)

    rb = re.search(r'\.demo-ribbon\s*\{[^{}]*bottom\s*:\s*(\d+)px', inner)
    ov = re.search(r'#gxOverlay\s*\{[^{}]*padding-bottom\s*:\s*(\d+)px', inner)
    if not rb or not ov:
        fail('demo ribbon offset',
             'the IOS-07 block no longer carries BOTH the .demo-ribbon bottom and the #gxOverlay '
             'padding-bottom — these two values are coupled and must move together')
        return
    banner, reserve = int(rb.group(1)), int(ov.group(1))
    if reserve <= banner + 35:
        fail('demo ribbon offset',
             'IOS-07 reserves only %dpx for a banner sitting %dpx up — the banner is ~35.5px tall, so '
             'it would cover demo content; the reservation must exceed bottom + height'
             % (reserve, banner))

    # the base rules must survive OUTSIDE the block, for the iPad and every non-matching viewport
    outside = css[:m.start()] + css[m.end():]
    if not re.search(r'body\.is-mobile-view\s+\.demo-ribbon\s*\{[^{}]*bottom\s*:', outside):
        fail('demo ribbon offset',
             'the base body.is-mobile-view .demo-ribbon bottom rule is gone — the protected legacy '
             'iPad would lose the banner offset entirely')
    if not re.search(r'body\.demo-mode\.is-mobile-view\s+#gxOverlay\s*\{[^{}]*padding-bottom\s*:', outside):
        fail('demo ribbon offset',
             'the base body.demo-mode.is-mobile-view #gxOverlay reservation is gone — the protected '
             'legacy iPad would lose its demo overlay padding')

    # IOS-07 is deliberately free of the mechanisms IOS-06 was reverted for
    live = re.sub(r'/\*.*?\*/', ' ', ''.join(re.findall(r'<style[^>]*>(.*?)</style>', src, re.S)), flags=re.S)
    if re.search(r'\d(?:dvh|svh|lvh)\b', live):
        fail('demo ribbon offset',
             'a dynamic viewport unit (dvh/svh/lvh) appeared in live CSS — IOS-06 was reverted for '
             'exactly this and IOS-07 is deliberately static')
    meta = re.search(r'<meta\s+name=["\']viewport["\'][^>]*>', src, re.I)
    if meta and 'viewport-fit' in meta.group(0):
        fail('demo ribbon offset',
             'viewport-fit=cover was added to the viewport meta — that changes the layout model for '
             'the whole app on every platform including the protected iPad')

    note('demo ribbon offset: IOS-07 lifts .demo-ribbon to %dpx with a matching %dpx overlay '
         'reservation, phone-scoped; base rules intact for the legacy iPad, no viewport units, no '
         'viewport-fit' % (banner, reserve))



def check_reset_overlay_display(src):
    """RESET-01 + RESET-02 (guard 33): resetToEntryGate() closes two KINDS of overlay, and they
    need opposite treatment. Getting either backwards has already shipped a defect.

    Class-driven overlays — #ent-ov, #fullDiscOv, the two sign-in dialogs and the Reference Card —
    open by adding .open against a stylesheet that hides them by default. Writing an inline
    display:'none' onto those beats the stylesheet permanently, so .open stops working and they can
    never open again until the page is refreshed (RESET-01: Entourage, the Full Disclaimer and the
    staff sign-in all died after any session reset, and because showEntourage() locks body scroll
    before it renders, clicking the dead control froze the page too).

    #rdInfoOv is the opposite case. ResearchData.openInfo() builds it at RUNTIME with an inline
    display:flex and z-index 100000, and there is NO stylesheet rule for it. CLEARING its inline
    display does not hide it, it reveals it — a bare div falls back to display:block, above the
    gate's z-index 10500 (RESET-02: a panel the user had already closed reappeared over the
    disclaimer gate after every reset). It must be hidden explicitly.

    Also asserted: the loop names the real Reference Card id. 'refCardOv' never existed, so nothing
    closed the card on reset and a genuine inactivity expiry left the previous customer's
    Scientific References card over the Full Table for the next one.
    """
    js = ''.join(re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', src, re.S))
    # strip comments first: the RESET-02 note deliberately names 'refCardOv' and 'rdInfoOv' while
    # explaining them, and the guard must read the CODE, not its own documentation
    js = re.sub(r'/\*.*?\*/', ' ', re.sub(r'(?m)^\s*//.*$', '', js), flags=re.S)

    m0 = re.search(r'function\s+resetToEntryGate\s*\(', js)   # exact name, not a prefix
    if not m0:
        fail('reset overlay display', 'resetToEntryGate() is gone (zero-match tripwire)')
        return
    body = js[m0.start():m0.start() + 12000]

    loop = re.search(r"\[\s*'fullDiscOv'.*?\]\s*\.forEach\s*\(function\s*\(id\)\s*\{(.*?)\}\s*\)\s*;",
                     body, re.S)
    if not loop:
        fail('reset overlay display',
             'the class-driven overlay-closing loop in resetToEntryGate() is gone — full-screen '
             'surfaces could occlude the entry gate again')
        return
    arr = body[loop.start():loop.start() + loop.group(0).index(']') + 1]

    # 1. the class-driven overlays must not be poisoned with a persistent inline display
    for m in re.finditer(r"style\.display\s*=\s*(['\"])(.*?)\1", loop.group(1)):
        if m.group(2).strip():
            fail('reset overlay display',
                 "resetToEntryGate() writes an inline display:'%s' onto its CLASS-DRIVEN overlays "
                 "again — that beats the stylesheet and leaves Entourage, the Full Disclaimer, the "
                 "sign-in dialogs and the Reference Card permanently dead after any reset (RESET-01)"
                 % m.group(2))

    # 2. the Reference Card must be referenced by its real id
    if 'refCardOv' in body:
        fail('reset overlay display',
             "resetToEntryGate() references 'refCardOv', which does not exist — the Reference Card "
             "is 'rcOv', and naming it wrongly means a session reset never closes it (RESET-02)")
    if "'rcOv'" not in arr:
        fail('reset overlay display',
             "the Reference Card ('rcOv') is not in the class-driven reset list — an inactivity "
             "expiry would leave the previous session's reference article over the Full Table")

    # 3. rdInfoOv must NOT be cleared with the class-driven group, and MUST be hidden explicitly
    if 'rdInfoOv' in arr:
        fail('reset overlay display',
             "'rdInfoOv' is back in the class-driven list — it is built in JS with an inline "
             "display:flex and has no stylesheet rule, so clearing its inline display REVEALS it "
             "over the entry gate instead of hiding it (RESET-02)")
    if not re.search(r"rdInfoOv[\s\S]{0,240}?style\.display\s*=\s*'none'", body):
        fail('reset overlay display',
             "resetToEntryGate() no longer hides #rdInfoOv explicitly — the Research Data & Privacy "
             "panel has no stylesheet display:none to fall back to, so it would reappear over the "
             "disclaimer gate (z-index 100000 against the gate's 10500) after a reset")

    # 4. the fix assumes the class-driven overlays stay class-driven
    css = re.sub(r'/\*.*?\*/', ' ', ''.join(re.findall(r'<style[^>]*>(.*?)</style>', src, re.S)), flags=re.S)
    for sel in ('#ent-ov', '#fullDiscOv'):
        if not re.search(re.escape(sel) + r'\.open\s*\{[^{}]*display\s*:\s*flex', css):
            fail('reset overlay display',
                 '%s.open no longer sets display:flex — RESET-01 assumed these overlays are opened '
                 'by class alone' % sel)
        if not re.search(r'(?<![\w.#-])' + re.escape(sel) + r'\s*\{[^{}]*display\s*:\s*none', css):
            fail('reset overlay display',
                 '%s no longer defaults to display:none — clearing the inline display would now '
                 'leave it visible after a reset' % sel)
    if not re.search(r'\.rc-ov\s*\{[^{}]*display\s*:\s*none', css):
        fail('reset overlay display',
             '.rc-ov no longer defaults to display:none — clearing its inline display would leave '
             'the Reference Card visible after a reset')

    note('reset overlay display: resetToEntryGate() clears the inline display on 5 class-driven '
         'overlays including the real rcOv, and hides the JS-built rdInfoOv explicitly; #ent-ov, '
         '#fullDiscOv and .rc-ov all still default to display:none')

def check_evidence_attribution(data):
    """DRUG-26: an A- or B-graded entry ASSERTS human evidence, so it must say where that comes from.

    QA Pass A (2026-08-30) found 36 entries graded A or B carrying no citation at all — 30 of the 90
    A/B pairs. Nothing caught it: check_pmids verifies that PMIDs which are PRESENT resolve, and never
    asks whether a claim of human evidence has any attribution behind it. The entries rendered an
    empty citation slot, which reads as "unsupported" for exactly the records with the strongest
    regulatory backing.

    The rule is deliberately narrow. Grades C (preclinical) and D (theoretical) are exempt: a record
    that says "no human data exists" is honest without a citation, and 160 of them legitimately have
    none. Only A and B — the grades that claim humans were studied — must point somewhere, either at
    a PMID or at a named authority such as the FDA label.
    """
    offenders = []
    for label, rows in (('DI_DATA', data.get('DI_DATA') or []),
                        ('DDI_DATA', data.get('DDI_DATA') or [])):
        for e in rows:
            if e.get('ev') not in ('A', 'B'):
                continue
            has_pmid = bool(re.search(r'\d{5,8}', str(e.get('pmid') or '')))
            if has_pmid or e.get('source') in ALLOWED_SOURCES:
                continue
            offenders.append((e.get('id'), '%s "%s" (%s/%s)'
                             % (label, e.get('id'), e.get('sev'), e.get('ev'))))
    new_offenders = [o for o in offenders if o[0] not in ATTRIBUTION_OPEN]
    for _, desc in new_offenders:
        fail('evidence attribution',
             '%s is graded A/B — which asserts human evidence — but cites no PMID and names no '
             'source' % desc)
    # a register entry that no longer violates must be REMOVED, or the register goes stale
    still = {o[0] for o in offenders}
    fixed = sorted(set(ATTRIBUTION_OPEN) - still)
    for rid in fixed:
        fail('evidence attribution',
             '"%s" is listed in ATTRIBUTION_OPEN but now has attribution — delete its line' % rid)
    if not new_offenders and not fixed and still:
        note('evidence attribution: %d A/B entries attributed, except %d registered as open'
             % (n_ab(data), len(still)))
    if not offenders:
        note('evidence attribution: all %d A/B-graded entries cite a PMID or name a source'
             % n_ab(data))


def check_hasrisk_invariant(data):
    """DRUG-24: hasRisk is editorial intent; ADVERSE_FINDINGS is what the reader actually sees.

    hasRisk is written on conditions but read by NO code — the caution a user sees comes from
    condHasAdverse(), which matches an adverse finding by condition label or by group. That makes
    hasRisk a comment that looks like data, and it had already drifted once: an earlier note in
    index.html claimed hasRisk was what flagged a condition, which was wrong.

    Deleting it would throw away a real editorial signal, so it is enforced instead. The rule is
    one-directional and deliberately so: if a curator marks a condition risky, the reader MUST see
    a caution on it. The converse is not required — condHasAdverse legitimately matches by group
    and covers more conditions than hasRisk marks.
    """
    conds = [c for c in data['CONDITIONS'] if c.get('id') != 'cancer-adverse']
    adverse = data.get('ADVERSE_FINDINGS') or {}

    def shown(cond):
        if cond.get('isRisk'):
            return True                       # the global filter row is the adverse entry point
        groups = group_keys(cond.get('group'))
        for mol in adverse:
            for f in adverse[mol]:
                if f.get('footnote'):
                    continue
                if f.get('condition') == cond.get('label') or f.get('group') in groups:
                    return True
        return False

    flagged = [c for c in conds if c.get('hasRisk')]
    silent = [c.get('id') for c in flagged if not shown(c)]
    for cid in silent:
        fail('hasRisk invariant',
             'condition "%s" is marked hasRisk:true but no adverse finding matches it by label or '
             'group — the curator flagged a risk the reader never sees' % cid)
    if not silent:
        note('hasRisk invariant: all %d flagged conditions surface a caution to the reader'
             % len(flagged))



def check_folded_aliases(src, data):
    """UX-126: folded aliases must stay VERBATIM inside the parent label, or search breaks silently.

    Some COND_ALIASES entries are synonyms carrying their parent's own name ("Cannabis Dependence"
    under "Cannabis Use Disorder"). They used to render as their own "alias -> parent" rows, sorted
    straight next to the parent, and read as duplicated entries. They now carry fold:true and are
    shown inside the parent's row text instead.

    The failure this guard exists to prevent has not shipped, and that is the point: it is one
    tidy-up away. "Cannabis Use Disorder (CUD - Cannabis Dependence - Cannabis Withdrawal)" looks
    redundant, and shortening it to "(CUD - Dependence - Withdrawal)" reads better and SILENTLY
    BREAKS a search for "cannabis dependence", because the Guided Match condition search matches
    the typed string CONTIGUOUSLY against visible row text. Measured before shipping: the shortened
    form lost 7 search terms, the verbatim form lost none.

    Enforces three things:
      1. every fold:true alias appears VERBATIM in its parent's rendered label;
      2. CONDITIONS[].label is never itself folded (folding is display-only — the bare label is the
         join key for condHasAdverse, the PubMed query map, the FAQ list and the V2FACTS counts);
      3. a zero-match tripwire — if no fold:true alias is found at all, the feature has been
         removed or the parser has drifted, and this FAILS rather than reporting success.
    """
    aliases = re.findall(r"\{alias:'([^']+)',\s*target:'([^']+)'(,\s*fold:true)?\}",
                         src[src.find('var COND_ALIASES'):src.find('];', src.find('var COND_ALIASES'))])
    folded = [(a, t) for a, t, f in aliases if f]
    if not folded:
        fail('folded aliases',
             'no fold:true alias found — UX-126 folding was removed, or this parser no longer '
             'matches the COND_ALIASES format (zero-match tripwire)')
        return

    labels = {c.get('id'): c.get('label') for c in data['CONDITIONS']}
    bad = []
    for alias, target in folded:
        parent = labels.get(target)
        if parent is None:
            fail('folded aliases', 'alias "%s" folds into unknown condition id "%s"' % (alias, target))
            continue
        if '(' in parent and alias in parent:
            continue                          # already inside the condition's own published label
        rendered = parent + ' (' + ' \u00b7 '.join(
            a for a, t in folded if t == target) + ')'
        if alias not in rendered:
            bad.append((alias, rendered))
    for alias, rendered in bad:
        fail('folded aliases',
             'alias "%s" is NOT verbatim in the rendered parent label "%s" — a Guided Match search '
             'for it will return nothing' % (alias, rendered))

    shortened = [a for a, t in folded
                 if labels.get(t) and a.split()[0].lower() == labels[t].split()[0].lower()
                 and a not in src]
    for a in shortened:
        fail('folded aliases', 'folded alias "%s" no longer appears in the source verbatim' % a)

    if not bad and not shortened:
        note('folded aliases: %d synonym aliases fold verbatim into %d parent labels; no condition '
             'label mutated' % (len(folded), len(set(t for _, t in folded))))



def check_cross_listed(src, data):
    """UX-127: the cross-listing sentence must be DERIVED, not just agree with its own copy.

    It read "two conditions are cross-listed" in BOTH the How-to-Use overlay and the main FAQ while
    the data held three: testicular-cancer had been dual-grouped into Men's Health and the sentence
    was never updated. check_faq_parity could not catch it — that guard compares the two copies to
    EACH OTHER, and two identical wrong sentences agree perfectly.

    It hid for the same reason AUDIT-11 did: the COUNT beside it is derived and was correct, so the
    arithmetic still balanced (the enumeration listed 68 names of which 65 were distinct, and
    68 - 3 = 65). Nothing looked wrong.

    Asserts that every crossListedNote fallback names the right NUMBER of cross-listed conditions
    and names every one of them, both derived from CONDITIONS. Tripwires on the data side and the
    markup side: if either the dual-grouped conditions or the spans vanish, this FAILS rather than
    quietly reporting success.
    """
    conds = [c for c in data['CONDITIONS'] if c.get('id') != 'cancer-adverse']
    dual = [c for c in conds
            if isinstance(c.get('group'), (list, tuple)) and len(c['group']) > 1]
    if not dual:
        fail('cross-listed',
             'no dual-grouped condition found in CONDITIONS — cross-listing was removed, or this '
             'parser no longer reads group arrays (zero-match tripwire)')
        return

    words = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
    want = words[len(dual)] if len(dual) < len(words) else str(len(dual))

    spans = re.findall(r'<span data-v2fact="crossListedNote"[^>]*>([^<]*)</span>', src)
    if not spans:
        fail('cross-listed',
             'no data-v2fact="crossListedNote" span found — the sentence has been hand-written '
             'again (zero-match tripwire)')
        return

    import html as _html
    bad = False
    for i, lit in enumerate(spans, 1):
        text = _html.unescape(lit)
        if not text.strip().lower().startswith(want):
            bad = True
            fail('cross-listed',
                 'crossListedNote fallback #%d starts "%s..." but the data has %d cross-listed '
                 'conditions, so it must start "%s"'
                 % (i, text.strip()[:30], len(dual), want))
        for c in dual:
            label = _html.unescape(c.get('label') or '')
            if label not in text:
                bad = True
                fail('cross-listed',
                     'crossListedNote fallback #%d never names cross-listed condition "%s"'
                     % (i, label))
    if not bad:
        note('cross-listed: %d dual-grouped conditions (%s) named in all %d crossListedNote '
             'fallback(s)' % (len(dual), ', '.join(c['id'] for c in dual), len(spans)))



def check_legacy_safari(src):
    """IOS-01: the build must parse and lay out on an older iPad. jsc cannot tell us this.

    Reported from an iPad: the entry gate appeared but could not be scrolled or dismissed, and
    Guided Match, Health Conditions, Entourage Effect, Drug Interactions and every molecule tile
    were inert — while the page still rendered and the <details> FAQ rows still opened, because
    those need no JavaScript. Two independent causes, both invisible to every existing guard:

      1. ONE optional-chaining token (`?.`). It is ES2020, a SYNTAX error in Safari before 13.4,
         and a syntax error does not fail one line — it kills the whole <script> block at parse
         time. That block held 1.81 MB of the build's 1.85 MB of JS. check_scripts_parse runs the
         Mac's current JavaScriptCore, which accepts ES2020 happily, so it passed throughout.
      2. `inset:0` on position:fixed overlays. `inset` is ignored before Safari 14.5, and a fixed
         element with no offsets shrink-wraps instead of covering the viewport, so #entryGate had
         no constrained height for its own overflow-y:auto to scroll against.

    Floor enforced here is Safari 10.3 / iOS 10.3, which the rest of the build already meets.
    Strings and comments are blanked before scanning so prose about these features cannot trip it.
    """
    blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>', src)
    if not blocks:
        fail('legacy safari', 'no inline <script> blocks found (zero-match tripwire)')
        return
    js = '\n'.join(blocks)

    def blank(code):
        out = []; i = 0; n = len(code)
        while i < n:
            c = code[i]
            if c == '/' and i + 1 < n and code[i + 1] == '/':
                j = code.find('\n', i); j = n if j < 0 else j
                out.append(' ' * (j - i)); i = j
            elif c == '/' and i + 1 < n and code[i + 1] == '*':
                j = code.find('*/', i + 2); j = n if j < 0 else j + 2
                out.append(' ' * (j - i)); i = j
            elif c in '"\'`':
                q = c; j = i + 1
                while j < n:
                    if code[j] == '\\':
                        j += 2; continue
                    if code[j] == q:
                        j += 1; break
                    if q != '`' and code[j] == '\n':
                        break
                    j += 1
                out.append(' ' * (j - i)); i = j
            else:
                out.append(c); i += 1
        return ''.join(out)

    code = blank(js)
    # Syntax-level only. These do not throw at runtime — they stop the whole block from parsing.
    SYNTAX = [
        ('optional chaining `?.`',        'Safari 13.4', r'\?\.'),
        ('nullish coalescing `??`',       'Safari 13.4', r'\?\?'),
        ('logical assignment `||= &&= ??=`', 'Safari 14', r'(\|\|=|&&=|\?\?=)'),
        ('regex lookbehind `(?<=` `(?<!`', 'Safari 16.4', r'\(\?<[=!]'),
        ('class private `#field`',        'Safari 14.5', r'(?m)^\s*#[A-Za-z_]'),
        ('class `static {}` block',       'Safari 16.4', r'\bstatic\s*\{'),
        ('numeric separator `1_000`',     'Safari 13',   r'\b\d[\d_]*_\d'),
        ('BigInt literal `1n`',           'Safari 14',   r'\b\d+n\b'),
    ]
    found = False
    for name, ver, pat in SYNTAX:
        for m in re.finditer(pat, code):
            found = True
            line = js[:m.start()].count('\n') + 1
            fail('legacy safari',
                 '%s needs %s and is a PARSE error below it — one occurrence kills the entire '
                 '<script> block. Near JS line %d: ...%s...'
                 % (name, ver, line, ' '.join(js[max(0, m.start() - 48):m.start() + 24].split())))

    # `inset` without longhand offsets: silently ignored before Safari 14.5
    bare = len(re.findall(r'position\s*:\s*fixed\s*;\s*inset\s*:', src))
    if bare:
        found = True
        fail('legacy safari',
             '%d `position:fixed;inset:` declaration(s) carry no top/right/bottom/left fallback — '
             '`inset` is ignored before Safari 14.5 and the overlay will not cover the viewport'
             % bare)

    # IOS-02: the .gx-opt fallback looks redundant on a modern browser — that is precisely the
    # point, and precisely why a tidy-up would delete it. <button> cannot be a flex container
    # before Safari 14, and without these the Guided Match option rows stack (measured 117px tall
    # against the intended 67px, tick dropping to the left).
    for pat, name in (
            (r'\.gx-opt \.ic\{[^}]*display:inline-block', '.gx-opt .ic{display:inline-block}'),
            (r'\.gx-opt \.lab\{[^}]*display:inline-flex', '.gx-opt .lab{display:inline-flex}'),
            (r'\.gx-opt \.chk\{[^}]*float:right', '.gx-opt .chk{float:right}'),
            # IOS-03: .gx-cta was the ONLY full-width Guided Match control relying on flex
            # stretch instead of declaring a width, and Safari <14 does not stretch <button>
            # flex items — the three result-screen buttons came out 303/157/94px, not 460.
            (r'#gxOverlay \.gx-cta\{[^}]*width:100%', '#gxOverlay .gx-cta{width:100%}')):
        if not re.search(pat, src):
            found = True
            fail('legacy safari',
                 'the legacy fallback "%s" is missing — on Safari before 14 the Guided Match rows '
                 'lose their layout: <button> cannot be a flex container (option rows stack) and '
                 '<button> flex items do not stretch (the result-screen CTAs shrink to their text)'
                 % name)

    guarded = len(re.findall(
        r'position\s*:\s*fixed\s*;\s*top:0;right:0;bottom:0;left:0;\s*inset\s*:\s*0', src))
    if not guarded:
        found = True
        fail('legacy safari',
             'no position:fixed overlay carries the top/right/bottom/left + inset:0 pair — the '
             'IOS-01 fallback has been removed or reformatted (zero-match tripwire)')

    if not found:
        note('legacy safari: no ES2020+ syntax in %d KB of inline JS; %d fixed overlay(s) carry '
             'longhand offsets beside inset:0; .gx-opt flex fallback intact '
             '(floor: Safari 10.3 / iOS 10.3)' % (len(js) // 1024, guarded))



def check_tablet_default_view(src):
    """IOS-03: a tablet-sized mobile screen must OPEN on the fitted table, not the phone list.

    MOBILE_BREAKPOINT is 900, so an iPad in portrait (768) was treated as a phone and started on
    the molecule list. The fitted Table view already existed (MOB-14 scales the grid as one unit,
    transform-origin 0 0, overflow clipped) and measured correctly — 0.661 scale, 744px inside a
    763px screen — but it sat behind a toggle, and native pinch cannot zoom out past the layout
    width, so the whole table was effectively unreachable without hunting for the control.

    Three things are asserted, because removing any one silently restores the old behaviour:
      1. the tablet test keys off the device's SHORTER side, not the current width — otherwise a
         phone in landscape (844 wide) is mistaken for a tablet;
      2. the default never overrides a reader who picked a view (`userPickedView`);
      3. rotation across the breakpoint clears the scaled-grid state on the way out and re-applies
         it on the way back, or portrait returns showing the table at actual size until a reload.
    """
    checks = [
        (r'TABLET_MIN_SIDE\s*=\s*700',
         'the TABLET_MIN_SIDE = 700 threshold'),
        (r'Math\.min\(window\.innerWidth,\s*window\.innerHeight\)\s*>=\s*TABLET_MIN_SIDE',
         'the shorter-side test (a phone in landscape must not read as a tablet)'),
        (r'function applyDefaultView\(\)',
         'applyDefaultView()'),
        (r'userPickedView\s*=\s*true',
         "the reader's own view choice being recorded"),
        (r'applyDefaultView:\s*applyDefaultView',
         'applyDefaultView exposed on __v3MobileList for the rotation path'),
        (r'if \(!nowMobile\) \{ if \(window\.V2TableView\) window\.V2TableView\.clear\(\); \}',
         'the leaving-mobile cleanup in refresh()'),
    ]
    missing = [name for pat, name in checks if not re.search(pat, src)]
    for name in missing:
        fail('tablet default', 'IOS-03: %s is missing — a tablet in portrait will fall back to the '
                               'phone list, or rotation will leave stale scaled-grid state' % name)
    if not missing:
        note('tablet default: portrait tablets open on the fitted table; shorter-side test, '
             'reader override and rotation cleanup all present')



def check_legacy_fallback_scoping(src):
    """IOS-04: a legacy fallback that loses its scope is a visible bug for EVERYONE.

    The IOS-02/IOS-03 fallbacks were inert by construction — `float`, `vertical-align` and
    `width:100%` are ignored by flex layout, so a modern browser could not be affected. The IOS-04
    gap fallbacks are NOT like that: `gap` and `margin` both apply where both are supported, so an
    unscoped rule double-spaces every modern browser. Measured: .info moves from x=74 to x=87.

    Safety therefore rests entirely on two things, and this guard asserts both:
      1. every rule inside the IOS-04 block is scoped to body.no-flex-gap or body.no-button-flex;
      2. those classes are set only by runtime MEASUREMENT — the two feature probes are present,
         and nothing in the build sets them from a version or user-agent check.
    """
    # start AFTER the opening comment closes, or its own prose is parsed as selectors
    m = re.search(r'/\* ══ IOS-04: LEGACY-SAFARI FALLBACKS.*?\*/(.*?)/\* ══ END IOS-04', src, re.S)
    if not m:
        fail('legacy fallback scoping',
             'the IOS-04 fallback block is gone or its markers were renamed (zero-match tripwire)')
        return
    block = re.sub(r'/\*.*?\*/', ' ', m.group(1), flags=re.S)

    rules = re.findall(r'([^{}]+)\{[^}]*\}', block)
    if not rules:
        fail('legacy fallback scoping', 'the IOS-04 block contains no rules (zero-match tripwire)')
        return
    unscoped = []
    for sel in rules:
        for part in sel.split(','):
            part = part.strip()
            if not part:
                continue
            if not re.match(r'body\.(no-flex-gap|no-button-flex)\b', part):
                unscoped.append(part)
    for part in unscoped:
        fail('legacy fallback scoping',
             'IOS-04 selector "%s" is NOT scoped to body.no-flex-gap / body.no-button-flex — it '
             'would double-space every modern browser' % part[:90])

    probes = [
        (r'function flexGapOK\(\)', 'the flex-gap probe'),
        (r'function buttonFlexOK\(\)', 'the <button>-flex probe'),
        (r"classList\.toggle\('no-flex-gap'", 'the no-flex-gap class toggle'),
        (r"classList\.toggle\('no-button-flex'", 'the no-button-flex class toggle'),
    ]
    missing = [name for pat, name in probes if not re.search(pat, src)]
    for name in missing:
        fail('legacy fallback scoping',
             'IOS-04: %s is missing — the fallback classes would never be set, or would be set by '
             'something other than measurement' % name)

    # the fallbacks must never be driven by a version or UA check
    for bad in re.finditer(r"(userAgent|appVersion|vendor)\s*[.\[]?\s*(match|indexOf|test|includes)", src):
        ctx = src[max(0, bad.start() - 220):bad.start() + 80]
        if 'no-flex-gap' in ctx or 'no-button-flex' in ctx:
            fail('legacy fallback scoping',
                 'IOS-04 classes appear to be set from a user-agent check — they must come from '
                 'the feature probes only')

    if not unscoped and not missing:
        note('legacy fallback scoping: %d IOS-04 rule(s), all scoped to a measured capability '
             'class; both feature probes present' % len(rules))


# ── driver ─────────────────────────────────────────────────────────────────────
ROUTE04_ENGINE_TEST = r"""
var st={};
var ceilings=['any','min','low','none'];var INTOX=['THC9','THC8','THCP'];
var SETS=[[]].concat(ROUTE_KEYS.map(function(k){return [k];})).concat([
  ['oral','sublingual'],['inhalation_vapor','inhalation_smoked'],['oral','topical'],ROUTE_KEYS.slice()]);
var conds=CONDITIONS.map(function(c){return c.id;}).concat(Object.keys(G.guidedMol||{}));
var r={scenarios:0,sets:SETS.length,sciDiff:0,wrong:0,fallback:0,anyRouteExcl:0,anyDiff:0,thcLeak:0,both:0,fitDiff:0,pools:0,
       unionBad:0,toggle:0};
SETS.forEach(function(sel){var u=routeTypesFor(sel),want={};sel.forEach(function(k){ROUTE_MAP[k].forEach(function(t){want[t]=1;});});
  if(u.length!==Object.keys(want).length||u.some(function(t){return !want[t];}))r.unionBad++;});
conds.forEach(function(c){ceilings.forEach(function(ce){
  var base=JSON.stringify(G.recForConditions([c],ce,[]).molecules);
  var noRoute=G.recForConditions([c],ce,[]);var plain=noRoute.molecules.length?G.match(noRoute):null;
  var fit={};if(plain)plain.shown.forEach(function(s){fit[s.sku]=s.fit;});
  SETS.forEach(function(sel){
    var rec=G.recForConditions([c],ce,[]);if(JSON.stringify(rec.molecules)!==base)r.sciDiff++;
    if(!rec.molecules.length)return;r.scenarios++;
    var allowed=routeTypesFor(sel);rec.routeTypes=allowed;var res=G.match(rec);
    if(res.routeFallback)r.fallback++;
    res.shown.forEach(function(s){
      var off=allowed.length&&allowed.indexOf(s.type)<0;if(off)r.wrong++;
      var intox=INTOX.some(function(k){return (s.profile[k]||0)>0;});
      if(ce==='none'&&intox)r.thcLeak++;if(ce==='none'&&(off||intox))r.both++;
      if(fit[s.sku]!==s.fit)r.fitDiff++;});
    if(!allowed.length){
      r.anyRouteExcl+=res.excluded.filter(function(e){return e.reason==='off your preferred route';}).length;
      if(JSON.stringify(res.shown)!==JSON.stringify(plain.shown))r.anyDiff++;}
    else{var want2=plain.shown.filter(function(s){return allowed.indexOf(s.type)>=0;}).map(function(s){return s.sku;}).join(',');
      if(res.shown.map(function(s){return s.sku;}).join(',')!==want2)r.pools++;}
  });});});
// the Any rule, on the app's own toggleRoute(): mutually exclusive, last removal returns to Any
function t(fn,ok){fn();if(!ok())r.toggle++;}
st={};t(function(){},function(){return selRoutes().length===0;});
t(function(){toggleRoute('oral');},function(){return selRoutes().join()==='oral';});
t(function(){toggleRoute('sublingual');},function(){return selRoutes().join()==='oral,sublingual';});
t(function(){toggleRoute('any');},function(){return selRoutes().length===0&&routeTypesFor(selRoutes()).length===0;});
t(function(){toggleRoute('topical');toggleRoute('topical');},function(){return selRoutes().length===0;});
t(function(){toggleRoute('nonsense');},function(){return selRoutes().length===0;});
t(function(){toggleRoute('transdermal');toggleRoute('oral');},function(){return selRoutes().join()==='oral,transdermal';});
print(JSON.stringify(r));
"""


def check_route_hard_filter(src):
    """ROUTE-04 (guard 34): route of administration is a PRODUCT-ELIGIBILITY input, never a
    scientific one, and the customer's route choice is a HARD filter. Owner decisions 2026-10-12.

    Before ROUTE-04, match() fell back to every route whenever nothing on the chosen route
    survived ("never zero-out a kiosk result", ONB-11). Measured against the accepted build, that
    put wrong-route products in the displayed top six in 561 of 1,883 scenarios. The owner chose
    honest zero results, a standalone route screen in Standard Guided Match, the express path and
    the Complete Research Demonstration, and then MULTI-SELECT: several routes may be chosen, a
    product is eligible if it belongs to at least one of them (OR), and every unselected route stays
    excluded. 🌀 Any route is mutually exclusive with the specific routes and is st.routes = [].

    BEHAVIOURAL: runs the app's own matcher, CONDITIONS, M, ROUTE_MAP, ROUTE_KEYS, toggleRoute()
    and routeTypesFor() in JavaScriptCore — every condition × every THC setting × Any, each single
    route and the combinations Oral+Sublingual, Vaporised+Smoked, Oral+Topical and all six — and
    asserts:
      - route never changes the molecule set recForConditions() returns (science is upstream);
      - no shown product falls outside the union of the selected routes; routeFallback never true;
      - Any route excludes nothing for route reasons and returns exactly the unfiltered list;
      - with 🚫, no THC9/THC8/THCP product and no wrong-route product — both hold at once;
      - surviving products keep the fit they have with no route at all;
      - a selection returns exactly the on-route subset of the unfiltered list;
      - routeTypesFor() is exactly the union; toggleRoute() keeps Any exclusive and never empty.
    Plus exact-text checks on the navigation: standard, express and demo journey all reach the
    route screen; Continue leads on correctly; the in-demo standard flow is intact.
    """
    name = 'route hard filter'
    try:
        conds = js_literal(src, 'CONDITIONS = [', '[')
        mols = js_literal(src, 'const M = {', '{')
        rmap = js_literal(src, 'var ROUTE_MAP={', '{')
        rmeta = js_literal(src, 'var ROUTE_META={', '{')
        start = src.index('V4 GUIDED EXPERIENCE — production module')
        start = src.index('(function(){', start)
        end_marker = 'guidedMol:GUIDED_MOL};\n})();'
        end = src.index(end_marker, start) + len(end_marker)
        h0 = src.index('var ROUTE_KEYS=[')
        h1 = src.index('\n', src.index('function routeLabels(withIc)', h0))
    except ValueError:
        fail(name, 'matcher module, CONDITIONS, M, ROUTE_MAP/ROUTE_META or the route helpers '
                   'not found (zero-match tripwire)')
        return
    helpers = src[h0:h1].replace('  var st={}, hist=[];', '')
    prog = '\n'.join([
        'var window={V4DEMO:{key:function(n){return n;}}};',
        'var localStorage={getItem:function(){return null;},setItem:function(){},removeItem:function(){}};',
        'var M=%s;' % mols, 'var CONDITIONS=%s;' % conds, 'var ROUTE_MAP=%s;' % rmap,
        'var ROUTE_META=%s;' % rmeta,
        src[start:end], 'var G=window.V4Guided;', helpers, ROUTE04_ENGINE_TEST])
    tmp = os.path.join(HERE, '_preflight_route04.js')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(prog)
    try:
        res = subprocess.run([JSC, tmp], capture_output=True, text=True, timeout=180)
    finally:
        os.remove(tmp)
    try:
        r = json.loads((res.stdout or '').strip().splitlines()[-1])
    except (ValueError, IndexError):
        fail(name, 'engine test did not run: ' + (res.stderr or res.stdout)[:300])
        return
    if r['scenarios'] < 2500:   # 2,959 on 2026-10-12: 269 non-empty condition×THC cases × 11 selections
        fail(name, 'only %d route scenarios ran — expected about 2,959' % r['scenarios'])
    for key, msg in [
            ('sciDiff', 'route changed the scientific molecule set'),
            ('wrong', 'a product outside the selected routes was shown'),
            ('fallback', 'routeFallback was true — the pre-ROUTE-04 fallback is back'),
            ('anyRouteExcl', 'Any route excluded products for route reasons'),
            ('anyDiff', 'Any route did not return the unfiltered list'),
            ('thcLeak', '🚫 showed a THC9/THC8/THCP product'),
            ('both', '🚫 + selected routes did not satisfy both filters at once'),
            ('fitDiff', 'route changed a surviving product\'s fit value'),
            ('pools', 'a route selection did not return exactly the on-route subset'),
            ('unionBad', 'routeTypesFor() is not the exact union of the selected routes'),
            ('toggle', 'toggleRoute() broke the Any rule (exclusive, never empty)')]:
        if r[key]:
            fail(name, '%s (%d case(s))' % (msg, r[key]))

    # navigation — exact handler text, not a pattern
    nav = [
        ("var FUNNEL_STD=['intent','refine','thc','route','second','safety','meds','rec'];",
         'FUNNEL_STD must list route between thc and second'),
        ("var FUNNEL_DEMO=['intent','refine','experience','thc','timeofday','onset','route','second','safety','meds','rec'];",
         'FUNNEL_DEMO (in-demo standard flow) changed'),
        ("if(st.dj)return go('route',scRoute);",
         'demo journey THC must lead to the route screen'),
        ("if(st.expressed)return go('route',scRoute);",
         'express THC must lead to the route screen'),
        ("if(demoMode())go('timeofday',scTimeOfDay);else go('route',scRoute);",
         'standard THC → route, in-demo standard THC → timeofday'),
        ("timeOfDay:function(v){st.timeOfDay=v;go('onset',scOnset);},",
         'in-demo standard path: timeofday → onset'),
        ("onset:function(v){st.onset=v;go('route',scRoute);},",
         'in-demo standard path: onset → route'),
        ("routeToggle:function(k){toggleRoute(k);syncRouteBtns();},",
         'route buttons must toggle the shared state without leaving the screen'),
        ("if(st.dj)return go('safety',scSafety);\n      if(st.expressed)return go('rec',scRec);\n      go('second',scSecond);},",
         'Continue: demo → safety, express → results, otherwise → second'),
        ('onclick="V4GX.routeNext()">Continue &rarr;</button>',
         'the route screen must have a Continue → action'),
        ("djToggleRoute:function(k){toggleRoute(k);scDJRefine();},",
         'the Refine route row must use the same toggleRoute() state'),
        ('routeTypes:routeTypesFor(selRoutes())};}',
         'activeRec()/djActiveRec() must pass the union of the selected routes'),
    ]
    for needle, msg in nav:
        if needle not in src:
            fail(name, msg)
    if src.count('routeTypes:routeTypesFor(selRoutes())};}') != 2:
        fail(name, 'both activeRec() and djActiveRec() must pass the selected-route union')
    for gone in ('showing the closest alternatives', 'st.route||', 'djSetRoute'):
        if gone in src:
            fail(name, 'pre-multi-select route code is back: %r' % gone)
    note('route hard filter: %d condition × THC × route-selection scenarios (%d selections incl. '
         'Oral+Sublingual, Vaporised+Smoked, Oral+Topical, all six) — molecule set route-invariant, '
         '0 products outside the selected routes, Any route unfiltered, 🚫 and route both enforced, '
         'fit unchanged, Any rule holds; standard, express and demo journey all reach the route '
         'screen' % (r['scenarios'], r['sets']))


def check_print_reset(src):
    """PRINT-01 (guard 35): a printed report must reach the printer, and must not outlive the session.

    PT_doPrint() writes the report into #printRoot and calls window.print(). It used to EMPTY
    #printRoot on afterprint. On iOS Safari print() returns immediately and the print sheet renders its
    pages afterwards, so an early afterprint emptied the report first: the owner saw a black preview
    and printed a blank page on an iPhone 16 (2026-10-13; the timing is the likely cause, not
    instrumented). The report is now cleared by resetToEntryGate() instead — the one funnel for New
    Session, I'm Done and the inactivity expiry — because it can carry the previous customer's
    condition and medications.

    Asserts, by exact text inside the real function bodies:
      - PT_doPrint() OVERWRITES #printRoot with the new report (=, never +=) and never empties it;
      - no afterprint handler anywhere empties #printRoot;
      - resetToEntryGate() empties #printRoot.
    """
    name = 'print reset'
    try:
        dp = js_literal(src, 'function PT_doPrint(', '{')
        rs = js_literal(src, 'function resetToEntryGate(', '{')
    except ValueError:
        fail(name, 'PT_doPrint() or resetToEntryGate() not found (zero-match tripwire)')
        return
    if "root.innerHTML=PT_shell(titleText, ctxLine, bodyHtml, opts);" not in dp:
        fail(name, 'PT_doPrint() no longer overwrites #printRoot with the new report')
    if "root.innerHTML+=" in dp.replace(' ', ''):
        fail(name, 'PT_doPrint() appends to #printRoot — consecutive reports would accumulate')
    if re.search(r"root\.innerHTML\s*=\s*''", dp):
        fail(name, 'PT_doPrint() empties #printRoot again — on iOS that can blank the printed page')
    for m in re.finditer(r"addEventListener\(\s*'afterprint'", src):
        if 'printRoot' in src[m.start():m.start() + 400] or "innerHTML=''" in src[m.start() - 300:m.start()].replace(' ', ''):
            fail(name, 'an afterprint handler empties report content — PRINT-01 regression')
            break
    if "var _pr = document.getElementById('printRoot'); if (_pr) _pr.innerHTML = '';" not in rs:
        fail(name, 'resetToEntryGate() no longer empties #printRoot — the previous customer\'s '
                   'printed report (which may list medications) would survive into the next session')
    note('print reset: PT_doPrint overwrites #printRoot and never clears it on afterprint; '
         'resetToEntryGate() empties it')


def check_session_isolation(src):
    """RESET-03 (guard 36): a session reset must DELETE the previous customer's data, not just hide it.

    resetToEntryGate() is the one funnel for "↩ New Session", "I'm Done — End My Session" (shown as
    "End Session" on phones) and the inactivity expiry. Closing panels hid the previous customer's data
    but left it in place. Reproduced in a code-execution harness on all three paths and confirmed by the
    owner on an iPhone 16 (2026-10-14): after End Session, Warfarin was still in Drug Interactions → My
    Medication List (and the panel's Print rebuilt a report from it) and Fibromyalgia was still in
    Entourage Effect. The typed medication-search text and the hidden Guided Match screen survived too.

    Asserts, by exact text inside the real function bodies, that resetToEntryGate() calls each
    protection, and that the two helpers it relies on still clear what they are trusted to clear.
    """
    name = 'session isolation'
    try:
        rs = js_literal(src, 'function resetToEntryGate(', '{')
        cm = js_literal(src, 'function clearMedList(', '{')
        ce = js_literal(src, 'function clearEntourage(', '{')
    except ValueError:
        fail(name, 'resetToEntryGate(), clearMedList() or clearEntourage() not found (zero-match tripwire)')
        return
    need = [
        ("try { clearMedList(); } catch(e) {}", "the previous customer's medication list and its interaction report"),
        ("var _ma = document.getElementById('medAddInput'); if (_ma) _ma.value = '';", 'typed medication-search text'),
        ("var _ms = document.getElementById('medSuggestions'); if (_ms) { _ms.style.display = 'none'; _ms.innerHTML = ''; }", 'medication suggestions'),
        ("try { _medAiQuery = ''; } catch(e) {}", 'the last medication query'),
        ("try { switchDiMode('single'); } catch(e) {}", "Drug Interactions' default single-medication mode"),
        ("try { clearEntourage(); } catch(e) {}", "the previous customer's Entourage condition and analysis"),
        ("try { window._activeCondId = null; } catch(e) {}", 'the condition Entourage re-opens on'),
        ("var _gs = document.getElementById('gxStage'); if (_gs) _gs.innerHTML = '';", 'the hidden Guided Match screen'),
    ]
    for needle, what in need:
        if needle not in rs:
            fail(name, 'resetToEntryGate() no longer clears %s — it would survive into the next '
                       'customer\'s session on a shared kiosk' % what)
    for needle, what in [("_medList = [];", '_medList'), ("document.getElementById('medListResults').innerHTML = '';", 'the list report'),
                         ('renderMedListUI();', 'the rendered list')]:
        if needle not in cm:
            fail(name, 'clearMedList() no longer clears %s' % what)
    for needle, what in [("if (sel) sel.value = '';", 'the Entourage condition'), ("document.getElementById('entBody').innerHTML", 'the Entourage analysis')]:
        if needle not in ce:
            fail(name, 'clearEntourage() no longer clears %s' % what)
    note('session isolation: resetToEntryGate() deletes the medication list, its report and mode, typed '
         'search text, the Entourage condition and the hidden Guided Match screen')


def check_trusted_markup(src, data):
    """RENDER-01 (guard 37): V2's own medication text is HTML, and must be rendered as HTML.

    DI_DATA and DDI_DATA store mech / effect / monitor as curated markup — &mdash;, <strong>, <em>, <br>,
    <sub>. The Table's Drug Interactions panel and the printed report insert it as HTML. The Guided
    Match / Demo medication check (djMedResults) passed the effect text through esc() instead, so
    customers saw literal "&mdash;" and "<strong>" there (owner, iPhone 16, Albuterol, 2026-10-19;
    a harness run of SSRI + tramadol showed 20 literal <strong>, 8 <em> and 14 &mdash; on one screen —
    263 of 289 DI records and 22 of 104 DDI pairs were affected).

    Because that text is now inserted as HTML in one more place, this guard also restricts it:
      - djMedResults() inserts the effect / mech text without esc() (exact text, both lines);
      - every DI/DDI mech, effect and monitor uses only bare <strong>, <em>, <br>, <sub> tags and no
        executable markup (script, event handlers, javascript: URLs);
      - drug names, brands and DDI pair labels carry no HTML entities or tags, because THOSE are
        still escaped (medLabel) and would otherwise show literally.
    Escaping of customer-typed text, CSV/imported fields and IDs is not touched by RENDER-01.
    """
    name = 'trusted markup'
    try:
        fn = js_literal(src, 'function djMedResults(', '{')
    except ValueError:
        fail(name, 'djMedResults() not found (zero-match tripwire)')
        return
    for needle, what in [("'+(d.effect||d.mech||'')+'", 'drug-drug pair'),
                         ("'+(e.effect||e.mech||'')+'", 'cannabis-medication')]:
        if needle not in fn:
            fail(name, 'djMedResults() no longer inserts the %s effect text as trusted HTML' % what)
    if re.search(r'esc\(\s*[de]\.(effect|mech)', fn):
        fail(name, "djMedResults() escapes V2's curated effect/mech markup again — customers would see "
                   'literal &mdash; and <strong> on the Guided Match / Demo medication check')
    bare = re.compile(r'</?(?:strong|em|br|sub)\s*/?>')
    danger = re.compile(r'<\s*script|\son[a-z]+\s*=|javascript\s*:', re.I)
    bad_tag, bad_exec, bad_name = [], [], []
    for ds in ('DI_DATA', 'DDI_DATA'):
        for r in data.get(ds, []):
            rid = r.get('id', '?')
            for f in ('mech', 'effect', 'monitor'):
                v = r.get(f) or ''
                if danger.search(v):
                    bad_exec.append('%s.%s' % (rid, f))
                for t in re.findall(r'<[^>]*>', v):
                    if not bare.fullmatch(t):
                        bad_tag.append('%s.%s %s' % (rid, f, t[:30]))
            names = [r.get('drug') or '', r.get('label') or ''] + list(r.get('brands') or []) + list(r.get('drugs') or [])
            for n in names:
                if re.search(r'&[A-Za-z#0-9]+;|<|>', str(n)):
                    bad_name.append('%s: %s' % (rid, str(n)[:60]))
    if bad_exec:
        fail(name, 'executable markup in medication text: %s' % bad_exec[:6])
    if bad_tag:
        fail(name, 'medication text uses tags outside strong/em/br/sub (or with attributes): %s' % bad_tag[:6])
    if bad_name:
        fail(name, 'drug names / brands / pair labels contain HTML entities or tags, which are escaped '
                   'and would display literally: %s' % bad_name[:6])
    if len(data.get('DI_DATA', [])) < 200 or len(data.get('DDI_DATA', [])) < 50:
        fail(name, 'DI_DATA / DDI_DATA unexpectedly small — the markup scan may not have run')
    note('trusted markup: djMedResults renders curated effect text as HTML; %d DI + %d DDI records use only '
         'strong/em/br/sub with no executable markup; names and brands are entity-free'
         % (len(data.get('DI_DATA', [])), len(data.get('DDI_DATA', []))))


def check_reviewed_registry(src, data):
    """MEDREVIEW-01 (guard 38): "recognized, no interaction record" must never read as "safe".

    DI_REVIEWED holds medications The Table recognizes but has no cannabis-interaction record for —
    first mirabegron (Myrbetriq), owner decision 2026-10-19, after a PubMed + FDA-label review found no
    cannabis–mirabegron evidence. They are searchable and can be added to a medication list, but must
    carry no severity, grade or molecules, never feed interaction logic, and every place they appear
    must say that the absence of a record is not proof that no interaction exists.

    The same change found that every medication view already offered blanket reassurance — "✓ No
    cannabis interactions found for any medications in your list", "No cannabis drug interactions
    found for …" for an unknown name — which a recognized-only medication would have inherited. Those
    lines are now qualified whenever a reviewed-only medication is listed, and an unknown name gets
    wording that cannot be read as "no interaction". A harness run caught a second, detailed-section
    "✓" line in the Table list that the first pass missed; the generic scan below exists for that.

    Asserts: registry shape and separation; unique, non-colliding ids and names; the shared wording
    and its uncertainty language; no unsafe reassurance; the new not-found wording at every search;
    qualified ✓ lines at every summary; and (revised by MED-DIMEN) Dramamine searchable ONLY through a
    label-verified allowlist of qualified names, reaching both of its medicines, with the ambiguity
    message at every search.
    """
    name = 'reviewed registry'
    try:
        reg_src = js_literal(src, 'var DI_REVIEWED = [', '[')
        alias_src = js_literal(src, 'var DI_ALIASES = {', '{')
        build_iife = src[src.index('(function buildDiIndex() {'):]
        build_iife = build_iife[:build_iife.index('\n})();') + 6]
        bodies = {k: js_literal(src, m, '{') for k, m in [
            ('renderDrugResults', 'function renderDrugResults('), ('medAddSearch', 'function medAddSearch('),
            ('renderMedListResults', 'function renderMedListResults('), ('djRenderMedSearch', 'function djRenderMedSearch('),
            ('djMedResults', 'function djMedResults('), ('scRec', '  function scRec('), ('printRec', 'printRec:function('),
            ('diReviewedCardHtml', 'function diReviewedCardHtml('), ('findDdiInteractions', 'function findDdiInteractions('),
            ('buildCannabisHitsByMol', 'function buildCannabisHitsByMol(')]}
    except ValueError:
        fail(name, 'DI_REVIEWED, DI_ALIASES, buildDiIndex or a medication display function not found '
                   '(zero-match tripwire)')
        return
    tmp = os.path.join(HERE, '_preflight_reviewed.js')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write('print(JSON.stringify({reg:%s, alias:%s}));' % (reg_src, alias_src))
    try:
        res = subprocess.run([JSC, tmp], capture_output=True, text=True, timeout=60)
    finally:
        os.remove(tmp)
    try:
        j = json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        fail(name, 'could not evaluate DI_REVIEWED / DI_ALIASES: ' + (res.stderr or res.stdout)[:200])
        return
    reg, alias = j['reg'], j['alias']
    di = data.get('DI_DATA', [])
    if not reg:
        fail(name, 'DI_REVIEWED is empty — mirabegron would fall back to "not in database"')

    # 1. shape and separation
    required = ('id', 'drug', 'brands', 'cls', 'cat', 'reviewed', 'basis')
    forbidden = ('sev', 'ev', 'mols', 'mech', 'effect', 'monitor')
    di_ids = {r.get('id') for r in di}
    di_names = set()
    for r in di:
        di_names.add(str(r.get('drug', '')).lower())
        di_names.update(str(b).lower() for b in (r.get('brands') or []))
    di_names.update(k.lower() for k in alias)
    seen = set()
    for r in reg:
        rid = r.get('id', '?')
        miss = [k for k in required if not r.get(k)]
        if miss:
            fail(name, 'DI_REVIEWED "%s" is missing %s' % (rid, ', '.join(miss)))
        bad = [k for k in forbidden if k in r]
        if bad:
            fail(name, 'DI_REVIEWED "%s" carries interaction field(s) %s — a reviewed-only medication '
                       'must not have a severity, grade, molecules or interaction text' % (rid, ', '.join(bad)))
        if rid in seen:
            fail(name, 'duplicate DI_REVIEWED id "%s"' % rid)
        seen.add(rid)
        if rid in di_ids or rid in alias:
            fail(name, 'DI_REVIEWED id "%s" collides with a DI_DATA record or alias' % rid)
        for n in [r.get('drug', '')] + list(r.get('brands') or []) + list(r.get('aliases') or []):
            if str(n).lower() in di_names:
                fail(name, 'reviewed name "%s" also resolves to an interaction record' % n)
            if re.search(r'&[A-Za-z#0-9]+;|<|>', str(n)):
                fail(name, 'reviewed name "%s" contains an HTML entity or tag' % n)
    if 'DI_REVIEWED' in build_iife:
        fail(name, 'buildDiIndex reads DI_REVIEWED — reviewed medications would enter the interaction index')
    for k in ('findDdiInteractions', 'buildCannabisHitsByMol'):
        if 'DI_REVIEWED' in bodies[k] or 'diReviewed' in bodies[k]:
            fail(name, '%s() reads the reviewed registry — it must stay interaction-only' % k)

    # 2. shared wording
    def const(v):
        m = re.search(r"var %s = '([^']*)';" % v, src)
        return m.group(1) if m else None
    msg, lbl, nf, q = const('DI_REVIEWED_MSG'), const('DI_REVIEWED_LABEL'), const('DI_NOT_FOUND_MSG'), const('DI_QUALIFIER')
    if not all((msg, lbl, nf, q)):
        fail(name, 'a shared wording constant (DI_REVIEWED_MSG / _LABEL / DI_NOT_FOUND_MSG / DI_QUALIFIER) is missing')
        return
    for needle in ('isn’t proof that no interaction exists', 'not a complete interaction screening'):
        if needle not in msg:
            fail(name, 'DI_REVIEWED_MSG lost its required uncertainty language: "%s"' % needle)
    if 'doesn’t mean it has no cannabis interaction' not in nf:
        fail(name, 'DI_NOT_FOUND_MSG no longer says that absence from the database is not "no interaction"')
    if lbl != 'Reviewed — no interaction record':
        fail(name, 'DI_REVIEWED_LABEL changed from the approved "Reviewed — no interaction record"')
    unsafe = re.compile(r'\bsafe\b|\bsafely\b|(does not|doesn[’\']t|won[’\']t|will not|cannot) interact|'
                        r'\bno risk\b|\bproven\b|\bno known interaction|\bhas no interaction|\bno interactions? found\b', re.I)
    for nm, txt in (('DI_REVIEWED_MSG', msg), ('DI_REVIEWED_LABEL', lbl), ('DI_NOT_FOUND_MSG', nf)):
        m = unsafe.search(txt)
        if m:
            fail(name, '%s contains unsafe reassurance: "%s"' % (nm, m.group(0)))

    # 3. every display site uses the shared wording
    for fn, needles in [('diReviewedCardHtml', ('DI_REVIEWED_MSG', 'DI_REVIEWED_LABEL')),
                        ('renderDrugResults', ('diReviewedCardHtml', 'DI_NOT_FOUND_MSG')),
                        ('medAddSearch', ('diReviewedMatches', 'DI_REVIEWED_LABEL', 'DI_NOT_FOUND_MSG')),
                        ('renderMedListResults', ('DI_REVIEWED_MSG', 'DI_QUALIFIER')),
                        ('djRenderMedSearch', ('diReviewedMatches', 'DI_REVIEWED_LABEL', 'DI_NOT_FOUND_MSG')),
                        ('djMedResults', ('DI_REVIEWED_MSG', 'DI_QUALIFIER')),
                        ('scRec', ('DI_REVIEWED_MSG', 'DI_QUALIFIER')),
                        ('printRec', ('DI_REVIEWED_MSG', 'DI_QUALIFIER'))]:
        for nd in needles:
            if nd not in bodies[fn]:
                fail(name, '%s() no longer uses %s — the reviewed-medication state could render without its '
                           'uncertainty wording or qualifier' % (fn, nd))
    if 'No cannabis drug interactions found' in src:
        fail(name, 'the old "No cannabis drug interactions found…" wording is back — an unknown medication '
                   'would read as having no interaction')
    if 'No match in this demonstration dataset' in src:
        fail(name, 'the old Demo not-found wording is back')

    # 4. qualified ✓ lines — exact needles, then a generic scan for any unqualified "✓ No" line
    for fn, nd in [('renderMedListResults', "'✓ No drug-drug interactions found within your medication list' + revQ"),
                   ('renderMedListResults', "'✓ No cannabis interactions found' + (revQ ||"),
                   ('renderMedListResults', "✓ No cannabis interactions found' + (_medList.some(diIsReviewed) ? DI_QUALIFIER"),
                   ('djMedResults', "'✓ No drug–drug interactions within the list'+revQ"),
                   ('djMedResults', "'✓ No cannabis interactions with the associated molecules'+revQ"),
                   ('scRec', "'no drug–drug interactions'+_revQ"),
                   ('scRec', "'none with associated molecules'+_revQ"),
                   ('scRec', "(_rev.length?'ℹ️ ':'✓ ')"),
                   ('printRec', "'no drug–drug interactions'+prQ"),
                   ('printRec', "'no cannabis–medication interactions'+prQ")]:
        if nd not in bodies[fn]:
            fail(name, '%s(): a ✓ / "no interactions" line is no longer qualified when a reviewed-only '
                       'medication is listed (%s…)' % (fn, nd[:48]))
    for fn in ('renderMedListResults', 'djMedResults'):
        for m in re.finditer(r"'[^']*✓ No [^']*'", bodies[fn]):
            tail = bodies[fn][m.end():m.end() + 70]
            if not re.match(r"\s*\+\s*(revQ|\(revQ|\(_medList\.some\(diIsReviewed\))", tail):
                fail(name, '%s(): unqualified reassurance %s — it must take the reviewed-medication qualifier'
                     % (fn, m.group(0)[:60]))

    # 5. Dramamine (MED-DIMEN, 2026-10-20). FDA labels use the brand for TWO medicines: dimenhydrinate
    #    (Original Formula, for Kids, Orange) and meclizine (Less Drowsy, All Day, Nausea, Dramamine-N,
    #    Chewable Orange). MEDREVIEW-01 banned the word outright; MED-DIMEN replaced the ban with a
    #    stricter rule: only an allowlist of qualified product names, each label-verified to ONE active
    #    ingredient, may be searchable; Dramamine may never sit in `brands` (each brand also registers
    #    its first word as a key, which would create a bare "dramamine" key) and never stand alone; a
    #    plain "Dramamine" search must reach BOTH records; and the shared ambiguity message must appear
    #    at every medication search.
    #    Allowlist = names that appear VERBATIM as a brand name on current FDA labels (owner rule,
    #    2026-10-20): no reconstructed word orders ("All Day Less Drowsy", "Children's") and no truncations
    #    ("Dramamine Original" exists only inside "Dramamine Original Formula"; prefix matching still finds
    #    it). "Dramamine Nausea" was also rejected as semantically ambiguous.
    DRAMAMINE_ALLOW = {
        'dramamine original formula': 'dimenhydrinate', 'dramamine for kids': 'dimenhydrinate',
        'dramamine less drowsy': 'meclizine', 'dramamine-n': 'meclizine'}
    for t in ('dimenhydrinate', 'meclizine'):
        if t not in di_ids:
            fail(name, 'the %s record is missing — Dramamine aliases point at it' % t)
    for r in di:
        for f in [r.get('id', ''), r.get('drug', ''), r.get('cls', '')] + list(r.get('brands') or []):
            if 'dramamine' in str(f).lower():
                fail(name, '"Dramamine" in DI_DATA "%s" name/brand/class (%s) — brands register their first word, '
                           'creating a bare "dramamine" key; use a qualified alias instead' % (r.get('id'), f))
    for r in reg:
        for f in [r.get('drug', '')] + list(r.get('brands') or []) + list(r.get('aliases') or []):
            if 'dramamine' in str(f).lower():
                fail(name, '"Dramamine" in the reviewed registry (%s)' % f)
    for k, v in alias.items():
        kl = k.lower().strip()
        if 'dramamine' not in kl:
            continue
        if kl == 'dramamine':
            fail(name, 'a bare "dramamine" alias exists — plain Dramamine must not be mapped to either medicine')
        elif re.search(r'orange|chewable|motion\s*sickness', kl):
            fail(name, 'ambiguous Dramamine alias "%s" — orange / chewable / motion-sickness names exist for '
                       'both dimenhydrinate and meclizine' % k)
        elif kl not in DRAMAMINE_ALLOW:
            fail(name, 'Dramamine alias "%s" is not on the label-verified allowlist' % k)
        elif DRAMAMINE_ALLOW[kl] != v:
            fail(name, 'Dramamine alias "%s" maps to "%s", but its labels contain %s' % (k, v, DRAMAMINE_ALLOW[kl]))
    def word_start(key, q):
        starts = [0] + [i for i in range(1, len(key)) if not key[i - 1].isalnum() and key[i].isalnum()]
        return any(key.startswith(q, st) for st in starts)
    reach = {v for k, v in alias.items() if word_start(k.lower(), 'dramamine')}
    if reach != {'dimenhydrinate', 'meclizine'}:
        fail(name, 'a plain "Dramamine" search must reach BOTH dimenhydrinate and meclizine; it reaches %s'
             % (sorted(reach) or 'nothing'))
    amb = const('DI_AMBIGUOUS_MSG')
    if not amb:
        fail(name, 'DI_AMBIGUOUS_MSG (the Dramamine ambiguity message) is missing')
    else:
        for needle in ('brand name used for different medicines', 'Check the active ingredient on your package',
                       'dimenhydrinate', 'meclizine'):
            if needle not in amb:
                fail(name, 'DI_AMBIGUOUS_MSG lost "%s"' % needle)
        if unsafe.search(amb):
            fail(name, 'DI_AMBIGUOUS_MSG contains unsafe reassurance')
    try:
        an = js_literal(src, 'function diAmbiguityNote(', '{')
    except ValueError:
        an = ''
    if "indexOf('dramamine') === 0" not in an or 'DI_AMBIGUOUS_MSG' not in an:
        fail(name, 'diAmbiguityNote() no longer shows the message for a query that is or begins with "Dramamine"')
    for fn in ('renderDrugResults', 'medAddSearch', 'djRenderMedSearch'):
        if 'diAmbiguityNote' not in bodies[fn]:
            fail(name, '%s() no longer shows the Dramamine ambiguity message' % fn)
    note('reviewed registry: %d reviewed-only medication(s) with no sev/ev/mols; shared wording carries the '
         'uncertainty language; unknown names use the new not-found text; every ✓ summary is qualified when '
         'a reviewed-only medication is listed; Dramamine only via %d label-verified qualified aliases, reaching '
         'both dimenhydrinate and meclizine, with the ambiguity message at all 3 searches'
         % (len(reg), sum(1 for k in alias if 'dramamine' in k.lower())))


def check_demo_pos_copy(src):
    """COPY-04 (guard 39): Demo copy must not claim a live POS connection that does not exist yet.

    Found by the 2026-10-21 read-only audit before the MJBizCon video capture. The Demo's Best matches
    screen said "Connected · Demo-POS · live stock" and "On a licensed dispensary, The Table connects to
    your POS / seed-to-sale system to rank in-stock products in real time (INV-05)" — present tense, with
    an internal ticket code on screen. Product selected said "✓ Clinical fit", "Sent to POS terminal ·
    added to cart" and "…all connected to the dispensary POS", and its sample lab report showed "(INV-06)".
    Live POS / inventory integration is FUTURE work; the Demo inventory is simulated.

    Scoped to the screens COPY-04 corrected (Demo Best matches, Product selected, its sample lab report,
    and the dormant standard-flow products screen), with comments stripped, so internal notes and
    explicitly future-facing wording elsewhere do not trip it. Deliberately NOT covered, and not changed
    by COPY-04: the dormant scHandoff() line and the budtender-console placard.

    COPY-05 (2026-10-21) extended it to the public Help/FAQ Refine Inventory Match answers (both FAQ
    copies), which said "Ask your dispensary whether they've connected The Table to their POS" and that
    the feature "delivers its full benefit when your dispensary has integrated The Table with their live
    POS… Ask your dispensary if they participate in the Acannability platform." Those three answers must
    say "in development", and two benefit-implying phrases in the same answer ("matches your therapeutic
    needs", "addresses your therapeutic goals") may not return. Page-wide rules are limited to exact
    phrases that have no legitimate use, so "therapeutic" alone is never banned.
    """
    name = 'demo POS copy'
    try:
        bodies = {k: strip_comments(js_literal(src, 'function %s(' % k, '{'))
                  for k in ('scDJProducts', 'scDJDone', 'coaCard', 'scProducts')}
    except ValueError:
        fail(name, 'scDJProducts(), scDJDone(), coaCard() or scProducts() not found (zero-match tripwire)')
        return
    banned = [(r'live stock', '"live stock"'),
              (r'\b(connects|is connected|are connected)\s+(directly\s+)?(to|with)\s+(your|the|a)\b[^\'<]{0,40}\bPOS\b',
               'present-tense "connects to … POS"'),
              (r'\ball connected to\b', '"all connected to …"'),
              (r'clinical fit', '"Clinical fit"'),
              (r'added to (the |your )?cart', '"added to cart"'),
              (r'sent to (the )?POS', '"sent to POS"'),
              (r'\bin real time\b', '"in real time"'),
              (r'Connected\s*·', 'a "Connected ·" status badge')]
    for fn, body in bodies.items():
        for rx, what in banned:
            m = re.search(rx, body, re.I)
            if m:
                fail(name, '%s() shows %s (%r) — live POS / inventory integration does not exist yet; the '
                           'Demo inventory is simulated' % (fn, what, m.group(0)))
    visible = strip_comments(src)
    for m in re.finditer(r'\(INV-\d+\)', visible):
        line = src.count('\n', 0, m.start()) + 1
        fail(name, 'user-visible internal ticket reference %s on line %d' % (m.group(0), line))
    need = [('scDJProducts', 'Demo-POS · simulated inventory'),
            ('scDJProducts', 'Live POS integration — simulated. In development: in a licensed dispensary, The Table is '
                             'designed to connect with POS / seed-to-sale inventory'),
            ('scProducts', 'Demo-POS · simulated inventory'),
            ('scDJDone', '✓ Profile match'),
            ('scDJDone', '✓ Medications reviewed'),
            ('scDJDone', 'Simulated hand-off to POS · demonstration only'),
            ('scDJDone', 'Live POS connection is in development.'),
            ('coaCard', 'Sample lab report for demonstration'),
            ('coaCard', 'With future POS / lab integration, this would show')]
    for fn, needle in need:
        if needle not in bodies[fn]:
            fail(name, '%s() lost its required simulation / in-development wording: "%s"' % (fn, needle[:70]))
    # COPY-05: the Help/FAQ Refine Inventory Match answers
    faq = []
    for q in ('What is Refine Inventory Match?', 'How does Refine Inventory Match help me find the right product'):
        for m in re.finditer(re.escape(q), visible):
            end = visible.find('</details>', m.end())
            faq.append((q, visible[m.end():end if end > 0 else m.end()]))
    if len(faq) != 3:
        fail(name, 'expected 3 Refine Inventory Match FAQ answers (2 copies of "What is…", 1 "How does…"), found %d '
                   '(zero-match tripwire)' % len(faq))
    for q, body in faq:
        for rx, what in [(r'is the next step', '"is the next step"'),
                         (r'\bconnects directly to\b', 'present-tense "connects directly to"'),
                         (r'delivers its full benefit', '"delivers its full benefit"')]:
            if re.search(rx, body, re.I):
                fail(name, 'FAQ "%s" says %s — live POS / inventory integration is in development' % (q[:40], what))
        need = (['inventory system is in development.', 'is in development and is not yet available.']
                if q.startswith('How does') else ['is in development, and is previewed today only as a simulation in Demo Mode.'])
        for nd in need:
            if nd not in body:
                fail(name, 'FAQ "%s" lost its required in-development wording: "%s"' % (q[:40], nd))
    for phrase in ('connected The Table to', 'integrated The Table with', 'participate in the Acannability platform',
                   'dispensary integration tool', 'matches your therapeutic needs', 'addresses your therapeutic goals'):
        i = visible.lower().find(phrase.lower())
        if i >= 0:
            fail(name, 'user-visible "%s" on line %d — implies a current POS connection or a therapeutic benefit'
                 % (phrase, src.count('\n', 0, i) + 1))
    note('demo POS copy: Best matches, Product selected and the sample lab report say simulated / in '
         'development; no "live stock", present-tense POS connection, "Clinical fit", "added to cart" or '
         'visible (INV-) codes; the %d Help/FAQ Refine Inventory Match answers say "in development"' % len(faq))


def check_research_association_labels(src, data):
    """TERM-01 (guard 40): molecule research topics are shown as "[Molecule] | [Topic]" under
    "[Molecule] — RESEARCH ASSOCIATIONS", with neutral public wording — DISPLAY ONLY.

    Owner-approved 2026-10-24. The 372 molecule accordions read "CBG as Anti-Inflammatory", "CBG as
    Glioblastoma": "as" is awkward and "Anti-…" / "Analgesic" wording implies an established effect. The
    stored topic strings in M[id].indications are DATA KEYS — citation matching (findRelevantCites),
    INDICATION_TERMS, PUBMED_EMPTY pair keys, accordion anchors (indAccId), Guided Match and condition
    matching all use them — so they are never renamed; IND_DISPLAY maps them for display only.

    Asserts: the heading template uses " | " + indLabel(i) and no "[Molecule] as" heading survives; the
    section heading and qualifier render; "This indication for", "Therapeutic Indications" and the
    molecule → topic arrow are gone from the panel; every IND_DISPLAY key is a real stored topic; no
    molecule gets two identical displayed headings; and the display map never feeds a data key.
    """
    name = 'research associations'
    try:
        m = re.search(r"const IND_DISPLAY = (\{.*?\n\});", src, re.S)
        disp = dict(re.findall(r"'([^']+)':\s*'([^']+)'", m.group(1)))
        bodies = {k: js_literal(src, mk, '{') for k, mk in [
            ('findRelevantCites', 'function findRelevantCites('), ('indAccId', 'function indAccId('),
            ('pubmedEmpty', 'function pubmedEmpty(')]}
        i0 = src.index('const indSections = mol.indications'); panel = src[i0:src.index("+ indSections\n", i0) + 400]
    except (AttributeError, ValueError):
        fail(name, 'IND_DISPLAY, the molecule panel or a key function not found (zero-match tripwire)')
        return
    visible = strip_comments(src)
    if "+ ' | ' + indLabel(i) + '</span><span class=\"acc-icon\">" not in panel:
        fail(name, 'the molecule accordion heading no longer reads "[Molecule] | [Topic]" via indLabel()')
    if re.search(r"mol\.symbol \+ ' as ' \+ i", visible):
        fail(name, 'a "[Molecule] as [Topic]" heading is back')
    for needle, what in [("Research Associations</span></div>", 'the "[Molecule] — RESEARCH ASSOCIATIONS" heading'),
                         ("'<div class=\"ind-assoc-q\">' + IND_QUALIFIER", 'the qualifier under the heading')]:
        if needle not in panel:
            fail(name, '%s is missing from the molecule panel' % what)
    q = re.search(r"const IND_QUALIFIER = '([^']*)'", src)
    if not q or 'not evidence of effectiveness' not in q.group(1) or 'preclinical, observational or clinical' not in q.group(1):
        fail(name, 'IND_QUALIFIER lost its required wording')
    for bad, what in [('This indication for', '"This indication for…"'), ('>Therapeutic Indications<', '"Therapeutic Indications"'),
                      ("mol.symbol + ' → ' + i", 'the molecule → topic arrow')]:
        if bad in visible:
            fail(name, '%s is back' % what)
    stored = {}
    for mid, mol in data['M'].items():
        for t in (mol.get('indications') or []):
            stored.setdefault(t, set()).add(mid)
    for k in disp:
        if k not in stored:
            fail(name, 'IND_DISPLAY key "%s" is not a stored research topic (renamed data, or a typo)' % k)
    for mid, mol in data['M'].items():
        seen = {}
        for t in (mol.get('indications') or []):
            if 'Unknown' in t:
                continue
            d = disp.get(t, t)
            if d in seen:
                fail(name, '%s would show two identical headings "%s" (%s and %s)' % (mid, d, seen[d], t))
            seen[d] = t
    for fn, body in bodies.items():
        if 'indLabel' in body or 'IND_DISPLAY' in body:
            fail(name, '%s() reads the display map — display wording must never become a data key' % fn)
    note('research associations: %d stored topics shown with neutral wording via IND_DISPLAY (display only); '
         'headings read "[Molecule] | [Topic]" under "[Molecule] — RESEARCH ASSOCIATIONS"' % len(disp))


def check_evidence_classification(src, data):
    """SCI-02 (guard 41): a review or meta-analysis is classified by the evidence it pools, not its type.

    Owner-approved 2026-10-24. Zheng 2018 (borneol) is a meta-analysis of animal studies only, yet read
    "supported by a systematic review or meta-analysis" in the strong-evidence colour. Citations now carry
    base:'pre' (animal / laboratory only → PreRev: preclinical tier and colour) or base:'mixed' (→ treated
    as a narrative review). Also holds the SCI-01/SCI-02 citation corrections in place.
    """
    name = 'evidence classification'
    known = {'30334462': 'pre', '40499220': 'mixed', '35459926': 'mixed', '37786751': 'mixed'}
    # citation records live in several topic blocks merged at runtime, so read them from the source text
    def records(pm):
        return re.findall(r"\{[^{}]*pmid:'%s'[^{}]*\}" % pm, src)
    def field(rec, key):
        m = re.search(r"\b%s:'([^']*)'" % key, rec)
        return m.group(1) if m else None
    def inds(rec):
        m = re.search(r"indications:\[([^\]]*)\]", rec)
        return re.findall(r"'([^']*)'", m.group(1)) if m else []
    for pm, base in known.items():
        recs = records(pm)
        if not recs:
            fail(name, 'PMID %s is no longer in the citation data (zero-match tripwire)' % pm)
        for r in recs:
            if field(r, 'base') != base:
                fail(name, 'PMID %s must carry base:%r — its pooled evidence is %s'
                     % (pm, base, 'animal / laboratory only' if base == 'pre' else 'mixed'))
    for r in re.findall(r"\{[^{}]*\bbase:'[^']*'[^{}]*\}", src):
        if field(r, 'base') not in ('pre', 'mixed', 'human'):
            fail(name, 'a citation has an unknown base %r' % field(r, 'base'))
    for r in records('38478773'):
        if 'Anti-Tumor' in inds(r):
            fail(name, 'the ASCO guideline is tagged Anti-Tumor again — it recommends AGAINST cancer-directed use')
    for r in records('35459926'):
        if 'Opioid Use Disorder' in inds(r):
            fail(name, 'the opioid-sparing analgesia review is tagged Opioid Use Disorder again')
    if len(records('37786751')) != 1 or field(records('37786751')[0], 'type') != 'Rev':
        fail(name, 'the CBD intestinal-motility review must exist once (its CBD copy) — it is attached to Δ9-THC again')
    thc = records('36452140')
    if not thc or 'Anti-Tumor' not in inds(thc[0]) or 'PRECLINICAL ONLY' not in thc[0] or 'ASCO' not in thc[0]:
        fail(name, 'Δ9-THC Antitumor Activity must cite PMID 36452140 with its preclinical / ASCO note')
    if 'CNS Protection' in (data['M'].get('BO', {}).get('indications') or []):
        fail(name, 'borneol "CNS Protection" is back — its only citation concerns BBB permeability')
    try:
        ev = src[src.index('V2EVID = (function'):]
        ev = ev[:ev.index('})();')]
    except ValueError:
        fail(name, 'V2EVID not found (zero-match tripwire)')
        return
    for needle, what in [("PreRev:2", 'PreRev ranked with preclinical evidence'),
                         ("c.base === 'pre') return 'PreRev'", "base:'pre' → PreRev"),
                         ("c.base === 'mixed') return 'Rev'", "base:'mixed' → Rev"),
                         ("var t = effType(list[k]);", 'strongestCiteType() uses effType()'),
                         ("ct = effType(c);", 'citeDirection() uses effType()')]:
        if needle not in ev:
            fail(name, 'the evidence classification lost: %s' % what)
    if re.search(r"PreRev\s*:\s*\{[^}]*tone:\s*'A'", ev):
        fail(name, 'a preclinical review is coloured as human-trial evidence')
    note('evidence classification: %d reviews classified by their pooled evidence; ASCO not antitumor; '
         'Δ9-THC antitumor = preclinical PMID 36452140; borneol CNS Protection absent' % len(known))


def check_lead_attribution(src, data):
    """SCI-03 (guard 43): a lead sentence may only rest on efficacy studies of the molecule itself.

    Owner-approved 2026-10-24. Phase C found failed trials counted as support (no finding tag), safety,
    feasibility and laboratory studies read as "supported by randomised controlled trial data", studies
    of other compounds (olorinab, dexanabinol, rimonabant, nabilone) counted for Δ9-THC, and the lead
    computed from only the first 4 citations. Holds the record classifications, the vocabularies, the
    lead logic, the show-all control, the Δ9-THC / OUD qualifier and the dronabinol wording in place,
    and adds a tripwire: a note that itself says NEGATIVE / INCONCLUSIVE / primary endpoint not met,
    protocol or safety study must carry the matching tag.
    """
    name = 'lead attribution'
    owners = {}
    def recs(pm, ind=None):
        out = []
        for m in re.finditer(r"\{[^{}]*pmid:'%s'[^{}]*\}" % pm, src):
            r = m.group(0)
            if 'indications:[' not in r:
                continue                      # condition-panel copies carry no topic tags
            if ind and ("'%s'" % ind.replace("'", "\\'")) not in r:
                continue
            out.append(r)
        return out
    def field(rec, key):
        m = re.search(r"\b%s:'((?:[^'\\]|\\.)*)'" % key, rec)
        return m.group(1) if m else None
    spec = [  # (pmid, topic or None, field, value)
        ('36878177', None, 'finding', 'inconc'), ('20855984', None, 'finding', 'inconc'),
        ('32678452', None, 'finding', 'inconc'), ('40069983', None, 'finding', 'inconc'),
        ('26560511', None, 'finding', 'neg'), ('25972490', None, 'finding', 'neg'),
        ('16849753', None, 'finding', 'neg'), ('32770831', None, 'finding', 'neg'), ('40846590', None, 'finding', 'neg'),
        ('34532852', None, 'finding', 'inconc'), ('19821373', None, 'finding', 'inconc'),
        ('31672337', None, 'finding', 'inconc'), ('41025421', None, 'finding', 'inconc'),
        ('41856154', 'Opioid Use Disorder', 'finding', 'inconc'), ('41856154', 'Bipolar Disorder', 'finding', 'inconc'),
        ('33998882', None, 'finding', 'inconc'), ('35037561', None, 'finding', 'neg'),
        ('27573936', None, 'findingBy', 'Hyperlipidemia=inconc'),
        ('40622698', None, 'role', 'safety'), ('36912195', None, 'role', 'safety'), ('30921490', None, 'role', 'pk'),
        ('12412840', None, 'role', 'safety'), ('38265476', None, 'role', 'safety'), ('26483357', None, 'role', 'safety'),
        ('36535195', None, 'role', 'safety'), ('39680411', None, 'role', 'supportive'),
        ('15892090', None, 'role', 'harm'), ('30398032', None, 'role', 'harm'), ('28946762', None, 'role', 'harm'),
        ('41604381', None, 'role', 'protocol'), ('41005282', None, 'role', 'feasibility'), ('40479610', None, 'role', 'feasibility'),
        ('22288893', None, 'role', 'mech'), ('31748505', None, 'role', 'mech'),
        ('27234658', None, 'role', 'related'), ('29976328', None, 'role', 'related'), ('31182592', None, 'role', 'population'),
        ('36740814', None, 'agent', 'other'), ('16361021', None, 'agent', 'other'),
        ('28705172', None, 'agent', 'antagonist'), ('27651218', None, 'agent', 'antagonist'),
        ('35235418', None, 'agent', 'endo'), ('28132572', None, 'agent', 'endo'),
        ('17974490', None, 'agent', 'analog'), ('20007734', None, 'agent', 'analog'), ('20434606', None, 'agent', 'analog'),
        ('31182351', None, 'agent', 'analog'), ('27428009', None, 'agent', 'analog'),
        ('16804518', None, 'design', 'pilot'), ('29444501', None, 'design', 'pilot'), ('40046611', None, 'design', 'pilot'),
        ('35037561', None, 'design', 'pilot'), ('37540761', None, 'design', 'case')]
    for pm, ind, key, val in spec:
        rs = recs(pm, ind)
        if not rs:
            fail(name, 'PMID %s%s is no longer tagged in the citation data (zero-match tripwire)' % (pm, ' / ' + ind if ind else ''))
        for r in rs:
            if field(r, key) != val:
                fail(name, 'PMID %s%s must carry %s:%r' % (pm, ' / ' + ind if ind else '', key, val))
    # the CBD chewing-gum trial tested CBD alone: it lives under CBD, never Δ9-THC
    for m in re.finditer(r"\{[^{}]*pmid:'33998882'[^{}]*\}", src):
        if 'indications:[' in m.group(0) and re.findall(r"\n\s*([A-Za-z0-9_]+)\s*:\s*\[", src[:m.start()])[-1] != 'CBD':
            fail(name, 'the CBD chewing-gum IBS trial (33998882) is filed under a molecule other than CBD')
    if not recs('7730690', 'Appetite Stimulant'):
        fail(name, 'the AIDS-anorexia dronabinol trial (7730690) lost its Appetite Stimulant tag')
    vocab = {'finding': {'neg', 'inconc', 'mixed'},
             'role': {'safety', 'harm', 'protocol', 'feasibility', 'pk', 'mech', 'population', 'related', 'supportive', 'context'},
             'agent': {'analog', 'other', 'antagonist', 'endo'}, 'design': {'pilot', 'case'}}
    tagged = [r for r in re.findall(r"\{[^{}]*pmid:'\d+'[^{}]*\}", src) if 'indications:[' in r]
    for r in tagged:
        for key, ok in vocab.items():
            v = field(r, key)
            if v is not None and v not in ok:
                fail(name, 'PMID %s has an unknown %s %r' % (field(r, 'pmid'), key, v))
        if field(r, 'agent') and not field(r, 'agentName'):
            fail(name, 'PMID %s names no compound for its agent tag' % field(r, 'pmid'))
        fb = field(r, 'findingBy')
        if fb and not re.match(r"^[^=]+=(neg|inconc|mixed)$", fb):
            fail(name, 'PMID %s has a malformed findingBy %r' % (field(r, 'pmid'), fb))
        # tripwire: the note itself says what kind of study this is — the tags must agree
        nt = re.sub(r'<[^>]+>', '', field(r, 'note') or '')
        if re.match(r'\s*NEGATIVE\b', nt) and field(r, 'finding') != 'neg' and not field(r, 'role'):
            fail(name, 'PMID %s: its note says NEGATIVE but it carries no finding:\'neg\'' % field(r, 'pmid'))
        if re.match(r'\s*INCONCLUSIVE\b', nt) and field(r, 'finding') != 'inconc':
            fail(name, 'PMID %s: its note says INCONCLUSIVE but it carries no finding:\'inconc\'' % field(r, 'pmid'))
        if re.search(r'did not meet its primary endpoint|did NOT meet its primary endpoint', nt) \
                and not field(r, 'finding') and not field(r, 'role'):
            fail(name, 'PMID %s missed its primary endpoint but counts as a positive finding' % field(r, 'pmid'))
        if re.search(r'THIS IS A PROTOCOL', nt) and field(r, 'role') != 'protocol':
            fail(name, 'PMID %s is a protocol but is not tagged role:\'protocol\'' % field(r, 'pmid'))
    for bad, what in [('not a molecule on this table', 'dronabinol described as not a molecule on The Table'),
                      ('DRONABINOL IS A SYNTHETIC THC', 'dronabinol described as not Δ9-THC'),
                      ('a synthetic, not a plant molecule', 'dronabinol described as not a plant molecule'),
                      ('Established proof-of-concept for cannabinoid-based glioblastoma therapy', 'the glioblastoma pilot overstated')]:
        if bad in src:
            fail(name, 'wording regressed: %s' % what)
    try:
        ev = src[src.index('V2EVID = (function'):]
        ev = ev[:ev.index('})();')]
        frc = src[src.index('function findRelevantCites('):]
        frc = frc[:frc.index('\n}\n')]
    except ValueError:
        fail(name, 'V2EVID / findRelevantCites not found (zero-match tripwire)')
        return
    for needle, what in [("if (!isEfficacy(list[k], ind)) continue;", 'strongestCiteType() skips non-efficacy / other-compound studies'),
                         ("!isEfficacy(c, ind)) continue;", 'citeDirection() skips non-efficacy / other-compound studies'),
                         ("function isEfficacy(c, ind) { return !!c && !roleFor(c, ind) && !c.agent; }", 'isEfficacy() = no role, no agent'),
                         ("c.design === 'pilot' || c.design === 'case') return 'Prelim'", 'pilots and case reports → Prelim'),
                         ("if (n.mixed || (n.pos && (n.neg || n.inconc))) return 'mixed';", 'benefit beside any other result reads mixed')]:
        if needle not in ev:
            fail(name, 'the lead logic lost: %s' % what)
    if re.search(r"Prelim\s*:\s*\{[^}]*tone:\s*'[ABC]'", ev):
        fail(name, 'a small uncontrolled pilot is given an evidence colour')
    if '.slice(0, 4)' in frc or '.slice(0,4)' in frc:
        fail(name, 'findRelevantCites() caps the citation set again — the lead must read every citation')
    for needle, what in [("citeDirection(relCites, strongType, i)", 'the panel lead passes its topic'),
                         ("V2EVID.citeDirection(cites, strongest, ind)", 'the Guided evidence line passes its topic'),
                         ("onclick=\"toggleCiteMore(this)\"", 'the panel offers "Show all N studies"'),
                         ("var q = citeQualifiers(c, null);", 'printed citations carry their qualifiers'),
                         ("'THC9|Opioid Use Disorder':", 'the Δ9-THC / Opioid Use Disorder qualifier')]:
        if needle not in src:
            fail(name, 'missing: %s' % what)
    if 'No study The Table holds has tested &Delta;9-THC as a treatment for opioid use disorder' not in src:
        fail(name, 'the Δ9-THC / Opioid Use Disorder qualifier lost its central statement')
    note('lead attribution: %d classified records hold; %d tagged citations within vocabulary and consistent with '
         'their notes; leads read every citation, efficacy studies of the molecule only' % (len(spec), len(tagged)))


def check_evidence_mapping(src, data):
    """SCI-04 (guard 44): evidence lines quote the condition's own research, and say what was given.

    Owner-approved 2026-10-24. The Guided Match / condition evidence line picked a molecule's topic by
    word overlap with the condition name, so "Trauma" sent PTSD to Traumatic Brain Injury, "Disease" sent
    Parkinson's to Sickle Cell and IBD, "Syndrome" sent IBS and Tourette to PCOS and "Skin" sent Acne to
    Melanoma — about 40 lines quoting another topic. The keyword citation fallback attached unverified
    papers (Δ9-THC / Inflammation on a crude extract). Holds: an explicit CONDITION_TOPICS entry for every
    condition, no known cross-topic pair, specific cancers mapped only to themselves, the keyword fallback
    retired, a preparation label on every human efficacy study, the narrative-review ordering, the TBI /
    Appetite / CBD-Anxiety qualifiers and CBN / Sleep = 7.
    """
    name = 'evidence mapping'
    try:
        cg = js_literal(src, 'var CANCER_GENERAL = [', '[')
        ct = js_literal(src, 'var CONDITION_TOPICS = {', '{')
    except Exception:
        fail(name, 'CONDITION_TOPICS / CANCER_GENERAL not found (zero-match tripwire)')
        return
    tmp = os.path.join(HERE, '_preflight_ct.js')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write('var CANCER_GENERAL = %s; print(JSON.stringify(%s));' % (cg, ct))
    try:
        res = subprocess.run([JSC, tmp], capture_output=True, text=True, timeout=60)
    finally:
        os.remove(tmp)
    try:
        topics = json.loads(res.stdout)
    except Exception:
        fail(name, 'CONDITION_TOPICS did not evaluate: %s' % (res.stderr or res.stdout)[:200])
        return
    labels = [c['label'] for c in data['CONDITIONS']]
    for lab in labels:
        if lab not in topics:
            fail(name, 'condition %r has no explicit research-topic entry' % lab)
    for lab in topics:
        if lab not in labels:
            fail(name, 'CONDITION_TOPICS names %r, which is not a condition label' % lab)
    known = set()
    for m in data['M'].values():
        known.update(m.get('indications') or [])
    for lab, lst in topics.items():
        for t in lst:
            if t not in known:
                fail(name, '%r maps to %r, which no molecule carries (typo?)' % (lab, t))
    bad = [('PTSD / Trauma', 'Traumatic Brain Injury'), ("Parkinson's Disease", 'Sickle Cell Disease'), ("Parkinson's Disease", 'IBD'),
           ("Parkinson's Disease", "Alzheimer's Disease"), ('Irritable Bowel Syndrome (IBS)', 'PCOS'), ('Irritable Bowel Syndrome (IBS)', 'IBD'),
           ('Tourette Syndrome', 'PCOS'), ('Skin Health / Acne', 'Melanoma'), ('Depression (Major Depressive Disorder)', 'Tourette Syndrome'),
           ('Arthritis / Joint Pain', 'Fibromyalgia'), ('Neuropathic Pain', 'Fibromyalgia'), ('Anti-Inflammatory', 'Antibacterial'),
           ('Anti-Inflammatory', 'Anticonvulsant'), ('Stress / Cortisol', 'Antioxidant'), ('Bone Health / Osteoporosis', 'Cardiovascular Health'),
           ('Chronic Prostatitis / Pelvic Pain', 'Endometriosis'), ('Chronic Prostatitis / Pelvic Pain', 'Chronic Liver Disease')]
    for lab, t in bad:
        if t in topics.get(lab, []):
            fail(name, 'cross-topic mapping is back: %s → %s' % (lab, t))
    specific = {'Glioblastoma', 'Breast Cancer', 'Lung Cancer', 'Colorectal Cancer', 'Leukemia', 'Prostate Cancer', 'Melanoma',
                'Pancreatic Cancer', 'Ovarian Cancer', 'Cervical Cancer'}
    for lab in specific:
        if topics.get(lab) != [lab]:
            fail(name, '%s must map only to its own research topic, got %r' % (lab, topics.get(lab)))
    if topics.get('Liver Cancer (HCC)') != ['Liver Cancer']:
        fail(name, 'Liver Cancer (HCC) must map only to Liver Cancer')
    if "if (CONDITION_TOPICS.hasOwnProperty(condLabel)) {" not in src:
        fail(name, 'matchedIndication() no longer consults the explicit map first')
    try:
        frc = src[src.index('function findRelevantCites('):]
        frc = frc[:frc.index('\n}\n')]
    except ValueError:
        fail(name, 'findRelevantCites not found (zero-match tripwire)')
        return
    if re.search(r'var\s+(rawTerms|scored|relevant)\b', frc):
        fail(name, 'the keyword citation fallback is back — every citation must be explicitly tagged')
    def field(rec, key):
        mm = re.search(r"\b%s:'((?:[^'\\]|\\.)*)'" % key, rec)
        return mm.group(1) if mm else None
    human = {'RCT', 'Meta', 'Clin', 'Reg', 'Obs'}
    n = 0
    for r in re.findall(r"\{[^{}]*pmid:'\d+'[^{}]*\}", src):
        if 'indications:[' not in r:
            continue
        pv = field(r, 'prep')
        if pv is not None and pv not in ('single', 'combo', 'plant', 'pooled'):
            fail(name, 'PMID %s has an unknown prep %r' % (field(r, 'pmid'), pv))
        t = field(r, 'type')
        if t in human and not field(r, 'role') and not field(r, 'agent') and field(r, 'base') != 'pre':
            n += 1
            if pv is None:
                fail(name, 'human efficacy study PMID %s says nothing about what was given (prep)' % field(r, 'pmid'))
    try:
        ev = src[src.index('V2EVID = (function'):]
        ev = ev[:ev.index('})();')]
    except ValueError:
        fail(name, 'V2EVID not found (zero-match tripwire)')
        return
    for needle, what in [("if (best === 'Rev' && pre) return pre;", 'a narrative review never outranks primary studies'),
                         ("if (c.type === 'Rev' && c.base === 'pre') return 'PreNarr';", 'preclinical narrative reviews are not called systematic'),
                         ("function prepOf(list, strongest, ind)", 'the preparation is assessed for the lead tier')]:
        if needle not in ev:
            fail(name, 'the lead logic lost: %s' % what)
    if re.search(r"PreNarr\s*:\s*\{[^}]*tone:\s*'[AB]'", ev):
        fail(name, 'a preclinical review is coloured as human evidence')
    for needle, what in [("'THC9|Traumatic Brain Injury':", 'the Δ9-THC / TBI qualifier'), ("'THC9|Appetite Stimulant':", 'the dronabinol appetite qualifier'),
                         ("'CBD|Anxiety':", 'the CBD / Anxiety context note'), ("That drug was <strong>dexanabinol</strong>", 'the TBI banner names dexanabinol')]:
        if needle not in src:
            fail(name, 'missing: %s' % what)
    for r in re.findall(r"\{[^{}]*pmid:'39680411'[^{}]*\}", src):
        if 'indications:[' in r and field(r, 'roleBy') != 'Anxiety=context':
            fail(name, 'the scan-anxiety trial must be context-only under CBD / Anxiety')
    byid = {c['id']: c['molecules'] for c in data['CONDITIONS']}
    for cid, mol, want, why in [('pain', 'CBD', 6, 'no positive CBD-alone pain evidence'), ('neuropathic', 'CBD', 6, 'no positive CBD-alone pain evidence'),
                                ('arthritis', 'CBD', 6, 'no positive CBD-alone pain evidence'), ('fibromyalgia', 'THC9', 7, 'no Δ9-THC-alone fibromyalgia study'),
                                ('pain', 'CBC', 6, 'preclinical only'), ('pain', 'THCP', 5, 'preclinical only'), ('neuropathic', 'CBC', 5, 'in-vitro only'),
                                ('neuropathic', 'THCP', 5, 'in-vitro only'), ('sleep', 'CBD', 6, 'CBD-alone sleep RCTs null or off-target'),
                                ('inflammation', 'THC9', 6, 'Δ9-THC-specific evidence is preclinical and partly mixed'),
                                ('ms', 'THC9', 7, 'SCI-04j: CAMS THC-alone primary endpoint missed; rest is combination evidence'),
                                ('ms', 'CBD', 6, 'SCI-04j: CBD only alongside THC'), ('spasm', 'THC9', 7, 'SCI-04j: same evidence as MS'),
                                ('spasm', 'CBD', 6, 'SCI-04j: CBD only alongside THC')]:
        if byid.get(cid, {}).get(mol) != want:
            fail(name, '%s / %s must be %d (owner-approved SCI-04b: %s)' % (mol, cid, want, why))
    # SCI-04f: MS-Related Spasticity — nabiximols is a THC + CBD product; never single-molecule evidence
    if topics.get('Multiple Sclerosis') != ['MS Spasticity'] or 'MS Spasticity' not in topics.get('Muscle Spasm / Spasticity', []):
        fail(name, 'Multiple Sclerosis / Muscle Spasm must map to the MS-Related Spasticity topic')
    for mol in ('THC9', 'CBD'):
        if 'MS Spasticity' not in (data['M'][mol].get('indications') or []):
            fail(name, '%s lost its MS-Related Spasticity research topic' % mol)
        if "'%s|MS Spasticity':" % mol not in src:
            fail(name, 'the %s / MS-Related Spasticity combination-product note is missing' % mol)
    ms = [r for r in re.findall(r"\{[^{}]*indications:\['MS Spasticity'\][^{}]*\}", src)]
    if len(ms) != 18:
        fail(name, 'expected 9 MS-Related Spasticity citations under each of THC9 and CBD, found %d records' % len(ms))
    for r in ms:
        # SCI-04i: CAMS (14615106) had a Δ9-THC-alone arm — single under THC9 only; its extract arm is combo under CBD
        if field(r, 'pmid') == '14615106':
            continue
        if field(r, 'prep') not in ('combo', 'pooled'):
            fail(name, 'MS-Related Spasticity PMID %s must be labelled combination or pooled evidence' % field(r, 'pmid'))
    cams = [r for r in ms if field(r, 'pmid') == '14615106']
    if sorted(field(r, 'prep') for r in cams) != ['combo', 'single'] or any(field(r, 'finding') != 'mixed' for r in cams):
        fail(name, 'CAMS (14615106) must be single (THC9) + combo (CBD), both finding mixed')
    # SCI-04j: under CBD, CAMS is a context-only cross-reference (combination arm) — never counted as CBD-alone evidence
    if [field(r, 'role') for r in cams if field(r, 'prep') == 'combo'] != ['context'] or [field(r, 'role') for r in cams if field(r, 'prep') == 'single'] != [None]:
        fail(name, 'CAMS must be context-only under CBD and counted (no role) under THC9')
    for r in cams:
        if 'did NOT improve' not in r or 'patient-reported spasticity improved' not in r.lower():
            fail(name, 'CAMS note must state the failed primary endpoint and the patient-reported improvement')
        if field(r, 'pmid') == '37209500' and field(r, 'role') != 'context':
            fail(name, 'the 2023 re-analysis (37209500) must be context-only: overlapping trial data')
    # SCI-04h: without a plain-language entry the fallback lowercases the topic to "ms spasticity"
    if "'HIV/AIDS':'HIV/AIDS', 'PCOS':'PCOS / PMOS'" not in src:   # SCI-04j: capitals kept in plain-language lists
        fail(name, 'HIV/AIDS and PCOS need their plain-language labels (otherwise they render as "hiv/aids" / "pcos")')
    if "'MS Spasticity':'MS-related spasticity'" not in src:
        fail(name, 'MS Spasticity needs its plain-language label (otherwise it renders as "ms spasticity")')
    sleep =[c for c in data['CONDITIONS'] if c['id'] == 'sleep']
    if not sleep or sleep[0]['molecules'].get('CBN') != 7:
        fail(name, 'CBN / Sleep must be 7 (owner-approved SCI-04; one RCT that missed its primary endpoint)')
    note('evidence mapping: %d conditions explicitly mapped, no cross-topic pairs; keyword fallback retired; '
         '%d human efficacy studies labelled by preparation' % (len(topics), n))


def check_primary_designation(src, data):
    """SCI-04g (guard 45): Primary requires evidence-based eligibility, not just a score of 8+.

    Owner-approved 2026-10-25. Of 30 score-8+ associations, six met the human-evidence standard; each
    carries a scope qualifier that must appear wherever it is called Primary. No condition may have a
    molecule promoted to Primary to fill an empty tier. Also holds the apigenin / limonene corrections.
    """
    name = 'primary designation'
    want = {'pain': 'THC9', 'neuropathic': 'THC9', 'seizures': 'CBD', 'hiv': 'THC9', 'nausea': 'THC9', 'appetite': 'THC9'}
    try:
        blk = src[src.index('var PRIMARY_EVIDENCE = {'):]
        blk = blk[:blk.index('\n};')]
    except ValueError:
        fail(name, 'PRIMARY_EVIDENCE not found (zero-match tripwire)')
        return
    got = re.findall(r"^\s*'([a-z-]+)':\s*\{(\w+):", blk, re.M)
    if sorted(got) != sorted(want.items()):
        fail(name, 'Primary-eligible set changed: %r (approved: %r)' % (sorted(got), sorted(want.items())))
    # each qualifier is checked on its own topic's entry (pain and neuropathic share wording)
    entry = dict((k, v) for k, v in re.findall(r"^\s*'([a-z-]+)':\s*\{\w+:\s*'(.*)'\},?\s*$", blk, re.M))
    for cid, needle, what in [('hiv', 'Not a treatment for HIV infection', 'HIV qualifier'),
                              ('seizures', 'Prescription CBD (Epidiolex) for Dravet and Lennox-Gastaut', 'epilepsy qualifier'),
                              ('nausea', 'Chemotherapy-related nausea', 'nausea qualifier'),
                              ('appetite', 'AIDS-related anorexia', 'appetite qualifier'),
                              ('pain', 'mainly in neuropathic or central pain', 'pain qualifier'),
                              ('neuropathic', 'mainly in neuropathic or central pain', 'neuropathic-pain qualifier')]:
        if needle not in entry.get(cid, ''):
            fail(name, 'missing: %s' % what)
    for needle, what in [("if (isPrimaryFor(condId, id, weight)) tile.classList.add('cond-primary');", 'tiles use the evidence rule'),
                         ("var primary=mols.filter(function(m){return isPrimaryFor(dispCond,m.id,m.w);});", 'Guided Match uses the evidence rule'),
                         ("var pr=list.filter(function(m){return isPrimaryFor(pcid,m.id,m.w);});", 'Guided secondary blocks use the evidence rule'),
                         ("var primary=mols.filter(function(m){return isPrimaryFor(dc,m.id,m.w);})", 'the Guided print uses the evidence rule'),
                         ("PT_sec('Primary research matches'", 'the Table print shows Primary qualifiers'),
                         ("primaryQualHtml(dispCond,pIds,false)", 'Guided Match shows Primary qualifiers'),
                         ("var primaryHtml = _pIds.length ? primaryQualHtml(condId, _pIds, false) : '<div class=\"pq-note\">' + NO_PRIMARY_NOTE + '</div>';", 'the Table panel shows Primary qualifiers'),
                         ("'</tbody></table>'+primaryQualHtml(dc,primary.map(function(m){return m.id;}),true)", 'the Guided print shows Primary qualifiers'),
                         ("function isPrimaryFor(condId, id, weight) { return weight >= 8 && !!primaryQualifier(condId, id); }", 'Primary requires both a score of 8+ and a qualifier')]:
        if needle not in src:
            fail(name, 'missing: %s' % what)
    if 'primary=mols.slice(0,Math.min(4,mols.length))' in src:
        fail(name, 'the fallback that promotes the top molecules to Primary is back')
    for bad, what in [('with well-documented anxiolytic effects in humans', 'apigenin overstatement'),
                      ('documented in both animal models and human observational reports', 'limonene overstatement')]:
        if bad in src:
            fail(name, 'description regressed: %s' % what)
    for cid, mol in want.items():
        c = [x for x in data['CONDITIONS'] if x['id'] == cid]
        if not c or c[0]['molecules'].get(mol, 0) < 8:
            fail(name, '%s / %s lost its score of 8+ (scores must be preserved)' % (mol, cid))
    note('primary designation: 6 evidence-based Primary associations with scope qualifiers; no promotion fallback; '
         'apigenin / limonene descriptions corrected')



def check_evidence_presentation(src, data):
    """SCI-04i (guard 46): topic results never present a molecule's overall grade as topic evidence.

    Owner-approved 2026-10-25. 256 of 405 ranked Supporting pairs showed "A — Clinical" or "B — Observational"
    beside a topic for which The Table held no human study. Asserts: the score legend, the overall-grade labels
    and the topic evidence line on every topic surface (Table chips, Table print, Guided Research detail, Guided
    print), the preclinical-kind map, and the four Entourage wording corrections plus the other synergy claims.
    """
    name = 'evidence presentation'
    m = re.search(r"var PRE_MODEL = buildPreModel\(\{a:'([^']*)', l:'([^']*)', c:'([^']*)'\}\);", src)
    if not m:
        fail(name, 'PRE_MODEL not found (zero-match tripwire)')
        return
    kinds = {'a': m.group(1).split(','), 'l': m.group(2).split(','), 'c': m.group(3).split(',')}
    pre = set(re.findall(r"type:'Pre'[^{}]*?pmid:'(\d+)'", src))
    allp = [p for v in kinds.values() for p in v if p]
    if len(allp) != len(set(allp)):
        fail(name, 'a PMID is listed under more than one preclinical kind')
    stray = [p for p in allp if p not in pre]
    if stray:
        fail(name, 'PRE_MODEL lists PMIDs that are not preclinical citations: %s' % stray[:5])
    if '41069302' not in kinds['c']:
        fail(name, 'the isovitexin theoretical study (41069302) must be classed computational')
    for needle, what in [
            ('Primary — human evidence, within stated limits', 'score legend: Primary'),
            ('Supporting · score 5–9', 'score legend: Supporting'),
            ("V2PLAIN.evidenceTag(id, M[id], cond.label)", 'Table chips carry the topic evidence tag'),
            ("overall research grade &mdash; all topics</strong>, not evidence for this topic", 'Table panel key'),
            ("Overall research grade &mdash; all topics: ' + M[id].evidence", 'Table print labels the overall grade'),
            ("'<div class=\"pt-print-ev\">For this topic: ' + V2PLAIN.evidenceLine(id, M[id], cond.label)", 'Table print topic line'),
            ("'<div class=\"gx-clin-ev\">For this topic: '+V2PLAIN.evidenceLine(m.id,mm,condLabel)", 'Guided Research detail topic line'),
            ("'<span class=\"gx-ev\">Overall '+mm.evidence", 'Guided Research detail labels the overall grade'),
            ("'<span class=\"pt-mol-ev\">For this topic: '+V2PLAIN.evidenceLine(m.id,mm,condLabel)", 'Guided print topic line'),
            ("Filter by Overall Research Grade (all topics)", 'filter heading'),
            ("if (pk) return V2EVID.PRE_KIND[pk].plain;", 'topic line names the kind of preclinical work'),
            ("(pr.direct ? 'includes ' : '')", 'combination evidence named even beside a single-molecule study'),
            ('>⬢ POTENTIAL MOLECULAR INTERACTIONS</div>', 'Entourage heading, all capitals (owner-approved 2026-10-25)'),
            ('Exploring how cannabis plant molecules may interact. Most proposed combinations are supported by preclinical or limited human research.', 'Entourage subtitle'),
            ('with anxiety-reducing and sedative effects in animal studies (mice)', 'linalool description'),
            ('its antioxidant activity in a computer-modelling (theoretical) study', 'isovitexin description'),
            ('not a treatment trial in patients', 'Spindle 2024 reference note')]:
        if needle not in src:
            fail(name, 'missing: %s' % what)
    for bad, what in [('Strong evidence (8–10)', 'legend equating 8–10 with strong evidence'),
                      ('Synergistic Molecule Combinations', 'Entourage "Synergistic" subtitle'),
                      ('to produce anxiolytic and sedative effects', 'linalool overstatement'),
                      ('Human aromatherapy and topical studies support', 'linalool human claim'),
                      ('potent antioxidant activity via TRPA1', 'isovitexin overstatement'),
                      ('First human clinical trial evidence of the cannabis entourage effect', 'Spindle overstatement (references)'),
                      ('first clinical evidence', 'Spindle overstatement (interaction cards)'),
                      ("broadening THC", 'nabiximols synergy claim'),
                      ('greater analgesia than THC alone', 'Johnson 2010 overstatement'),
                      ('work better together than any one alone', 'Guided Entourage teaser overstatement'),
                      ('Notable Synergies', '"Notable Synergies" heading'),
                      ('known molecular interactions', 'FAQ describing interactions as known'),
                      ('Entourage Effect Mapping', 'retired Entourage heading'),
                      ('synergistic molecule', 'synergy wording in the button, how-to or prompt'),
                      ("'Ev '+mm.evidence", 'unlabelled grade in Guided Research detail'),
                      ("<span class=\"pt-print-grade\">Evidence ' + M[id].evidence", 'unlabelled grade in the Table print')]:
        if bad in src:
            fail(name, 'regressed: %s' % what)
    note('evidence presentation: overall grades labelled on every topic surface; topic evidence line beside each molecule; '
         '%d preclinical papers classed (%d animal, %d laboratory, %d computational); Entourage wording corrected'
         % (len(allp), len([p for p in kinds['a'] if p]), len([p for p in kinds['l'] if p]), len([p for p in kinds['c'] if p])))



MED_IDENTITY_TEST = r'''
function N(s){return String(s||'').toLowerCase().replace(/[-\/]+/g,' ').replace(/\s+/g,' ').trim();}
var R={classKeys:[],rank:[],exactOther:[],table:[],amb:[],missingTarget:[],names:0};
var owners={}; function own(n,id){n=String(n||'').toLowerCase().trim(); if(!n||n==='generic')return; (owners[n]=owners[n]||{})[id]=1;}
var parts={};
DI_DATA.forEach(function(d){own(d.drug,d.id);own(d.id,d.id);(d.brands||[]).forEach(function(b){own(b,d.id);});
  parts[d.id]=String(d.drug||'').toLowerCase().split(/\s*[\/(),]\s*/).filter(Boolean);});
Object.keys(DI_ALIASES).forEach(function(a){own(a,DI_ALIASES[a]); if(!DI_DATA.some(function(d){return d.id===DI_ALIASES[a];}))R.missingTarget.push(a);});
// 1. no therapeutic-class text is a search key unless it is also a real medication name
var nameSet={}; Object.keys(owners).forEach(function(n){nameSet[n]=1;nameSet[N(n)]=1;nameSet[n.split(' ')[0]]=1;});
DI_DATA.forEach(function(d){var c=String(d.cls||'').toLowerCase().trim(); if(!c)return; [c,N(c)].forEach(function(k){ if(DI_INDEX[k]&&!nameSet[k]) R.classKeys.push(d.id+': "'+k+'"');});});
// 2. every medication name ranks its own record first; any other exact match must name the query itself
Object.keys(owners).forEach(function(n){ R.names++; var r=diSearchIndices(n); var l=r.idx.map(function(i){return DI_DATA[i];}).sort(diRankBy(r.exact,{major:1,moderate:2,minor:3}));
  if(!l.length||!owners[n][l[0].id]) R.rank.push(n+' -> '+(l[0]?l[0].id:'none'));
  l.forEach(function(d){ if(r.exact[DI_DATA.indexOf(d)]&&!owners[n][d.id]&&parts[d.id].indexOf(n)<0) R.exactOther.push(n+' -> '+d.id); });});
// 3. verified identities (MED-01 Stage 1). Each name must resolve to EXACTLY these records.
var T={'gabapenten':['gabapentin'],'gabapentine':['gabapentin'],'neurotin':['gabapentin'],'pregablin':['pregabalin'],'pregabalen':['pregabalin'],'lyrika':['pregabalin'],'apap':['acetaminophen'],'mtx':['methotrexate'],'toprol xl':['metoprolol'],'metoprolol succinate':['metoprolol'],'metoprolol succinate 25 mg':['metoprolol'],'metoprolol tartrate':['metoprolol'],'metformin er':['metformin'],'bupropion xl':['bupropion'],'zolpidem er':['zolpidem'],'diltiazem er':['diltiazem'],'verapamil sr':['verapamil'],'advil pm':['diphenhydramine','ibuprofen'],'aleve pm':['diphenhydramine','naproxen'],'ziac':['bisoprolol','hydrochlorothiazide'],'tarka':['trandolapril','verapamil'],'onfi':['clobazam'],'neurontin':['gabapentin'],'gabapentin':['gabapentin'],'gralise':['gabapentin'],'horizant':['gabapentin'],'lyrica':['pregabalin'],'lyrica cr':['pregabalin'],'pregabalin':['pregabalin'],'ambien':['zolpidem'],'zolpidem':['zolpidem'],'sonata':['zaleplon'],'zaleplon':['zaleplon'],'lunesta':['eszopiclone'],'eszopiclone':['eszopiclone'],'dyazide':['hydrochlorothiazide','triamterene'],'maxzide':['hydrochlorothiazide','triamterene'],
 'midamor':['amiloride'],'moduretic':['amiloride','hydrochlorothiazide'],'inspra':['eplerenone'],'eplerenone':['eplerenone'],'triamterene':['triamterene'],
 'amiloride':['amiloride'],'aldactone':['spironolactone'],'aldactazide':['hydrochlorothiazide','spironolactone'],
 'ezallor':['rosuvastatin'],'roszet':['ezetimibe','rosuvastatin'],'caduet':['amlodipine','atorvastatin'],'tylenol pm':['acetaminophen','diphenhydramine'],
 'symbyax':['olanzapine','fluoxetine'],'clozaril':['clozapine'],'zyprexa':['olanzapine'],'seroquel':['quetiapine'],'risperdal':['risperidone'],
 'abilify':['aripiprazole'],'aristada':['aripiprazole'],'geodon':['ziprasidone'],'latuda':['lurasidone'],'invega':['paliperidone'],'haldol':['haloperidol'],
 'quetiapine':['quetiapine'],'clozapine':['clozapine'],'olanzapine':['olanzapine'],'lurasidone':['lurasidone'],
 'triamcinolone':['intranasal_steroids','triamcinolone'],'kenalog':['triamcinolone'],'zilretta':['triamcinolone'],'nasacort':['intranasal_steroids'],
 'ritonavir':['ritonavir'],'norvir':['ritonavir'],'kaletra':['ritonavir'],'paxlovid':['ritonavir'],
 'efavirenz':['efavirenz'],'diflucan':['fluconazole'],'fluconazole':['fluconazole'],'sporanox':['itraconazole'],'itraconazole':['itraconazole'],
 'vfend':['voriconazole'],'noxafil':['posaconazole'],'cresemba':['isavuconazonium'],'isavuconazole':['isavuconazonium'],'ketoconazole':['ketoconazole'],'nizoral':['ketoconazole'],
 'tybost':['cobicistat'],'cobicistat':['cobicistat'],'nefazodone':['nefazodone'],'grapefruit juice':['grapefruit'],'phenobarbital':['phenobarbital'],'mysoline':['primidone'],
 'priftin':['rifapentine'],'rifampin':['rifampin'],'rifabutin':['rifabutin'],'clarithromycin':['clarithromycin'],'erythromycin':['erythromycin'],'verapamil':['verapamil'],'diltiazem':['diltiazem'],'calan':['verapamil'],'cardizem':['diltiazem'],'cardizem cd':['diltiazem'],'tiazac':['diltiazem'],'verelan':['verapamil'],'st johns wort':['stjohnswort'],"st john's wort":['stjohnswort'],'st. johns wort':['stjohnswort'],'sustiva':['efavirenz'],'atripla':['efavirenz'],'symfi':['efavirenz'],
 'actiq':["fentanyl"],'alprazolam':["alprazolam"],'altoprev':["lovastatin"],'arymo':["morphine"],'atenolol':["atenolol"],'ativan':["lorazepam"],'atorvastatin':["atorvastatin"],'belbuca':["buprenorphine"],'bisoprolol':["bisoprolol"],'brisdelle':["paroxetine"],'buprenorphine':["buprenorphine"],'butrans':["buprenorphine"],'bystolic':["nebivolol"],'celexa':["citalopram"],'chlordiazepoxide':["chlordiazepoxide"],'citalopram':["citalopram"],'clonazepam':["clonazepam"],'clorazepate':["clorazepate"],'codeine':["codeine"],'corgard':["nadolol"],'cymbalta':["duloxetine"],'demerol':["meperidine"],'desvenlafaxine':["desvenlafaxine"],'diastat':["diazepam"],'diazepam':["diazepam"],'dilaudid':["hydromorphone"],'doral':["quazepam"],'drizalma':["duloxetine"],'duloxetine':["duloxetine"],'duragesic':["fentanyl"],'effexor':["venlafaxine"],'effexor xr':["venlafaxine"],'escitalopram':["escitalopram"],'estazolam':["estazolam"],'exalgo':["hydromorphone"],'fentanyl':["fentanyl"],'fetzima':["levomilnacipran"],'flolipid':["simvastatin"],'fluoxetine':["fluoxetine"],'flurazepam':["flurazepam"],'halcion':["triazolam"],'hemangeol':["propranolol"],'hydrocodone':["hydrocodone"],'hydromorphone':["hydromorphone"],'inderal':["propranolol"],'inderal la':["propranolol"],'innopran xl':["propranolol"],'kadian':["morphine"],'khedezla':["desvenlafaxine"],'klonopin':["clonazepam"],'levomilnacipran':["levomilnacipran"],'lexapro':["escitalopram"],'librium':["chlordiazepoxide"],'lipitor':["atorvastatin"],'lopressor':["metoprolol"],'lopressor hct':["hydrochlorothiazide", "metoprolol"],'lorazepam':["lorazepam"],'lovastatin':["lovastatin"],'meperidine':["meperidine"],'metoprolol':["metoprolol"],'mevacor':["lovastatin"],'midazolam':["midazolam"],'milnacipran':["milnacipran"],'morphine':["morphine"],'ms contin':["morphine"],'nadolol':["nadolol"],'nayzilam':["midazolam"],'nebivolol':["nebivolol"],'niravam':["alprazolam"],'norco':["acetaminophen", "hydrocodone"],'nucynta':["tapentadol"],'nucynta er':["tapentadol"],'opana':["oxymorphone"],'oxaydo':["oxycodone"],'oxazepam':["oxazepam"],'oxycodone':["oxycodone"],'oxycontin':["oxycodone"],'oxymorphone':["oxymorphone"],'paroxetine':["paroxetine"],'paxil':["paroxetine"],'paxil cr':["paroxetine"],'percocet':["acetaminophen", "oxycodone"],'pethidine':["meperidine"],'pexeva':["paroxetine"],'pristiq':["desvenlafaxine"],'propranolol':["propranolol"],'prozac':["fluoxetine"],'quazepam':["quazepam"],'restoril':["temazepam"],'roxicodone':["oxycodone"],'roxybond':["oxycodone"],'sarafem':["fluoxetine"],'savella':["milnacipran"],'seizalam':["midazolam"],'sertraline':["sertraline"],'simvastatin':["simvastatin"],'sublocade':["buprenorphine"],'suboxone':["buprenorphine", "naloxone"],'subsys':["fentanyl"],'subutex':["buprenorphine"],'tapentadol':["tapentadol"],'temazepam':["temazepam"],'tenormin':["atenolol"],'toprol':["metoprolol"],'toprol-xl':["metoprolol"],'tranxene':["clorazepate"],'triazolam':["triazolam"],'valium':["diazepam"],'valtoco':["diazepam"],'venlafaxine':["venlafaxine"],'vicoprofen':["hydrocodone", "ibuprofen"],'vytorin':["ezetimibe", "simvastatin"],'xanax':["alprazolam"],'xanax xr':["alprazolam"],'zebeta':["bisoprolol"],'zocor':["simvastatin"],'zoloft':["sertraline"]
,
 'aciphex':["rabeprazole"],'adcirca':["tadalafil"],'advil':["ibuprofen"],'aleve':["naproxen"],'almotriptan':["almotriptan"],'amaryl':["glimepiride"],'amerge':["naratriptan"],'anaprox':["naproxen"],'avanafil':["avanafil"],'axert':["almotriptan"],'axid':["nizatidine"],'caldolor':["ibuprofen"],'chlorpropamide':["chlorpropamide"],'cialis':["tadalafil"],'dexilant':["dexlansoprazole"],'dexlansoprazole':["dexlansoprazole"],'diabeta':["glyburide"],'duexis':["famotidine", "ibuprofen"],'esomeprazole':["esomeprazole"],'frova':["frovatriptan"],'frovatriptan':["frovatriptan"],'gliclazide':["gliclazide"],'glimepiride':["glimepiride"],'glipizide':["glipizide"],'glucotrol':["glipizide"],'glyburide':["glyburide"],'glynase':["glyburide"],'ibuprofen':["ibuprofen"],'imitrex':["sumatriptan"],'lansoprazole':["lansoprazole"],'levitra':["vardenafil"],'maxalt':["rizatriptan"],'motrin':["ibuprofen"],'naprelan':["naproxen"],'naprosyn':["naproxen"],'naproxen':["naproxen"],'naratriptan':["naratriptan"],'nexium':["esomeprazole"],'nexium 24hr':["esomeprazole"],'nizatidine':["nizatidine"],'omeprazole':["omeprazole"],'pantoprazole':["pantoprazole"],'pepcid':["famotidine"],'prevacid':["lansoprazole"],'prevacid 24hr':["lansoprazole"],'prilosec':["omeprazole"],'prilosec otc':["omeprazole"],'protonix':["pantoprazole"],'rabeprazole':["rabeprazole"],'relpax':["eletriptan"],'revatio':["sildenafil"],'rizatriptan':["rizatriptan"],'sildenafil':["sildenafil"],'staxyn':["vardenafil"],'stendra':["avanafil"],'sumatriptan':["sumatriptan"],'tadalafil':["tadalafil"],'tolbutamide':["tolbutamide"],'treximet':["naproxen", "sumatriptan"],'vardenafil':["vardenafil"],'viagra':["sildenafil"],'zolmitriptan':["zolmitriptan"],'zomig':["zolmitriptan"]
,
 'chlor-trimeton':["chlorpheniramine"],'chlorpheniramine':["chlorpheniramine"],'claritin-d':["loratadine", "pseudoephedrine"],'coricidin hbp cold & flu':["acetaminophen", "chlorpheniramine"],'coricidin hbp cough & cold':["chlorpheniramine", "dextromethorphan"],'dayquil cold & flu':["acetaminophen", "dextromethorphan"],'midol complete':["acetaminophen", "caffeine", "pyrilamine"],'midol long lasting relief':["acetaminophen"],'nyquil cold & flu':["acetaminophen", "dextromethorphan", "doxylamine"],'nyquil severe cold & flu':["acetaminophen", "dextromethorphan", "doxylamine", "phenylephrine"],'phenylephrine':["phenylephrine"],'pseudoephedrine':["pseudoephedrine"],'pyrilamine':["pyrilamine"],'sudafed':["pseudoephedrine"],'sudafed pe':["phenylephrine"],
'excedrin migraine':["acetaminophen", "aspirin", "caffeine"],'excedrin extra strength':["acetaminophen", "aspirin", "caffeine"],'excedrin pm':["acetaminophen", "aspirin", "diphenhydramine"],'excedrin rapid relief':["acetaminophen"],'excedrin tension headache':["acetaminophen", "caffeine"],'excedrin':[],'pamprin':[],'pamprin ib':[],'pamprin max':["acetaminophen", "aspirin", "caffeine"],'pamprin multi-symptom':["acetaminophen", "pyrilamine"],'nyquil hot remedy':["acetaminophen", "diphenhydramine", "phenylephrine"],'tums':["calcium_carbonate"],'calcium carbonate':["calcium_carbonate"],'milk of magnesia':["magnesium_hydroxide"],'magnesium hydroxide':["magnesium_hydroxide"],'aluminum hydroxide':["aluminum_hydroxide"],'gaviscon':["aluminum_hydroxide", "magnesium_carbonate"],'rolaids':["calcium_carbonate", "magnesium_hydroxide"],'mylanta':[],'elavil':["amitriptyline"],'pamelor':["nortriptyline"],'tofranil':["imipramine"],'norpramin':["desipramine"],'anafranil':["clomipramine"],'silenor':["doxepin"],'topamax':["topiramate"],'trileptal':["oxcarbazepine"],'oxtellar xr':["oxcarbazepine"],'aptiom':["eslicarbazepine"],'catapres':["clonidine"],'cardura':["doxazosin"],'minipress':["prazosin"],'hydralazine':["hydralazine"],'labetalol':["labetalol"],'terazosin':["terazosin"],'flovent hfa':["fluticasone_propionate"],'arnuity ellipta':["fluticasone_furoate"],'pulmicort':["budesonide_inhaled"],'asmanex':["mometasone_inhaled"],'alvesco':["ciclesonide_inhaled"],'timoptic':["oph_timolol"],'betoptic':["oph_betaxolol"],'xalatan':["oph_latanoprost"],'lumigan':["oph_bimatoprost"],'travatan z':["oph_travoprost"],'zioptan':["oph_tafluprost"],'vyzulta':["oph_latanoprostene"],'omlonti':["oph_omidenepag"],'alphagan p':["oph_brimonidine"],'iopidine':["oph_apraclonidine"],'trusopt':["oph_dorzolamide"],'azopt':["oph_brinzolamide"],'mounjaro':["tirzepatide"],'zepbound':["tirzepatide"],'ozempic':["semaglutide"],'victoza':["liraglutide"],'trulicity':["dulaglutide"],'byetta':["exenatide"],'claritin':["loratadine"],'allegra':["fexofenadine"],'zyrtec':["cetirizine"],'xyzal':["levocetirizine"],'clarinex':["desloratadine"],'cozaar':["losartan"],'diovan':["valsartan"],'benicar':["olmesartan"],'avapro':["irbesartan"],'atacand':["candesartan"],'micardis':["telmisartan"],'edarbi':["azilsartan"],'hyzaar':["hydrochlorothiazide", "losartan"],'entresto':["valsartan"],'exforge':["amlodipine", "valsartan"],'lotrel':["amlodipine", "benazepril"],'zestril':["lisinopril"],'vasotec':["enalapril"],'altace':["ramipril"],'lotensin':["benazepril"],'accupril':["quinapril"],'norvasc':["amlodipine"],'procardia':["nifedipine"],'procardia xl':["nifedipine"],'adalat cc':["nifedipine"],'plendil':["felodipine"],'thalitone':["chlorthalidone"],'chlorthalidone':["chlorthalidone"],'microzide':["hydrochlorothiazide"],'deltasone':["prednisone"],'medrol':["methylprednisolone"],'decadron':["dexamethasone"],'dexamethasone':["dexamethasone"],'orapred':["prednisolone"],'flomax':["tamsulosin"],'uroxatral':["alfuzosin"],'rapaflo':["silodosin"],'maalox':[],'ritalin':["methylphenidate"],'concerta':["methylphenidate"],'methylphenidate':["methylphenidate"],'focalin':["dexmethylphenidate"],'azstarys':["dexmethylphenidate"],'adderall':["amphetamine"],'mydayis':["amphetamine"],'evekeo':["amphetamine"],'dexedrine':["dextroamphetamine"],'zenzedi':["dextroamphetamine"],'vyvanse':["lisdexamfetamine"],'lisdexamfetamine':["lisdexamfetamine"]
};
Object.keys(T).forEach(function(n){var r=diSearchIndices(n); var ex=r.idx.filter(function(i){return r.exact[i];}).map(function(i){return DI_DATA[i].id;}).sort();
  if(JSON.stringify(ex)!==JSON.stringify(T[n].slice().sort())) R.table.push(n+': expected '+T[n].join('+')+', got '+(ex.join('+')||'none'));});
// 4. family brands are never an exact match for one product, and carry a note
['robitussin','mucinex','dramamine'].forEach(function(n){var r=diSearchIndices(n); if(Object.keys(r.exact).length) R.amb.push(n+' is an exact match for '+Object.keys(r.exact).map(function(i){return DI_DATA[i].id;}).join(','));
  if(!diAmbiguityNote(n)) R.amb.push(n+' has no ambiguity note');});
if(DI_DATA.some(function(d){return d.id==='antipsychotics';})) R.table.push('the combined "antipsychotics" record is back');
if(DI_DATA.some(function(d){return d.id==='antiretrovirals';})) R.table.push('the combined "antiretrovirals" record (ritonavir + efavirenz) is back');
// 6. MED-01 Stage 1b: interaction pairs decided drug by drug for the nine antipsychotics (owner-requested review)
R.pairs=[]; var PR={}; DDI_DATA.forEach(function(p){PR[p.id]=p;});
function has(id,x){return !!PR[id]&&PR[id].drugs.indexOf(x)>=0;}
function hasFam(id,x){return Object.keys(PR).some(function(k){return (k===id||k.indexOf(id+'_')===0)&&PR[k].drugs.indexOf(x)>=0;});}   // MED-01 Phase 4: grade-split families
// MED-01 P2R3 (AC3): grade splits keep coverage across a pair and its D sibling(s)
var SPLIT={antiarrhythmic_qt:['antiarrhythmic_qt_named_','antiarrhythmic_qt_class_'],carisoprodol_cns:['carisoprodol_zdrug_class'],opioids_cyp3a4inh:['meperidine_cyp3a4inh'],antacid_absorption:['antacid_absorption_al_mg'],chlorpheniramine_cns:['chlorpheniramine_opioids'],pyrilamine_cns:['pyrilamine_opioids'],ics_cyp3a:['ciclesonide_'],ccb_cyp3a4ind:['ccb_cyp3a4ind_class','felodipine_'],taxane_cyp3a4:['paclitaxel_cyp3a4_class','nab_paclitaxel_cyp3a4_class']};
function hasF(id,x){if(has(id,x))return true; return (SPLIT[id]||[]).some(function(pre){return DDI_DATA.some(function(p){return p.id.indexOf(pre)===0&&p.drugs.indexOf(x)>=0;});});}
function need(c,m){if(!c)R.pairs.push(m);}
var AP=['clozapine','olanzapine','quetiapine','lurasidone','aripiprazole','risperidone','paliperidone','ziprasidone','haloperidol'];
AP.forEach(function(a){need(!has('antiarrhythmic_qt',a),'antiarrhythmic_qt contains '+a+' again (antipsychotics have their own QT pairs)');
  need(has('metoclopramide_eps',a),'metoclopramide_eps lost '+a+' (the metoclopramide label says avoid with all antipsychotics)');});
['clozapine','quetiapine','paliperidone','haloperidol'].forEach(function(a){need(has('antipsychotic_qt_antiarrhythmic',a),'antipsychotic_qt_antiarrhythmic lost '+a);});
need(has('ziprasidone_qt_contraindicated','ziprasidone'),'ziprasidone QT contraindication pair missing');
['olanzapine','aripiprazole','risperidone','lurasidone'].forEach(function(a){DDI_DATA.forEach(function(p){
  need(!(p.drugs.indexOf(a)>=0&&p.drugs.indexOf('sotalol')>=0),a+' is paired with sotalol in '+p.id+' (labeled trials showed little QT effect)');});});
['ropinirole_dopamine_antagonist','levodopa_dopamine_antagonist','pramipexole_dopamine_antagonist'].forEach(function(id){
  ['quetiapine','clozapine'].forEach(function(a){need(!has(id,a),id+' contains '+a+' again (assessed in pd_quetiapine_clozapine)');});
  ['olanzapine','risperidone','paliperidone','lurasidone','ziprasidone','haloperidol'].forEach(function(a){need(has(id,a),id+' lost '+a);});
  need(!has(id,'aripiprazole'),id+' contains aripiprazole again (assessed in its own Parkinson pairs)');});
need(has('levodopa_aripiprazole','carbidopa_levodopa')&&has('levodopa_aripiprazole','aripiprazole'),'levodopa_aripiprazole pair missing');
need(has('agonist_aripiprazole','ropinirole')&&has('agonist_aripiprazole','pramipexole')&&has('agonist_aripiprazole','aripiprazole'),'agonist_aripiprazole pair missing');
['carbidopa_levodopa','ropinirole','pramipexole'].forEach(function(x){need(has('pd_quetiapine_clozapine',x),'pd_quetiapine_clozapine lost '+x);});
['quetiapine_cyp3a4inh','lurasidone_cyp3a4inh','triamcinolone_cyp3a4inh','eplerenone_cyp3a4inh'].forEach(function(id){need(['ketoconazole','itraconazole','voriconazole','posaconazole','clarithromycin'].every(function(x){return has(id,x);}),id+' missing or lost a strong CYP3A4 inhibitor');});
// 7. MED-01 Stage 1c: ritonavir (strong CYP3A4 inhibitor) and efavirenz (moderate inducer) are separate medicines
['veneto_cyp3a4','brex_cyp3a4inh','colchicine_cyp3a4inh','propafenone_cyp','ticagrelor_cyp3a','gepant_cyp3a','eplerenone_cyp3a4inh','quetiapine_cyp3a4inh',
 'lurasidone_cyp3a4inh','triamcinolone_cyp3a4inh','eletriptan_cyp3a','ics_cyp3a'].forEach(function(id){
  need(has(id,'ritonavir')&&PR[id].groups&&PR[id].groups[PR[id].groups.length-1].indexOf('ritonavir')>=0,id+' must include ritonavir in its inhibitor group');
  need(!has(id,'efavirenz'),id+' contains efavirenz (an inducer, not an inhibitor)');});
['lurasidone_cyp3a4ind','quetiapine_cyp3a4ind','ritonavir_inducers'].forEach(function(id){['rifampin','rifapentine','carbamazepine','phenytoin','stjohnswort'].forEach(function(x){need(hasFam(id,x),id+' missing or lost '+x);});});
need(has('lurasidone_efavirenz','efavirenz'),'lurasidone_efavirenz pair missing');
need(has('efavirenz_inducers','rifampin')&&has('hiv_methadone','efavirenz')&&has('hiv_methadone','ritonavir'),'efavirenz inducer or HIV-methadone pair missing');
['ritonavir_statins','ritonavir_antiarrhythmics','ritonavir_benzos','ritonavir_pde5','ritonavir_alpha1'].forEach(function(id){need(has(id,'ritonavir'),id+' missing');});
var DR={}; DI_DATA.forEach(function(d){DR[d.id]=d;});
need(DR.olanzapine&&DR.olanzapine.sev==='moderate'&&DR.clozapine&&DR.clozapine.sev==='major','olanzapine Moderate / clozapine Major decision changed');
// 8. MED-01 Stage 1d / Stage 2 WS1: a combination brand is never a single-ingredient medicine. Every declared product must
//    match the verified ingredient list pinned here (RxNorm or FDA labeling, 30 Oct 2026); every ingredient must be a record
//    or a reviewed-only entry offered by the brand search; the note must name every active ingredient.
R.combo=[]; var REV={}; DI_REVIEWED.forEach(function(r){REV[r.id]=r;});
var CREQ={"yaz": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "yasmin": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "loryna": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "nikki": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "ocella": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "syeda": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "zarah": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "jasmiel": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "zumandimine": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "beyaz": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol", "levomefolate calcium"]], "safyral": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol", "levomefolate calcium"]], "nextstellis": [["drospirenone"], ["drospirenone", "estetrol"]], "drospirenone and estetrol": [["drospirenone"], ["drospirenone", "estetrol"]], "drospirenone and ethinyl estradiol": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "drospirenone/ethinyl estradiol": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "ethinyl estradiol and drospirenone": [["contraceptive_ee", "drospirenone"], ["drospirenone", "ethinyl estradiol"]], "levonorgestrel and ethinyl estradiol": [["contraceptive_ee"], ["levonorgestrel", "ethinyl estradiol"]], "levonorgestrel/ethinyl estradiol": [["contraceptive_ee"], ["levonorgestrel", "ethinyl estradiol"]], "ethinyl estradiol and levonorgestrel": [["contraceptive_ee"], ["levonorgestrel", "ethinyl estradiol"]], "norgestimate and ethinyl estradiol": [["contraceptive_ee"], ["norgestimate", "ethinyl estradiol"]], "norgestimate/ethinyl estradiol": [["contraceptive_ee"], ["norgestimate", "ethinyl estradiol"]], "ethinyl estradiol and norgestimate": [["contraceptive_ee"], ["norgestimate", "ethinyl estradiol"]], "norethindrone acetate and ethinyl estradiol": [["contraceptive_ee"], ["norethindrone acetate", "ethinyl estradiol"]], "norethindrone acetate/ethinyl estradiol": [["contraceptive_ee"], ["norethindrone acetate", "ethinyl estradiol"]], "ethinyl estradiol and norethindrone acetate": [["contraceptive_ee"], ["norethindrone acetate", "ethinyl estradiol"]], "norethindrone and ethinyl estradiol": [["contraceptive_ee"], ["norethindrone", "ethinyl estradiol"]], "norethindrone/ethinyl estradiol": [["contraceptive_ee"], ["norethindrone", "ethinyl estradiol"]], "ethinyl estradiol and norethindrone": [["contraceptive_ee"], ["norethindrone", "ethinyl estradiol"]], "desogestrel and ethinyl estradiol": [["contraceptive_ee"], ["desogestrel", "ethinyl estradiol"]], "desogestrel/ethinyl estradiol": [["contraceptive_ee"], ["desogestrel", "ethinyl estradiol"]], "ethinyl estradiol and desogestrel": [["contraceptive_ee"], ["desogestrel", "ethinyl estradiol"]], "norgestrel and ethinyl estradiol": [["contraceptive_ee"], ["norgestrel", "ethinyl estradiol"]], "ethynodiol diacetate and ethinyl estradiol": [["contraceptive_ee"], ["ethynodiol diacetate", "ethinyl estradiol"]], "norelgestromin and ethinyl estradiol": [["contraceptive_ee"], ["norelgestromin", "ethinyl estradiol"]], "etonogestrel and ethinyl estradiol": [["contraceptive_ee"], ["etonogestrel", "ethinyl estradiol"]], "segesterone acetate and ethinyl estradiol": [["contraceptive_ee"], ["segesterone acetate", "ethinyl estradiol"]], "ethinyl estradiol": [["contraceptive_ee"], ["ethinyl estradiol", "a progestin"]], "the pill": [["contraceptive_ee"], ["ethinyl estradiol", "a progestin"]], "lybalvi": [["olanzapine", "samidorphan"], ["olanzapine", "samidorphan"]], "olanzapine/samidorphan": [["olanzapine", "samidorphan"], ["olanzapine", "samidorphan"]], "olanzapine and samidorphan": [["olanzapine", "samidorphan"], ["olanzapine", "samidorphan"]], "sulfatrim": [["bactrim"], ["sulfamethoxazole", "trimethoprim"]], "efavirenz/emtricitabine/tenofovir": [["efavirenz", "emtricitabine", "tenofovir_df"], ["efavirenz", "emtricitabine", "tenofovir disoproxil fumarate"]], "efavirenz/lamivudine/tenofovir": [["efavirenz", "lamivudine", "tenofovir_df"], ["efavirenz", "lamivudine", "tenofovir disoproxil fumarate"]], "imodium multi-symptom relief": [["loperamide", "simethicone"], ["loperamide", "simethicone"]], "nirmatrelvir/ritonavir": [["nirmatrelvir", "ritonavir"], ["nirmatrelvir", "ritonavir"]], "lopinavir/ritonavir": [["lopinavir", "ritonavir"], ["lopinavir", "ritonavir"]], "excedrin tension": [["acetaminophen", "caffeine"], ["acetaminophen", "caffeine"]], "lopressor hct": [["metoprolol", "hydrochlorothiazide"], ["metoprolol", "hydrochlorothiazide"]], "robitussin dm": [["dextromethorphan", "guaifenesin"], ["dextromethorphan", "guaifenesin"]], "micardis hct": [["telmisartan", "hydrochlorothiazide"], ["telmisartan", "hydrochlorothiazide"]], "actoplus met": [["pioglitazone", "metformin"], ["pioglitazone", "metformin"]], "benicar hct": [["olmesartan", "hydrochlorothiazide"], ["olmesartan", "hydrochlorothiazide"]], "aldactazide": [["spironolactone", "hydrochlorothiazide"], ["spironolactone", "hydrochlorothiazide"]], "excedrin pm": [["acetaminophen", "aspirin", "diphenhydramine"], ["acetaminophen", "aspirin", "diphenhydramine"]], "diovan hct": [["valsartan", "hydrochlorothiazide"], ["valsartan", "hydrochlorothiazide"]], "zestoretic": [["lisinopril", "hydrochlorothiazide"], ["lisinopril", "hydrochlorothiazide"]], "vicoprofen": [["hydrocodone", "ibuprofen"], ["hydrocodone", "ibuprofen"]], "kombiglyze": [["saxagliptin", "metformin"], ["saxagliptin", "metformin"]], "segluromet": [["ertugliflozin", "metformin"], ["ertugliflozin", "metformin"]], "tylenol pm": [["acetaminophen", "diphenhydramine"], ["acetaminophen", "diphenhydramine"]], "mucinex dm": [["dextromethorphan", "guaifenesin"], ["dextromethorphan", "guaifenesin"]], "claritin-d": [["loratadine", "pseudoephedrine"], ["loratadine", "pseudoephedrine"]], "moduretic": [["amiloride", "hydrochlorothiazide"], ["amiloride", "hydrochlorothiazide"]], "augmentin": [["amoxicillin-clavulanate"], ["amoxicillin", "clavulanate"]], "premphase": [["estrogen_hrt", "medroxyprogesterone"], ["conjugated estrogens", "medroxyprogesterone"]], "simbrinza": [["oph_brinzolamide", "oph_brimonidine"], ["brinzolamide", "brimonidine"]], "rocklatan": [["oph_netarsudil", "oph_latanoprost"], ["netarsudil", "latanoprost"]], "invokamet": [["canagliflozin", "metformin"], ["canagliflozin", "metformin"]], "mucinex d": [["guaifenesin", "pseudoephedrine"], ["guaifenesin", "pseudoephedrine"]], "allegra-d": [["fexofenadine", "pseudoephedrine"], ["fexofenadine", "pseudoephedrine"]], "symbicort": [["budesonide_inhaled", "formoterol"], ["budesonide", "formoterol"]], "entresto": [["sacubitril", "valsartan"], ["sacubitril", "valsartan"]], "primaxin": [["imipenem-cilastatin"], ["imipenem", "cilastatin"]], "rifamate": [["rifampin", "isoniazid"], ["rifampin", "isoniazid"]], "combigan": [["oph_brimonidine", "oph_timolol"], ["brimonidine", "timolol"]], "treximet": [["sumatriptan", "naproxen"], ["sumatriptan", "naproxen"]], "percocet": [["oxycodone", "acetaminophen"], ["oxycodone", "acetaminophen"]], "ultracet": [["tramadol", "acetaminophen"], ["tramadol", "acetaminophen"]], "suboxone": [["buprenorphine", "naloxone"], ["buprenorphine", "naloxone"]], "contrave": [["naltrexone", "bupropion"], ["naltrexone", "bupropion"]], "namzaric": [["memantine", "donepezil"], ["memantine", "donepezil"]], "nuedexta": [["dextromethorphan", "quinidine_nuedexta"], ["dextromethorphan", "quinidine"]], "synjardy": [["empagliflozin", "metformin"], ["empagliflozin", "metformin"]], "yosprala": [["aspirin", "omeprazole"], ["aspirin", "omeprazole"]], "aggrenox": [["aspirin", "dipyridamole"], ["aspirin", "dipyridamole"]], "zyrtec-d": [["cetirizine", "pseudoephedrine"], ["cetirizine", "pseudoephedrine"]], "adderall": [["amphetamine"], ["amphetamine", "dextroamphetamine"]], "azstarys": [["dexmethylphenidate"], ["serdexmethylphenidate", "dexmethylphenidate"]], "paxlovid": [["nirmatrelvir", "ritonavir"], ["nirmatrelvir", "ritonavir"]], "avalide": [["irbesartan", "hydrochlorothiazide"], ["irbesartan", "hydrochlorothiazide"]], "exforge": [["amlodipine", "valsartan"], ["amlodipine", "valsartan"]], "vytorin": [["ezetimibe", "simvastatin"], ["ezetimibe", "simvastatin"]], "dyazide": [["triamterene", "hydrochlorothiazide"], ["triamterene", "hydrochlorothiazide"]], "maxzide": [["triamterene", "hydrochlorothiazide"], ["triamterene", "hydrochlorothiazide"]], "bactrim": [["bactrim"], ["sulfamethoxazole", "trimethoprim"]], "tmp-smx": [["bactrim"], ["sulfamethoxazole", "trimethoprim"]], "rifater": [["rifampin", "isoniazid", "pyrazinamide"], ["rifampin", "isoniazid", "pyrazinamide"]], "prempro": [["estrogen_hrt", "medroxyprogesterone"], ["conjugated estrogens", "medroxyprogesterone"]], "endocet": [["oxycodone", "acetaminophen"], ["oxycodone", "acetaminophen"]], "vicodin": [["hydrocodone", "acetaminophen"], ["hydrocodone", "acetaminophen"]], "zubsolv": [["buprenorphine", "naloxone"], ["buprenorphine", "naloxone"]], "symbyax": [["olanzapine", "fluoxetine"], ["olanzapine", "fluoxetine"]], "sinemet": [["carbidopa_levodopa"], ["carbidopa", "levodopa"]], "crexont": [["carbidopa_levodopa"], ["carbidopa", "levodopa"]], "stalevo": [["carbidopa_levodopa", "entacapone"], ["carbidopa", "levodopa", "entacapone"]], "janumet": [["sitagliptin", "metformin"], ["sitagliptin", "metformin"]], "zegerid": [["omeprazole", "sodium_bicarbonate"], ["omeprazole", "sodium bicarbonate"]], "trelegy": [["fluticasone_furoate", "umeclidinium", "vilanterol"], ["fluticasone furoate", "umeclidinium", "vilanterol"]], "breztri": [["budesonide_inhaled", "glycopyrronium", "formoterol"], ["budesonide", "glycopyrronium", "formoterol"]], "kaletra": [["lopinavir", "ritonavir"], ["lopinavir", "ritonavir"]], "atripla": [["efavirenz", "emtricitabine", "tenofovir_df"], ["efavirenz", "emtricitabine", "tenofovir disoproxil fumarate"]], "hyzaar": [["losartan", "hydrochlorothiazide"], ["losartan", "hydrochlorothiazide"]], "lotrel": [["amlodipine", "benazepril"], ["amlodipine", "benazepril"]], "caduet": [["amlodipine", "atorvastatin"], ["amlodipine", "atorvastatin"]], "roszet": [["ezetimibe", "rosuvastatin"], ["ezetimibe", "rosuvastatin"]], "unasyn": [["ampicillin-sulbactam"], ["ampicillin", "sulbactam"]], "septra": [["bactrim"], ["sulfamethoxazole", "trimethoprim"]], "cotrim": [["bactrim"], ["sulfamethoxazole", "trimethoprim"]], "bijuva": [["estrogen_hrt", "progesterone"], ["estradiol", "progesterone"]], "duavee": [["estrogen_hrt", "bazedoxifene"], ["conjugated estrogens", "bazedoxifene"]], "cosopt": [["oph_dorzolamide", "oph_timolol"], ["dorzolamide", "timolol"]], "duexis": [["ibuprofen", "famotidine"], ["ibuprofen", "famotidine"]], "lortab": [["hydrocodone", "acetaminophen"], ["hydrocodone", "acetaminophen"]], "rytary": [["carbidopa_levodopa"], ["carbidopa", "levodopa"]], "xigduo": [["dapagliflozin", "metformin"], ["dapagliflozin", "metformin"]], "advair": [["fluticasone_propionate", "salmeterol"], ["fluticasone propionate", "salmeterol"]], "airduo": [["fluticasone_propionate", "salmeterol"], ["fluticasone propionate", "salmeterol"]], "wixela": [["fluticasone_propionate", "salmeterol"], ["fluticasone propionate", "salmeterol"]], "dulera": [["mometasone_inhaled", "formoterol"], ["mometasone", "formoterol"]], "zosyn": [["piperacillin-tazobactam"], ["piperacillin", "tazobactam"]], "norco": [["hydrocodone", "acetaminophen"], ["hydrocodone", "acetaminophen"]], "dhivy": [["carbidopa_levodopa"], ["carbidopa", "levodopa"]], "duopa": [["carbidopa_levodopa"], ["carbidopa", "levodopa"]], "symfi": [["efavirenz", "lamivudine", "tenofovir_df"], ["efavirenz", "lamivudine", "tenofovir disoproxil fumarate"]], "breo": [["fluticasone_furoate", "vilanterol"], ["fluticasone furoate", "vilanterol"]], "nyquil severe": [["acetaminophen", "dextromethorphan", "doxylamine", "phenylephrine"], ["acetaminophen", "dextromethorphan", "doxylamine", "phenylephrine"]], "nyquil cold": [["acetaminophen", "dextromethorphan", "doxylamine"], ["acetaminophen", "dextromethorphan", "doxylamine"]], "dayquil severe": [["acetaminophen", "dextromethorphan", "guaifenesin", "phenylephrine"], ["acetaminophen", "dextromethorphan", "guaifenesin", "phenylephrine"]], "dayquil cold": [["acetaminophen", "dextromethorphan"], ["acetaminophen", "dextromethorphan"]], "coricidin hbp cold": [["acetaminophen", "chlorpheniramine"], ["acetaminophen", "chlorpheniramine"]], "coricidin hbp cough": [["dextromethorphan", "chlorpheniramine"], ["dextromethorphan", "chlorpheniramine"]], "coricidin hbp nighttime": [["acetaminophen", "dextromethorphan", "doxylamine"], ["acetaminophen", "dextromethorphan", "doxylamine"]], "coricidin hbp maximum strength multi": [["acetaminophen", "chlorpheniramine", "dextromethorphan"], ["acetaminophen", "chlorpheniramine", "dextromethorphan"]], "coricidin hbp maximum strength cold": [["acetaminophen", "guaifenesin"], ["acetaminophen", "guaifenesin"]], "midol complete caffeine free": [["acetaminophen", "pamabrom", "pyrilamine"], ["acetaminophen", "pamabrom", "pyrilamine"]], "midol complete": [["acetaminophen", "caffeine", "pyrilamine"], ["acetaminophen", "caffeine", "pyrilamine"]], "midol long lasting": [["acetaminophen"], ["acetaminophen"]], "midol bloat": [["pamabrom"], ["pamabrom"]], "excedrin extra strength": [["acetaminophen", "aspirin", "caffeine"], ["acetaminophen", "aspirin", "caffeine"]], "excedrin migraine": [["acetaminophen", "aspirin", "caffeine"], ["acetaminophen", "aspirin", "caffeine"]], "excedrin rapid relief": [["acetaminophen"], ["acetaminophen"]], "pamprin multi": [["acetaminophen", "pamabrom", "pyrilamine"], ["acetaminophen", "pamabrom", "pyrilamine"]], "pamprin max": [["acetaminophen", "aspirin", "caffeine"], ["acetaminophen", "aspirin", "caffeine"]], "pamprin bloat": [["pamabrom"], ["pamabrom"]], "nyquil high blood pressure": [["acetaminophen", "dextromethorphan", "doxylamine"], ["acetaminophen", "dextromethorphan", "doxylamine"]], "nyquil hbp": [["acetaminophen", "dextromethorphan", "doxylamine"], ["acetaminophen", "dextromethorphan", "doxylamine"]], "nyquil alcohol free": [["acetaminophen", "chlorpheniramine", "dextromethorphan"], ["acetaminophen", "chlorpheniramine", "dextromethorphan"]], "nyquil hot remedy": [["acetaminophen", "diphenhydramine", "phenylephrine"], ["acetaminophen", "diphenhydramine", "phenylephrine"]], "nyquil allergy": [["doxylamine"], ["doxylamine"]], "mylanta maximum strength": [["aluminum_hydroxide", "magnesium_hydroxide", "simethicone"], ["aluminum_hydroxide", "magnesium_hydroxide", "simethicone"]], "mylanta classic": [["calcium_carbonate", "magnesium_hydroxide", "simethicone"], ["calcium_carbonate", "magnesium_hydroxide", "simethicone"]], "mylanta gas": [["simethicone"], ["simethicone"]], "rolaids advanced": [["calcium_carbonate", "magnesium_hydroxide", "simethicone"], ["calcium_carbonate", "magnesium_hydroxide", "simethicone"]], "rolaids": [["calcium_carbonate", "magnesium_hydroxide"], ["calcium_carbonate", "magnesium_hydroxide"]], "gaviscon": [["aluminum_hydroxide", "magnesium_carbonate"], ["aluminum_hydroxide", "magnesium_carbonate"]], "advil pm": [["ibuprofen", "diphenhydramine"], ["ibuprofen", "diphenhydramine"]], "aleve pm": [["naproxen", "diphenhydramine"], ["naproxen", "diphenhydramine"]], "bc powder": [["aspirin", "caffeine"], ["aspirin", "caffeine"]], "bc arthritis": [["aspirin", "caffeine"], ["aspirin", "caffeine"]], "goody's extra strength": [["acetaminophen", "aspirin", "caffeine"], ["acetaminophen", "aspirin", "caffeine"]], "goody's max": [["acetaminophen", "aspirin", "caffeine"], ["acetaminophen", "aspirin", "caffeine"]], "goody's cool orange": [["acetaminophen", "aspirin", "caffeine"], ["acetaminophen", "aspirin", "caffeine"]], "goody's back and body": [["acetaminophen", "aspirin"], ["acetaminophen", "aspirin"]], "goody's hangover": [["aspirin", "caffeine"], ["aspirin", "caffeine"]], "goody's mental alert": [["aspirin", "caffeine"], ["aspirin", "caffeine"]], "ziac": [["bisoprolol", "hydrochlorothiazide"], ["bisoprolol", "hydrochlorothiazide"]], "tenoretic": [["atenolol", "chlorthalidone"], ["atenolol", "chlorthalidone"]], "vaseretic": [["enalapril", "hydrochlorothiazide"], ["enalapril", "hydrochlorothiazide"]], "atacand hct": [["candesartan", "hydrochlorothiazide"], ["candesartan", "hydrochlorothiazide"]], "tarka": [["trandolapril", "verapamil"], ["trandolapril", "verapamil"]], "genvoya": [["elvitegravir", "cobicistat", "emtricitabine", "tenofovir_af"], ["elvitegravir", "cobicistat", "emtricitabine", "tenofovir alafenamide"]], "stribild": [["elvitegravir", "cobicistat", "emtricitabine", "tenofovir_df"], ["elvitegravir", "cobicistat", "emtricitabine", "tenofovir disoproxil fumarate"]], "symtuza": [["darunavir", "cobicistat", "emtricitabine", "tenofovir_af"], ["darunavir", "cobicistat", "emtricitabine", "tenofovir alafenamide"]], "prezcobix": [["darunavir", "cobicistat"], ["darunavir", "cobicistat"]], "evotaz": [["atazanavir", "cobicistat"], ["atazanavir", "cobicistat"]], "elvitegravir/cobicistat/emtricitabine/tenofovir alafenamide": [["elvitegravir", "cobicistat", "emtricitabine", "tenofovir_af"], ["elvitegravir", "cobicistat", "emtricitabine", "tenofovir alafenamide"]], "elvitegravir/cobicistat/emtricitabine/tenofovir disoproxil fumarate": [["elvitegravir", "cobicistat", "emtricitabine", "tenofovir_df"], ["elvitegravir", "cobicistat", "emtricitabine", "tenofovir disoproxil fumarate"]], "darunavir/cobicistat/emtricitabine/tenofovir alafenamide": [["darunavir", "cobicistat", "emtricitabine", "tenofovir_af"], ["darunavir", "cobicistat", "emtricitabine", "tenofovir alafenamide"]], "darunavir/cobicistat": [["darunavir", "cobicistat"], ["darunavir", "cobicistat"]], "atazanavir/cobicistat": [["atazanavir", "cobicistat"], ["atazanavir", "cobicistat"]], "biktarvy": [["bictegravir", "emtricitabine", "tenofovir_af"], ["bictegravir", "emtricitabine", "tenofovir alafenamide"]], "descovy": [["emtricitabine", "tenofovir_af"], ["emtricitabine", "tenofovir alafenamide"]], "truvada": [["emtricitabine", "tenofovir_df"], ["emtricitabine", "tenofovir disoproxil fumarate"]], "triumeq": [["abacavir", "dolutegravir", "lamivudine"], ["abacavir", "dolutegravir", "lamivudine"]], "dovato": [["dolutegravir", "lamivudine"], ["dolutegravir", "lamivudine"]], "juluca": [["dolutegravir", "rilpivirine"], ["dolutegravir", "rilpivirine"]], "bictegravir/emtricitabine/tenofovir alafenamide": [["bictegravir", "emtricitabine", "tenofovir_af"], ["bictegravir", "emtricitabine", "tenofovir alafenamide"]], "emtricitabine/tenofovir alafenamide": [["emtricitabine", "tenofovir_af"], ["emtricitabine", "tenofovir alafenamide"]], "emtricitabine/tenofovir disoproxil fumarate": [["emtricitabine", "tenofovir_df"], ["emtricitabine", "tenofovir disoproxil fumarate"]], "abacavir/dolutegravir/lamivudine": [["abacavir", "dolutegravir", "lamivudine"], ["abacavir", "dolutegravir", "lamivudine"]], "dolutegravir/lamivudine": [["dolutegravir", "lamivudine"], ["dolutegravir", "lamivudine"]], "dolutegravir/rilpivirine": [["dolutegravir", "rilpivirine"], ["dolutegravir", "rilpivirine"]], "jalyn": [["dutasteride", "tamsulosin"], ["dutasteride", "tamsulosin"]], "dutasteride/tamsulosin": [["dutasteride", "tamsulosin"], ["dutasteride", "tamsulosin"]], "dutasteride and tamsulosin": [["dutasteride", "tamsulosin"], ["dutasteride", "tamsulosin"]], "entadfi": [["finasteride", "tadalafil"], ["finasteride", "tadalafil"]], "finasteride/tadalafil": [["finasteride", "tadalafil"], ["finasteride", "tadalafil"]]};
Object.keys(CREQ).forEach(function(b){ var c=DI_COMBINATIONS[b];
  if(!c){R.combo.push(b+': not declared in DI_COMBINATIONS');return;}
  if(JSON.stringify(c.ids)!==JSON.stringify(CREQ[b][0])||JSON.stringify(c.ingr)!==JSON.stringify(CREQ[b][1])) R.combo.push(b+': ingredients changed from the verified list');});
Object.keys(DI_COMBINATIONS).forEach(function(b){ if(!CREQ[b]) R.combo.push(b+': declared but not in the verified list'); });
Object.keys(DI_COMBINATIONS).forEach(function(b){ var c=diComboFor(b);
  if(c!==DI_COMBINATIONS[b]){R.combo.push(b+': search resolves to a different combination (prefix shadowing)');return;}
  var r=diSearchIndices(b), got={}; r.idx.forEach(function(i){got[DI_DATA[i].id]=1;
    if(r.exact[i]&&c.ids.indexOf(DI_DATA[i].id)<0) R.combo.push(b+': the '+DI_DATA[i].id+' record claims this product but is not one of its ingredients');});
  diReviewedMatches(b).forEach(function(x){got[x.id]=1;});
  c.ids.forEach(function(id){if(DR[id]) got[id]=1;});   // MED-01 WS5a: onDiSearch, medAddSearch and Guided Match add every ingredient record (source checks below); reviewed-only ingredients must still come from diReviewedMatches
  var note=diAmbiguityNote(b).toLowerCase();
  c.ingr.forEach(function(g){ if(note.indexOf(g.toLowerCase())<0) R.combo.push(b+': note does not name '+g);});
  c.ids.forEach(function(id){ if(!DR[id]&&!REV[id]){R.combo.push(b+': ingredient '+id+' has no record or reviewed entry');return;}
    if(!got[id]) R.combo.push(b+': search does not offer '+id);});});
['symfi lo','advair diskus','advair hfa','janumet xr','sinemet cr','bactrim ds','septra ds','augmentin xr','breo ellipta','trelegy ellipta','wixela inhub'].forEach(function(v){
  if(!diComboFor(v)) R.combo.push(v+': product variant is not recognised as a combination');});
// 9. MED-01 Stage 2 WS4 (supersedes the WS2 rule): the mixed records (azoles, CYP3A4 inhibitors / inducers "various") are gone.
//    Each pair now lists single medicines, chosen from the labeling. Strong groups must be complete; moderate inhibitors and
//    inducers, and partial strong groups, may appear ONLY where pinned below (each is a labeling rule documented in change
//    log section X). Any change to these maps fails until it is made deliberately.
R.cyp=[];
['azoles','cyp3a4inh','cyp3a4ind'].forEach(function(id){ if(DR[id]) R.cyp.push('the mixed record "'+id+'" is back');
  DDI_DATA.forEach(function(p){ if(p.drugs.indexOf(id)>=0) R.cyp.push(p.id+' references the retired record '+id);});
  Object.keys(DI_ALIASES).forEach(function(a){ if(DI_ALIASES[a]===id) R.cyp.push('alias '+a+' still opens '+id);});});
var S_INH=['ketoconazole','itraconazole','voriconazole','posaconazole','clarithromycin','ritonavir','cobicistat','nefazodone'],
    M_INH=['fluconazole','isavuconazonium','erythromycin','verapamil','diltiazem','grapefruit'],
    S_IND=['rifampin','rifapentine','carbamazepine','phenytoin','stjohnswort','apalutamide','enzalutamide'],
    M_IND=['efavirenz','phenobarbital','primidone','rifabutin'];
var PIN={"mod": {"sulfonylurea_fluconazole": ["fluconazole"], "avanafil_cyp3a4mod": ["fluconazole", "erythromycin", "verapamil", "diltiazem"], "apixaban_isavuconazonium": ["isavuconazonium"], "buprenorphine_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "atorvastatin_cyp3a4inh": ["erythromycin", "grapefruit"], "lovastatin_ccb_nondhp": ["verapamil", "diltiazem"], "veneto_cyp3a4": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "dofetilide_contraindicated": ["verapamil"], "propafenone_cyp": ["erythromycin", "grapefruit"], "eplerenone_ccb_nondhp": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem"], "lurasidone_ccb_nondhp": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem"], "tacrolimus_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "sirolimus_cyp3a4inh": ["isavuconazonium", "erythromycin", "verapamil", "diltiazem"], "everolimus_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "rivaroxaban_cyp3a4inh": ["erythromycin"], "methadone_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "abemaciclib_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "dasatinib_cyp3a4inh": ["grapefruit"], "nilotinib_cyp3a4inh": ["grapefruit"], "bosutinib_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "ibrutinib_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "acalabrutinib_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "erlotinib_cyp3a4inh": ["grapefruit"], "regorafenib_cyp3a4inh": ["grapefruit"], "olaparib_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "vincristine_cyp3a4inh": ["fluconazole"], "tretinoin_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "cariprazine_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "ivabradine_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "ccb_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "statins_cyp3a4inh": ["erythromycin", "grapefruit"], "statins_ccb": ["verapamil", "diltiazem"], "colchicine_cyp3a4mod": ["fluconazole", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "gepant_cyp3a4mod": ["fluconazole", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "dofetilide_diltiazem": ["diltiazem"], "dabigatran_diltiazem": ["diltiazem"], "loperamide_diltiazem": ["diltiazem"], "digoxin_verapamil": ["verapamil"], "digoxin_diltiazem": ["diltiazem"], "dofetilide_cyp3a4_caution": ["isavuconazonium", "grapefruit"], "dofetilide_qt_antiinfective": ["erythromycin"], "dofetilide_azole_qt": ["fluconazole"], "sotalol_procainamide_macrolide": ["erythromycin"], "quinidine_erythromycin": ["erythromycin"], "disopyramide_macrolide": ["erythromycin"], "quinidine_azole_contraindicated": ["fluconazole"], "disopyramide_azole_cyp3a4": ["fluconazole"], "sotalol_procainamide_azole_qt": ["fluconazole"], "amiodarone_macrolide": ["erythromycin"], "amiodarone_azole_reported": ["fluconazole"], "amiodarone_isavuconazonium": ["isavuconazonium"], "nuedexta_cyp3a4_named": ["fluconazole", "erythromycin"], "nuedexta_cyp3a4_ecg": ["verapamil", "diltiazem", "grapefruit"], "nuedexta_isavuconazonium": ["isavuconazonium"], "ritonavir_isavuconazonium": ["isavuconazonium"], "cobicistat_isavuconazonium": ["isavuconazonium"], "efavirenz_diltiazem": ["diltiazem"], "efavirenz_ccb": ["verapamil"], "efavirenz_qt": ["fluconazole", "erythromycin"], "loperamide_pgp": ["verapamil"], "dabigatran_pgp": ["verapamil"], "booster_ndhp_ccb": ["verapamil", "diltiazem"], "kaletra_pr": ["verapamil", "diltiazem"], "quinidine_diltiazem": ["diltiazem"], "quinidine_verapamil": ["verapamil"], "loperamide_cyp3a4_other": ["fluconazole", "erythromycin"], "steroid_cyp3a4inh_labeled": ["erythromycin"], "kaletra_qt": ["fluconazole", "erythromycin"], "cyclosporine_cyp3a4inh_class": ["isavuconazonium"], "tramadol_cyp3a4inh_class": ["fluconazole", "isavuconazonium", "verapamil", "diltiazem", "grapefruit"], "meperidine_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "cyclosporine_cyp3a4inh": ["fluconazole", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "tramadol_cyp3a4inh": ["erythromycin"], "opioids_cyp3a4inh": ["fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "rilpivirine_macrolides": ["erythromycin"], "rilpivirine_qt": ["fluconazole"], "pde5_cyp3a4inh_erythromycin": ["erythromycin"], "pde5_cyp3a4inh_named_tadalafil": ["erythromycin"]}, "partS": {"atorvastatin_cyp3a4inh": ["posaconazole", "ritonavir"], "atorvastatin_posaconazole": ["ketoconazole", "itraconazole", "voriconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "avanafil_cyp3a4inh": ["ritonavir"], "benzos_cyp3a4inh_partial": ["ritonavir"], "apixaban_posaconazole": ["ketoconazole", "itraconazole", "voriconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "rivaroxaban_posaconazole": ["ketoconazole", "itraconazole", "voriconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "doac_voriconazole": ["ketoconazole", "itraconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "clari_statin": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "dofetilide_contraindicated": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "canagliflozin_ugt_inducers": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"], "apixaban_cyp3a4inh": ["voriconazole", "posaconazole", "clarithromycin", "nefazodone"], "rivaroxaban_cyp3a4inh": ["voriconazole", "posaconazole", "clarithromycin", "ritonavir", "nefazodone"], "methadone_cyp3a4inh": ["ritonavir", "cobicistat"], "statins_cyp3a4inh": ["clarithromycin", "ritonavir"], "benzos_cyp3a4inh": ["ritonavir"], "pde5_cyp3a4inh": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "alpha1_cyp3a4inh": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "dofetilide_label_avoid": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "dofetilide_cyp3a4_caution": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin"], "dofetilide_qt_antiinfective": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "dofetilide_azole_qt": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "sotalol_procainamide_macrolide": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "quinidine_clarithromycin": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "disopyramide_macrolide": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "quinidine_azole_contraindicated": ["itraconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "quinidine_itraconazole": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "disopyramide_azole_contraindicated": ["voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "disopyramide_azole_cyp3a4": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "sotalol_procainamide_azole_qt": ["itraconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "amiodarone_macrolide": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "amiodarone_azole_reported": ["ketoconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "amiodarone_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "amiodarone_posaconazole": ["ketoconazole", "itraconazole", "voriconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "quinidine_cobicistat": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "nefazodone"], "disopyramide_booster": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "nefazodone"], "quinidine_disopyramide_nefazodone": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat"], "nuedexta_cyp3a4_named": ["cobicistat", "nefazodone"], "nuedexta_cyp3a4_ecg": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir"], "ritonavir_voriconazole": ["ketoconazole", "itraconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "cobicistat_voriconazole": ["ketoconazole", "itraconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "ritonavir_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "ritonavir_itraconazole": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "cobicistat_ketoconazole_itraconazole": ["voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "amiodarone_cobicistat": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "nefazodone"], "flecainide_cobicistat": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "nefazodone"], "cobicistat_clarithromycin": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "efavirenz_voriconazole": ["ketoconazole", "itraconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "efavirenz_itraconazole": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "efavirenz_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "efavirenz_posaconazole": ["ketoconazole", "itraconazole", "voriconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "efavirenz_clarithromycin": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "loperamide_pgp": ["voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "dabigatran_pgp": ["voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "linezolid_maoi_class": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat"], "amiodarone_nefazodone": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat"], "ritonavir_clarithromycin": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "ritonavir_ssri_nefazodone": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat"], "loperamide_cyp3a4_other": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "cobicistat"], "methylprednisolone_azole": ["voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "dexamethasone_itraconazole": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "steroid_cyp3a4inh_labeled": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "dexamethasone_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "steroid_azole_inferred": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "kaletra_qt": ["ketoconazole", "itraconazole", "ritonavir", "cobicistat", "nefazodone"], "cyclosporine_cyp3a4inh_class": ["ketoconazole", "itraconazole", "voriconazole", "clarithromycin", "ritonavir", "nefazodone"], "tramadol_cyp3a4inh_class": ["ketoconazole", "ritonavir"], "ciclesonide_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "ciclesonide_cyp3a4inh_class": ["ketoconazole"], "paclitaxel_cyp3a4_class": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "nefazodone"], "meperidine_cyp3a4inh": ["ritonavir"], "cyclosporine_cyp3a4inh": ["posaconazole", "cobicistat"], "tramadol_cyp3a4inh": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"], "taxane_cyp3a4": ["voriconazole", "posaconazole", "cobicistat"], "drospirenone_cyp3a4inh": ["ketoconazole", "ritonavir", "cobicistat"], "drospirenone_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "drospirenone_ritonavir": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"], "rilpivirine_macrolides": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "buspirone_cyp3a4inh": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat"], "buspirone_cyp3a4inh_named": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"], "buspirone_cyp3a4inh_class": ["ketoconazole", "itraconazole", "ritonavir", "nefazodone"], "zolpidem_cyp3a4inh": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "zolpidem_cyp3a4inh_itraconazole": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "zolpidem_cyp3a4inh_class": ["ketoconazole", "itraconazole"], "eszopiclone_cyp3a4inh": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "eszopiclone_cyp3a4inh_named": ["ketoconazole", "voriconazole", "posaconazole", "cobicistat"], "eszopiclone_cyp3a4inh_class": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "nefazodone"], "pde5_cyp3a4inh_named_sildenafil": ["voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "pde5_cyp3a4inh_named_tadalafil": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "pde5_cyp3a4inh_named_vardenafil": ["ketoconazole", "voriconazole", "posaconazole", "ritonavir", "nefazodone"], "pde5_cyp3a4inh_class": ["ketoconazole", "itraconazole", "ritonavir"], "pde5_cyp3a4inh_class_vardenafil": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "cobicistat"], "alpha1_cyp3a4inh_named_itraconazole": ["ketoconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "alpha1_cyp3a4inh_named_clarithromycin": ["ketoconazole", "itraconazole", "voriconazole", "posaconazole", "ritonavir", "cobicistat", "nefazodone"], "alpha1_cyp3a4inh_class_tamsulosin": ["ketoconazole", "ritonavir"], "alpha1_cyp3a4inh_class_alfuzosin": ["ketoconazole", "itraconazole", "ritonavir"], "alpha1_cyp3a4inh_class_silodosin": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir"], "eletriptan_cyp3a": ["ketoconazole", "voriconazole", "posaconazole", "cobicistat"], "eletriptan_cyp3a_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "eletriptan_cyp3a_class": ["ketoconazole", "itraconazole", "clarithromycin", "ritonavir", "nefazodone"], "almotriptan_cyp3a4inh": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "almotriptan_cyp3a4inh_class": ["ketoconazole"], "galantamine_ketoconazole": ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"]}, "indS": {"rifampin_morphine_hydromorphone": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "ssri_sjw": ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "apalutamide", "enzalutamide"], "warf_sjw": ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "apalutamide", "enzalutamide"], "efavirenz_inducers": ["apalutamide", "enzalutamide"], "rasagiline_serotonergic": ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "apalutamide", "enzalutamide"], "canagliflozin_ugt_inducers": ["rifapentine", "carbamazepine", "stjohnswort", "apalutamide", "enzalutamide"], "letermovir_rifamycins": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "cobicistat_inducers": ["rifapentine", "apalutamide", "enzalutamide"], "cobicistat_inducers_named_combo": ["rifampin", "carbamazepine", "phenytoin", "stjohnswort", "enzalutamide"], "cobicistat_inducers_unnamed": ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide"], "linezolid_maoi_class": ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "apalutamide", "enzalutamide"], "cyclosporine_cyp3a4ind_class": ["rifampin", "carbamazepine", "phenytoin", "stjohnswort"], "methadone_cyp3a4ind_class": ["rifampin", "carbamazepine", "phenytoin", "stjohnswort"], "tramadol_cyp3a4ind_class": ["rifampin", "carbamazepine", "phenytoin"], "felodipine_anticonvulsant_inducers": ["rifampin", "rifapentine", "stjohnswort", "apalutamide", "enzalutamide"], "felodipine_cyp3a4ind_class": ["carbamazepine", "phenytoin"], "paclitaxel_cyp3a4_class": ["rifampin", "carbamazepine"], "cyclosporine_cyp3a4ind": ["rifapentine", "apalutamide", "enzalutamide"], "tramadol_cyp3a4ind": ["rifapentine", "stjohnswort", "apalutamide", "enzalutamide"], "methadone_cyp3a4ind": ["rifapentine", "apalutamide", "enzalutamide"], "taxane_cyp3a4": ["rifapentine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "hc_inducers": ["apalutamide", "enzalutamide"], "hc_inducers_unnamed": ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "stjohnswort"], "dmpa_im_inducers": ["apalutamide", "enzalutamide"], "lng_ec_rifampin": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "lng_ec_inducers": ["rifampin"], "ulipristal_rifampin": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "ulipristal_inducers": ["rifampin", "rifapentine", "apalutamide", "enzalutamide"], "ulipristal_inducers_unnamed": ["rifampin", "carbamazepine", "phenytoin", "stjohnswort"], "bictegravir_rifampin": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "dolutegravir_inducers_dose": ["rifapentine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "dolutegravir_inducers_avoid": ["rifampin", "rifapentine", "carbamazepine", "apalutamide", "enzalutamide"], "tenofovir_af_rifamycins": ["carbamazepine", "phenytoin", "apalutamide", "enzalutamide"], "tenofovir_af_carbamazepine": ["rifampin", "rifapentine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "tenofovir_af_anticonvulsants": ["rifampin", "rifapentine", "carbamazepine", "stjohnswort", "apalutamide", "enzalutamide"], "rilpivirine_contraindicated": ["apalutamide", "enzalutamide"], "vemlidy_anticonvulsants": ["rifampin", "rifapentine", "carbamazepine", "stjohnswort", "apalutamide", "enzalutamide"], "quetiapine_cyp3a4ind": ["rifampin", "rifapentine", "carbamazepine", "stjohnswort", "apalutamide", "enzalutamide"], "quetiapine_cyp3a4ind_named": ["rifapentine", "phenytoin", "apalutamide", "enzalutamide"], "quetiapine_cyp3a4ind_class": ["rifampin", "carbamazepine", "phenytoin", "stjohnswort"], "buspirone_cyp3a4ind": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "buspirone_cyp3a4ind_named": ["rifampin", "rifapentine", "stjohnswort", "apalutamide", "enzalutamide"], "buspirone_cyp3a4ind_class": ["rifampin", "carbamazepine", "phenytoin"], "samidorphan_rifampin": ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "samidorphan_cyp3a4ind": ["rifampin"]}, "mi": {"zaleplon_cyp3a4ind": ["phenobarbital"], "ritonavir_inducers": ["phenobarbital", "primidone"], "efavirenz_inducers": ["phenobarbital", "rifabutin"], "lurasidone_efavirenz": ["efavirenz", "phenobarbital", "primidone"], "canagliflozin_ugt_inducers": ["phenobarbital"], "tacrolimus_cyp3a4ind": ["phenobarbital", "rifabutin"], "sirolimus_cyp3a4ind": ["rifabutin"], "abemaciclib_cyp3a4ind": ["efavirenz", "phenobarbital", "primidone", "rifabutin"], "imatinib_cyp3a4ind": ["phenobarbital", "rifabutin"], "erlotinib_cyp3a4ind": ["phenobarbital", "rifabutin"], "lorlatinib_cyp3a4ind": ["efavirenz", "phenobarbital", "primidone", "rifabutin"], "regorafenib_cyp3a4ind": ["phenobarbital"], "olaparib_cyp3a4ind": ["efavirenz", "phenobarbital", "primidone", "rifabutin"], "venetoclax_cyp3a4ind": ["efavirenz", "phenobarbital", "primidone", "rifabutin"], "ivabradine_cyp3a4ind": ["phenobarbital", "primidone"], "ticagrelor_cyp3a4ind": ["phenobarbital"], "gepant_cyp3a4ind": ["efavirenz", "phenobarbital", "primidone"], "letermovir_rifamycins": ["rifabutin"], "cobicistat_inducers": ["phenobarbital"], "cobicistat_inducers_unnamed": ["primidone"], "cobicistat_efavirenz": ["efavirenz"], "ritonavir_efavirenz": ["efavirenz"], "ritonavir_rifabutin": ["rifabutin"], "cobicistat_rifabutin": ["rifabutin"], "cyclosporine_cyp3a4ind": ["phenobarbital"], "hc_inducers": ["phenobarbital", "primidone", "rifabutin"], "dmpa_im_inducers": ["phenobarbital", "primidone"], "dmpa_subq_inducers": ["phenobarbital", "primidone"], "lng_ec_efavirenz": ["efavirenz"], "lng_ec_inducers": ["phenobarbital", "primidone", "rifabutin"], "ulipristal_inducers": ["phenobarbital", "primidone"], "ulipristal_inducers_unnamed": ["efavirenz", "rifabutin"], "dolutegravir_inducers_dose": ["efavirenz"], "dolutegravir_inducers_avoid": ["phenobarbital"], "tenofovir_af_rifamycins": ["rifabutin"], "tenofovir_af_anticonvulsants": ["phenobarbital"], "rilpivirine_contraindicated": ["phenobarbital"], "rilpivirine_rifabutin": ["rifabutin"], "vemlidy_anticonvulsants": ["phenobarbital"]}};
var got={mod:{},partS:{},indS:{},mi:{}};
DDI_DATA.forEach(function(p){ var g=(p.groups||[p.drugs]), x=g[g.length-1];
  var m=M_INH.filter(function(i){return x.indexOf(i)>=0;}); if(m.length) got.mod[p.id]=m;
  var s=S_INH.filter(function(i){return x.indexOf(i)>=0;}); if(s.length&&s.length<S_INH.length) got.partS[p.id]=S_INH.filter(function(i){return x.indexOf(i)<0;});
  var si=S_IND.filter(function(i){return x.indexOf(i)>=0;}); if(si.length&&si.length<S_IND.length) got.indS[p.id]=S_IND.filter(function(i){return x.indexOf(i)<0;});
  var mi=M_IND.filter(function(i){return x.indexOf(i)>=0;}); if(mi.length) got.mi[p.id]=mi;});
[['mod','moderate CYP3A4 inhibitor where the labeling sets no moderate rule'],['partS','strong-inhibitor group incomplete'],['indS','strong-inducer group incomplete'],['mi','moderate inducer where the labeling sets no rule']].forEach(function(k){
  var a=got[k[0]], b=PIN[k[0]];
  Object.keys(a).forEach(function(id){ if(JSON.stringify(a[id])!==JSON.stringify(b[id])) R.cyp.push(id+': '+k[1]+' ('+a[id].join(',')+')');});
  Object.keys(b).forEach(function(id){ if(!a[id]&&PR[id]) R.cyp.push(id+': pinned '+k[0]+' entry no longer present');});});
// the decisions behind the owner's examples, stated explicitly
['apixaban_cyp3a4inh','rivaroxaban_cyp3a4inh','quetiapine_cyp3a4inh','lurasidone_cyp3a4inh','eplerenone_cyp3a4inh','brex_cyp3a4inh','colchicine_cyp3a4inh','gepant_cyp3a','eletriptan_cyp3a','ics_cyp3a','ticagrelor_cyp3a'].forEach(function(id){
  need(!PR[id]||!has(id,'fluconazole'),id+' fires for fluconazole, a moderate inhibitor its labeling does not cover');});
need(JSON.stringify(PR.apixaban_cyp3a4inh.groups[1].slice().sort())===JSON.stringify(['cobicistat','itraconazole','ketoconazole','ritonavir']),'apixaban rule must be the combined P-gp and strong CYP3A4 inhibitors only');
need(JSON.stringify(PR.rivaroxaban_cyp3a4inh.groups[1].slice().sort())===JSON.stringify(['cobicistat','erythromycin','itraconazole','ketoconazole'])&&has('ritonavir_rivaroxaban','ritonavir'),'rivaroxaban rule must be combined P-gp + strong inhibitors, plus erythromycin (renal impairment)');
need(PR.methadone_cyp3a4inh&&!has('methadone_cyp3a4inh','ritonavir'),'methadone_cyp3a4inh must not include ritonavir (it lowers methadone)');
need(has('dofetilide_contraindicated','verapamil')&&has('dofetilide_contraindicated','ketoconazole')&&!has('dofetilide_contraindicated','itraconazole'),'dofetilide pair must follow its contraindication list (ketoconazole, verapamil)');
// MED-01 3C-2d: the dofetilide labeling contraindicates verapamil only; diltiazem is on its list of CYP3A4 inhibitors to co-administer cautiously.
need(!has('dofetilide_contraindicated','diltiazem'),'diltiazem must not carry the dofetilide contraindication (labeling names verapamil only)');
// MED-01 3C follow-up (AA8): Moderate/A since the FDA CiPA crossover study of the pair (Vicente 2019) replaced the labeling-only basis.
need(PR.dofetilide_diltiazem&&PR.dofetilide_diltiazem.sev==='moderate'&&PR.dofetilide_diltiazem.ev==='A'&&/30447156/.test(PR.dofetilide_diltiazem.pmid)&&has('dofetilide_diltiazem','diltiazem')&&!has('dofetilide_diltiazem','verapamil'),'dofetilide_diltiazem must be Moderate/A for diltiazem only, citing the CiPA study (PMID 30447156)');
need(!DR.ccb_nondhp&&DR.verapamil&&DR.diltiazem&&DR.verapamil.sev+DR.verapamil.ev==='moderateD'&&DR.diltiazem.sev+DR.diltiazem.ev==='moderateD','verapamil and diltiazem must be separate Moderate/D records');
var VD_OK=['dofetilide_contraindicated','dofetilide_diltiazem','dabigatran_pgp','dabigatran_diltiazem','loperamide_pgp','loperamide_diltiazem','digoxin_verapamil','digoxin_diltiazem','efavirenz_diltiazem','efavirenz_ccb','quinidine_diltiazem','quinidine_verapamil'];   // MED-01 P2R2: quinidine + diltiazem measured (A), + verapamil labeling only (B)   // MED-01 WS5c: efavirenz labeling measured diltiazem only
DDI_DATA.forEach(function(p){if(VD_OK.indexOf(p.id)<0&&(has(p.id,'verapamil')!==has(p.id,'diltiazem'))) need(false,p.id+' covers only one of verapamil/diltiazem without a label reason');});
// MED-01 3C-3a (owner priority safety review, 31 Oct 2026; change log AA6)
function SG(id,sev,ev){return PR[id]&&PR[id].sev===sev&&PR[id].ev===ev;}
need(SG('digoxin_verapamil','major','A')&&has('digoxin_verapamil','digoxin')&&has('digoxin_verapamil','verapamil')&&!has('digoxin_verapamil','diltiazem'),'digoxin + verapamil must be its own Major/A pair (+50-75%, controlled human studies)');
need(SG('digoxin_diltiazem','moderate','A')&&has('digoxin_diltiazem','digoxin')&&has('digoxin_diltiazem','diltiazem')&&!has('digoxin_diltiazem','verapamil'),'digoxin + diltiazem must be its own Moderate/A pair');
need(SG('digoxin_dofetilide','moderate','B')&&has('digoxin_dofetilide','digoxin')&&has('digoxin_dofetilide','dofetilide'),'digoxin + dofetilide must be Moderate/B (labeling: higher torsades rate)');
need(!has('dabigatran_pgp','diltiazem')&&has('dabigatran_pgp','verapamil')&&SG('dabigatran_diltiazem','moderate','B'),'diltiazem must be outside dabigatran_pgp, in its own Moderate/B pair (mixed observational data)');
need(!has('loperamide_pgp','diltiazem')&&has('loperamide_pgp','verapamil')&&SG('loperamide_diltiazem','moderate','D'),'diltiazem must be outside loperamide_pgp, in its own Moderate/D pair (CYP3A4 mechanism only)');
need(DR.dofetilide&&DR.dofetilide.sev==='major'&&DR.dofetilide.ev==='D','dofetilide cannabis record must stay Major/D (a labeling precaution alone does not raise the grade)');
need(DR.dofetilide&&!/no meaningful metabolic route/i.test(DR.dofetilide.mech)&&/cannabinoids/.test(DR.dofetilide.mech)&&/What is documented/.test(DR.dofetilide.effect)&&/What is theoretical/.test(DR.dofetilide.effect),'dofetilide cannabis record must quote the labeling precaution and separate documented from theoretical effects');
only('dofetilide_label_avoid',['prochlorperazine','itraconazole','lamotrigine'],['ketoconazole','verapamil']); need(SG('dofetilide_label_avoid','major','B'),'dofetilide_label_avoid must be Major/B');
only('dofetilide_qt_antiinfective',['clarithromycin','erythromycin','azithromycin'],['fluconazole','voriconazole','posaconazole','isavuconazonium','ketoconazole','itraconazole']); need(SG('dofetilide_qt_antiinfective','major','B'),'dofetilide_qt_antiinfective (macrolides; each labeling names dofetilide) must be Major/B');
only('dofetilide_azole_qt',['fluconazole','voriconazole','posaconazole'],['isavuconazonium','ketoconazole','itraconazole','clarithromycin']); need(SG('dofetilide_azole_qt','major','D'),'dofetilide_azole_qt must be Major/D (no labeling names the pair)');
only('dofetilide_cyp3a4_caution',['ritonavir','cobicistat','nefazodone','fluvoxamine','isavuconazonium','grapefruit'],['paroxetine','diltiazem','verapamil']); need(SG('dofetilide_cyp3a4_caution','moderate','D'),'dofetilide_cyp3a4_caution must be Moderate/D (general labeling precaution only)');
// MED-01 3C follow-up (AA8): one grade rule for every dofetilide pair. A = the pair given together in a controlled human study;
// B = a labeling naming the pair with a pair-specific instruction or report; D = mechanism or a general precautionary list.
(function(){var G={dofetilide_contraindicated:'majorA',dofetilide_kdiuretic:'majorB',dofetilide_diltiazem:'moderateA',dofetilide_label_avoid:'majorB',dofetilide_qt_antiinfective:'majorB',
  dofetilide_azole_qt:'majorD',dofetilide_cyp3a4_caution:'moderateD',digoxin_dofetilide:'moderateB',dofetilide_cationic_secretion:'moderateD',antiarrhythmic_qt_named_dofetilide:'majorB',antiarrhythmic_qt_class_dofetilide:'majorD'};   // MED-01 P2R3 (A1)   // MED-01 P2R2 (C3)
  DDI_DATA.forEach(function(p){if(p.drugs.indexOf('dofetilide')>=0&&p.groups&&p.groups[0].length===1&&p.groups[0][0]==='dofetilide'||p.id==='digoxin_dofetilide') need(G[p.id],'dofetilide pair '+p.id+' has no recorded grade decision');});
  Object.keys(G).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===G[id],id+' must be '+G[id]+' under the dofetilide grade rule');});})();
// Class IA / III antiarrhythmics with macrolides and azoles: each of the 4 x 9 combinations fires exactly the decided pair (or none)
(function(){function link(a,b){return DDI_DATA.filter(function(p){var g=p.groups; return g&&g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;});}
  var E={sotalol:{clarithromycin:'sotalol_procainamide_macrolide',erythromycin:'sotalol_procainamide_macrolide',azithromycin:'classia_iii_azithromycin',fluconazole:'sotalol_procainamide_azole_qt',voriconazole:'sotalol_procainamide_azole_qt',posaconazole:'sotalol_procainamide_azole_qt',ketoconazole:'sotalol_procainamide_azole_qt',itraconazole:'',isavuconazonium:''},
    quinidine:{clarithromycin:'quinidine_clarithromycin',erythromycin:'quinidine_erythromycin',azithromycin:'classia_iii_azithromycin',fluconazole:'quinidine_azole_contraindicated',voriconazole:'quinidine_azole_contraindicated',posaconazole:'quinidine_azole_contraindicated',ketoconazole:'quinidine_azole_contraindicated',itraconazole:'quinidine_itraconazole',isavuconazonium:''},
    disopyramide:{clarithromycin:'disopyramide_macrolide',erythromycin:'disopyramide_macrolide',azithromycin:'classia_iii_azithromycin',fluconazole:'disopyramide_azole_cyp3a4',voriconazole:'disopyramide_azole_cyp3a4',posaconazole:'disopyramide_azole_cyp3a4',ketoconazole:'disopyramide_azole_contraindicated',itraconazole:'disopyramide_azole_contraindicated',isavuconazonium:''}};
  E.procainamide=E.sotalol;
  Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var got=link(a,b).join(','); need(got===E[a][b],a+' + '+b+' must fire '+(E[a][b]||'no pair')+' (fires '+(got||'none')+')');});});
  var SE={classia_iii_azithromycin:'majorB',sotalol_procainamide_macrolide:'majorB',quinidine_clarithromycin:'majorB',quinidine_erythromycin:'majorA',disopyramide_macrolide:'majorB',
    quinidine_azole_contraindicated:'majorB',quinidine_itraconazole:'majorA',disopyramide_azole_contraindicated:'majorB',disopyramide_azole_cyp3a4:'majorD',sotalol_procainamide_azole_qt:'majorD'};
  Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id]&&PR[id].source==='label',id+' must be '+SE[id]+' (labeling-based)');});
  ['quinidine_azole_contraindicated','quinidine_itraconazole','disopyramide_azole_contraindicated'].forEach(function(id){need(PR[id]&&/contraindicated/i.test(PR[id].label),id+' label must say contraindicated');});
  // MED-01 3C closure (AA9): Nuedexta has its own quinidine record and pairs; these full-dose pairs must say it is not matched.
  ['quinidine_azole_contraindicated','quinidine_itraconazole'].forEach(function(id){need(PR[id]&&/Nuedexta[^.]*is not matched here/.test(PR[id].mech)&&!/For Nuedexta/.test(PR[id].monitor),id+' must say Nuedexta is not matched here (it follows its own labeling)');});
  need(PR.quinidine_itraconazole&&/9390107/.test(PR.quinidine_itraconazole.pmid)&&PR.quinidine_erythromycin&&/10594487/.test(PR.quinidine_erythromycin.pmid),'Grade A antiarrhythmic pairs must cite their human studies');})();
// MED-01 3C closure (AA9): amiodarone with macrolides and azoles, decided pair by pair (A/B/C/D as in AA8).
(function(){function link(a,b){return DDI_DATA.filter(function(p){var g=p.groups; return g&&g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  var E={amiodarone:{clarithromycin:'amiodarone_macrolide',erythromycin:'amiodarone_macrolide',azithromycin:'amiodarone_azithromycin',fluconazole:'amiodarone_azole_reported',
    voriconazole:'amiodarone_azole_reported',itraconazole:'amiodarone_azole_reported',ketoconazole:'amiodarone_ketoconazole',posaconazole:'amiodarone_posaconazole',isavuconazonium:'amiodarone_isavuconazonium'},
    quinidine:{cobicistat:'quinidine_cobicistat',nefazodone:'quinidine_disopyramide_nefazodone',ritonavir:'ritonavir_antiarrhythmics'},
    disopyramide:{cobicistat:'disopyramide_booster',ritonavir:'disopyramide_booster',nefazodone:'quinidine_disopyramide_nefazodone'},
    // Nuedexta: its own labeling, never the full-dose quinidine pairs (azoles must not read as contraindicated)
    quinidine_nuedexta:{quinidine:'nuedexta_quinidine',ketoconazole:'nuedexta_cyp3a4_named',itraconazole:'nuedexta_cyp3a4_named',fluconazole:'nuedexta_cyp3a4_named',voriconazole:'nuedexta_cyp3a4_named',
      posaconazole:'nuedexta_cyp3a4_named',ritonavir:'nuedexta_cyp3a4_named',clarithromycin:'nuedexta_cyp3a4_named',erythromycin:'nuedexta_cyp3a4_named',nefazodone:'nuedexta_cyp3a4_ecg',
      diltiazem:'nuedexta_cyp3a4_ecg',verapamil:'nuedexta_cyp3a4_ecg',grapefruit:'nuedexta_cyp3a4_ecg',cobicistat:'nuedexta_cyp3a4_ecg',isavuconazonium:'nuedexta_isavuconazonium',
      sotalol:'nuedexta_qt_named',amiodarone:'nuedexta_qt_named',azithromycin:'nuedexta_qt_named',ziprasidone:'nuedexta_qt_named',dofetilide:'nuedexta_qt',haloperidol:'nuedexta_qt',
      desipramine:'nuedexta_desipramine',amitriptyline:'nuedexta_tca',paroxetine:'nuedexta_paroxetine',digoxin:'nuedexta_digoxin',propafenone:'nuedexta_cyp2d6',codeine:'nuedexta_cyp2d6',
      hydrochlorothiazide:'nuedexta_kdiuretic'}};   // MED-01 P2R2: restored at D (Nuedexta risk-factor statement)
  Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var got=link(a,b); need(got===E[a][b],a+' + '+b+' must fire '+(E[a][b]||'no pair')+' (fires '+(got||'none')+')');});});
  var SE={amiodarone_macrolide:'majorB',amiodarone_azithromycin:'majorB',amiodarone_azole_reported:'majorB',amiodarone_ketoconazole:'majorC',amiodarone_posaconazole:'majorD',amiodarone_isavuconazonium:'moderateD',
    quinidine_cobicistat:'majorB',disopyramide_booster:'majorB',quinidine_disopyramide_nefazodone:'majorD',
    nuedexta_quinidine:'majorB',nuedexta_cyp3a4_named:'majorB',nuedexta_cyp3a4_ecg:'moderateB',nuedexta_isavuconazonium:'moderateD',nuedexta_qt_named:'majorB',nuedexta_qt:'majorD',
    nuedexta_desipramine:'majorA',nuedexta_tca:'majorB',nuedexta_paroxetine:'moderateA',nuedexta_digoxin:'majorB',nuedexta_cyp2d6:'moderateD',linezolid_nuedexta:'majorD',nuedexta_kdiuretic:'moderateD'};   // MED-01 P2R1, P2R2
  Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id],id+' must be '+SE[id]+' (change log AA9)');});
  // every Nuedexta pair has a recorded decision, and only the quinidine pair may say contraindicated
  DDI_DATA.forEach(function(p){if(p.drugs.indexOf('quinidine_nuedexta')>=0){need(SE[p.id],'Nuedexta pair '+p.id+' has no recorded decision');
    need(p.id==='nuedexta_quinidine'||!/contraindicated\)/i.test(p.label),p.id+': Nuedexta labeling contraindicates only other quinidine here');}
    if(p.drugs.indexOf('quinidine')>=0) need(p.drugs.indexOf('quinidine_nuedexta')<0||p.id==='nuedexta_quinidine',p.id+': full-dose quinidine pair must not include Nuedexta');});
  need(PR.nuedexta_quinidine&&/contraindicated/i.test(PR.nuedexta_quinidine.label),'nuedexta_quinidine label must say contraindicated');
  var NC=DI_COMBINATIONS.nuedexta, Q=DI_DATA.filter(function(d){return d.id==='quinidine';})[0], QN=DI_DATA.filter(function(d){return d.id==='quinidine_nuedexta';})[0];
  need(NC&&NC.ids.join(',')==='dextromethorphan,quinidine_nuedexta','Nuedexta must resolve to dextromethorphan + quinidine_nuedexta');
  need(DI_ALIASES.nuedexta==='quinidine_nuedexta','the nuedexta alias must point to the low-dose record');
  need(Q&&!Q.brands.some(function(b){return /nuedexta/i.test(b);}),'the full-dose quinidine record must not claim Nuedexta');
  need(QN&&QN.brands.join()==='Nuedexta'&&QN.sev==='moderate'&&QN.ev==='D','quinidine_nuedexta must be the Nuedexta-only Moderate/D record');
  need(PR.nuedexta_paroxetine&&/22283559/.test(PR.nuedexta_paroxetine.pmid),'nuedexta_paroxetine Grade A must cite Schoedel 2012');
  need(/13 healthy volunteers/.test(PR.nuedexta_desipramine.mech)&&/three times the quinidine/.test(PR.nuedexta_desipramine.mech),'nuedexta_desipramine Grade A must describe the labeled study and its higher quinidine dose');
  need(PR.amiodarone_ketoconazole&&/18445370/.test(PR.amiodarone_ketoconazole.pmid),'amiodarone_ketoconazole Grade C must cite its laboratory study');
  need(/weeks/.test(PR.amiodarone_macrolide.monitor)&&/half-life/.test(PR.amiodarone_azole_reported.mech),'amiodarone pairs must explain the long half-life');
  // Vicente 2019 re-check: design and limits stated
  need(/double-blind randomised crossover/.test(PR.dofetilide_diltiazem.mech)&&/two stopped/.test(PR.dofetilide_diltiazem.mech)&&/0\.25&nbsp;mg against 0\.375/.test(PR.dofetilide_diltiazem.mech),'dofetilide_diltiazem must state the CiPA design, withdrawals and doses');})();
// every strong and moderate CYP3A4 inhibitor in The Table must reach dofetilide through some pair
S_INH.concat(M_INH).forEach(function(i){need(DDI_DATA.some(function(p){var g=p.groups; return g&&g.length===2&&g[0].indexOf('dofetilide')>=0&&g[1].indexOf(i)>=0;}),'dofetilide has no pair for CYP3A4 inhibitor '+i);});
need(PR.colchicine_cyp3a4mod&&PR.gepant_cyp3a4mod,'moderate-inhibitor pairs for colchicine and the gepants are missing');
// WS3 Stage 3A: gabapentin/pregabalin and the three Z-drugs are separate medicines with their own pairs
need(PR.gaba_opi&&!has('gaba_opi','pregabalin')&&PR.pregabalin_opi&&PR.pregabalin_opi.sev==='major','gabapentin and pregabalin must each have their own opioid pair (Major)');
need(PR.gabapentinoid_dup&&has('gabapentinoid_dup','gabapentin')&&has('gabapentinoid_dup','pregabalin'),'gabapentin + pregabalin duplicate-therapy pair missing');
need(PR.pregabalin_benz&&PR.pregabalin_pioglitazone&&PR.pregabalin_acei,'pregabalin label pairs (benzodiazepines, pioglitazone, ACE inhibitors) missing');
need(PR.zdrug_opi&&has('zdrug_opi','zaleplon')&&has('zdrug_opi','eszopiclone')&&!has('zdrug_opi','zolpidem')&&PR.zolp_opi,'zaleplon/eszopiclone opioid pair missing or merged with zolpidem');
need(PR.eszopiclone_cyp3a4inh&&PR.eszopiclone_cyp3a4ind&&PR.zaleplon_cyp3a4ind&&PR.zaleplon_cimetidine,'Z-drug metabolism pairs missing');
need(!DDI_DATA.some(function(p){return /^zaleplon_cyp3a4inh$/.test(p.id);}),'zaleplon must not get a strong-inhibitor pair (CYP3A4 is a minor route; erythromycin +20%)');
['carisoprodol_cns','promethazine_cns','hydroxyzine_cns'].forEach(function(id){need(hasF(id,'zolpidem')&&hasF(id,'zaleplon')&&hasF(id,'eszopiclone'),id+' must cover all three Z-drugs');});
['gabapentin','pregabalin','zolpidem','zaleplon','eszopiclone'].forEach(function(id){need(DR[id],'record '+id+' missing');});
if(DR.gabapentin) need(!/pregabalin|lyrica/i.test(DR.gabapentin.drug+DR.gabapentin.brands.join(' ')),'gabapentin record names pregabalin again');
if(DR.zolpidem) need(!/sonata|lunesta|zaleplon|eszopiclone/i.test(DR.zolpidem.brands.join(' ')),'zolpidem record lists another Z-drug');
// WS3 clinical check: posaconazole and voriconazole with apixaban/rivaroxaban (human PK, PMID 34273071)
need(PR.apixaban_posaconazole&&PR.apixaban_posaconazole.sev==='moderate'&&PR.apixaban_posaconazole.ev==='A','apixaban + posaconazole must be Moderate/A (human PK 1.62-fold)');
need(PR.rivaroxaban_posaconazole&&PR.rivaroxaban_posaconazole.sev==='moderate'&&PR.rivaroxaban_posaconazole.ev==='A','rivaroxaban + posaconazole must be Moderate/A (human PK 1.37-fold)');
need(PR.doac_voriconazole&&PR.doac_voriconazole.sev==='minor'&&PR.doac_voriconazole.ev==='A'&&JSON.stringify(PR.doac_voriconazole.groups)==='[["apixaban","rivaroxaban"],["voriconazole"]]','apixaban/rivaroxaban + voriconazole must be one Minor/A pair');
// MED-01 Stage 3B: class records split into medications; each grade is the medication's own (no class copy), each pair names only
//    the members its labeling or text supports. Grades below were assigned 31 Oct 2026 from pair-specific evidence (change log Z).
['opioids','benzos','ssri','snri','statins','betablockers'].forEach(function(c){need(!DR[c],'class record '+c+' is back: medications must keep their own records');
  Object.keys(DI_ALIASES).forEach(function(a){need(DI_ALIASES[a]!==c,'alias '+a+' points at the removed class record '+c);});
  DDI_DATA.forEach(function(p){need(p.drugs.indexOf(c)<0,p.id+' still names the class record '+c);});});
var G3B={"oxycodone": "A", "morphine": "A", "hydromorphone": "A", "oxymorphone": "B", "fentanyl": "A", "buprenorphine": "A", "hydrocodone": "D", "codeine": "D", "tapentadol": "D", "meperidine": "D", "midazolam": "A", "diazepam": "A", "alprazolam": "D", "triazolam": "D", "clonazepam": "D", "chlordiazepoxide": "D", "clorazepate": "D", "estazolam": "D", "flurazepam": "D", "quazepam": "D", "lorazepam": "D", "oxazepam": "D", "temazepam": "D", "citalopram": "A", "escitalopram": "B", "sertraline": "B", "fluoxetine": "B", "paroxetine": "D", "duloxetine": "D", "venlafaxine": "D", "desvenlafaxine": "D", "milnacipran": "D", "levomilnacipran": "D", "simvastatin": "D", "lovastatin": "D", "atorvastatin": "D", "metoprolol": "D", "atenolol": "D", "bisoprolol": "D", "nebivolol": "D", "nadolol": "D", "propranolol": "B"};
Object.keys(G3B).forEach(function(id){need(DR[id]&&DR[id].ev===G3B[id]&&DR[id].sev==='moderate',id+' must be its own Moderate/'+G3B[id]+' record');});
function only(pid,yes,no){need(PR[pid],pid+' missing'); if(!PR[pid])return; yes.forEach(function(x){need(hasF(pid,x),pid+' must cover '+x);}); no.forEach(function(x){need(!has(pid,x),pid+' must not cover '+x);});}
var OP3B=['oxycodone','hydrocodone','morphine','hydromorphone','oxymorphone','fentanyl','buprenorphine','codeine','tapentadol','meperidine'];
only('opioids_cyp3a4inh',['oxycodone','hydrocodone','fentanyl','codeine','meperidine'],['morphine','hydromorphone','oxymorphone','tapentadol','buprenorphine']);
only('buprenorphine_cyp3a4inh',['buprenorphine','ketoconazole'],['oxycodone']); need(PR.buprenorphine_cyp3a4inh&&PR.buprenorphine_cyp3a4inh.sev==='moderate','buprenorphine + CYP3A4 inhibitors is Moderate (no boxed warning)');
only('opioids_cyp3a4ind',['buprenorphine','codeine','meperidine'],['morphine','hydromorphone','oxymorphone','tapentadol']);
only('benzos_cyp3a4inh',['alprazolam','triazolam','midazolam','estazolam'],['diazepam','clonazepam','lorazepam','oxazepam','temazepam']);
only('benzos_cyp3a4inh_partial',['diazepam','clonazepam','chlordiazepoxide','clorazepate','flurazepam','quazepam'],['alprazolam','lorazepam','ritonavir']);
only('ritonavir_benzos',['triazolam','midazolam','alprazolam'],['diazepam','lorazepam']);
only('ritonavir_benzos_dose',['diazepam','clonazepam','clorazepate','estazolam','flurazepam'],['alprazolam','triazolam','midazolam','lorazepam','oxazepam','temazepam']);
only('brex_ssri',['fluoxetine','paroxetine'],['sertraline','citalopram','escitalopram']);
only('antiarrhythmic_qt',['citalopram','escitalopram','sertraline','fluoxetine'],['paroxetine']);
only('ssri_nsaid',['sertraline','duloxetine','venlafaxine'],[]);
only('statins_cyp3a4inh',['simvastatin','lovastatin'],['atorvastatin']); only('atorvastatin_cyp3a4inh',['atorvastatin','clarithromycin'],['simvastatin']);
only('clari_statin',['simvastatin','lovastatin'],['atorvastatin']); only('statin_amiodarone',['simvastatin','lovastatin'],['atorvastatin']);
only('statins_ccb',['simvastatin','amlodipine','nifedipine','felodipine'],['lovastatin','atorvastatin']); only('lovastatin_ccb_nondhp',['lovastatin','verapamil','diltiazem'],['amlodipine']);
only('ritonavir_statins',['simvastatin','lovastatin'],['atorvastatin','rosuvastatin']); only('ritonavir_statins_dose',['atorvastatin'],['simvastatin','rosuvastatin']);   // MED-01 P2R2: rosuvastatin measured, own pair
['opi_benz','alc_opi','gaba_opi','pregabalin_opi','naltrexone_opioids'].forEach(function(pid){OP3B.forEach(function(x){need(has(pid,x),pid+' lost '+x);});});
need(PR.apixaban_isavuconazonium&&PR.apixaban_isavuconazonium.sev==='minor'&&PR.apixaban_isavuconazonium.ev==='A'&&!has('apixaban_isavuconazonium','rivaroxaban'),'apixaban + isavuconazonium must be Minor/A, apixaban only (rivaroxaban unchanged in the study)');
// MED-01 Stage 3B follow-up (31 Oct 2026): remaining grouped records split; label-based safety corrections (change log Z8-Z11).
var G3F={"naproxen": "D", "ibuprofen": "D", "omeprazole": "A", "esomeprazole": "D", "lansoprazole": "D", "dexlansoprazole": "D", "pantoprazole": "D", "rabeprazole": "D", "nizatidine": "D", "sildenafil": "B", "tadalafil": "D", "vardenafil": "D", "avanafil": "D", "glipizide": "D", "glimepiride": "D", "glyburide": "D", "tolbutamide": "D", "chlorpropamide": "D", "gliclazide": "D", "sumatriptan": "D", "rizatriptan": "D", "zolmitriptan": "D", "naratriptan": "D", "frovatriptan": "D", "almotriptan": "D"};
Object.keys(G3F).forEach(function(id){need(DR[id]&&DR[id].ev===G3F[id]&&DR[id].drug.indexOf('/')<0,id+' must be its own single-medicine record graded '+G3F[id]);});
['ibuprofen','omeprazole','pantoprazole','sildenafil','glipizide'].forEach(function(id){need(DR[id]&&DR[id].drug.toLowerCase()===id,id+' record must name only '+id);});
need(!DR.triptans,'grouped triptans record is back');
['nizatidine','axid','lansoprazole','nexium','cialis','stendra','amaryl','tolbutamide','glyburide'].forEach(function(a){need(!DI_ALIASES[a],'alias '+a+' must not point at another medicine');});
only('atorvastatin_posaconazole',['atorvastatin','posaconazole'],[]); need(PR.atorvastatin_posaconazole&&PR.atorvastatin_posaconazole.sev==='major','atorvastatin + posaconazole is contraindicated by the Noxafil labeling (Major)');
only('atorvastatin_cyp3a4inh',['atorvastatin'],['posaconazole']);
only('rifampin_morphine_hydromorphone',['morphine','hydromorphone','rifampin'],['oxycodone']);
only('avanafil_cyp3a4inh',['avanafil','ketoconazole'],['sildenafil']); need(PR.avanafil_cyp3a4inh&&PR.avanafil_cyp3a4inh.sev==='major','avanafil + strong CYP3A4 inhibitors is Major (labeling: do not use)');
['sildenafil','tadalafil','vardenafil'].forEach(function(x){need(hasFam('pde5_cyp3a4inh',x),'pde5_cyp3a4inh family must cover '+x);}); need(!hasFam('pde5_cyp3a4inh','avanafil'),'avanafil has its own pairs'); only('avanafil_cyp3a4mod',['avanafil','erythromycin'],[]);
only('vardenafil_antiarrhythmic',['vardenafil','sotalol','quinidine'],['sildenafil']);
only('sulfonylurea_fluconazole',['glipizide','glyburide','tolbutamide','fluconazole'],[]);
only('rizatriptan_propranolol',['rizatriptan','propranolol'],[]); only('zolmitriptan_cimetidine',['zolmitriptan','cimetidine'],[]); only('almotriptan_cyp3a4inh',['almotriptan','ketoconazole'],['sumatriptan']);
only('clopidogrel_ppi',['omeprazole','esomeprazole'],['pantoprazole','lansoprazole','rabeprazole']);
only('mycophenolate_ppi',['omeprazole','esomeprazole','lansoprazole','dexlansoprazole','pantoprazole','rabeprazole'],[]);
['warf_ibu','lithium_ibu','pred_nsaid','ssri_nsaid','triple_whammy','dabigatran_nsaid'].forEach(function(pid){need(has(pid,'naproxen')&&has(pid,'ibuprofen'),pid+' must cover ibuprofen and naproxen');});
['nitro_sild','ritonavir_pde5','sild_antihyp'].forEach(function(pid){['sildenafil','tadalafil','vardenafil','avanafil'].forEach(function(x){need(has(pid,x),pid+' lost '+x);});});
only('rasagiline_serotonergic',['meperidine','tramadol','methadone','tapentadol'],['oxycodone','morphine']);
only('rasagiline_opioids',['oxycodone','morphine','fentanyl','buprenorphine'],['meperidine']); need(PR.rasagiline_opioids&&PR.rasagiline_opioids.sev==='major','rasagiline + opioids stays Major (opioid labelings: not recommended with MAOIs)');
only('rasagiline_stimulants',['methylphenidate','dexmethylphenidate','amphetamine','dextroamphetamine','lisdexamfetamine'],['pseudoephedrine']); only('rasagiline_decongestants',['pseudoephedrine','phenylephrine'],['methylphenidate','amphetamine']); need(PR.rasagiline_stimulants&&PR.rasagiline_stimulants.sev==='major'&&PR.rasagiline_decongestants&&PR.rasagiline_decongestants.sev==='major','rasagiline + stimulants and + decongestants are Major (their own labelings: contraindicated / do not use with MAOIs)'); only('rasagiline_dextromethorphan',['dextromethorphan'],[]);
need(PR.ssri_decon&&PR.ssri_decon.sev==='minor'&&PR.ssri_decon.ev==='D'&&!/serotonergic effect of decongestants/.test(PR.ssri_decon.label),'ssri_decon is Minor/D with the corrected mechanism');
// MED-01 Stage 3C-1: product families never open a single profile; decongestants are two medicines.
need(!DR.decongestants,'grouped decongestant record is back');
['nyquil','dayquil','theraflu','coricidin','coricidin hbp','midol','maalox','mylanta','sudafed','excedrin','pamprin'].forEach(function(f){need(DI_AMBIGUOUS_FAMILY[f.split(' ')[0]],'no family note for '+f);});
['nyquil','dayquil','theraflu','coricidin','coricidin hbp','midol','nyquil (sleep component)','excedrin','pamprin','pamprin ib','mylanta','maalox'].forEach(function(f){DI_DATA.forEach(function(d){need((d.brands||[]).map(function(b){return String(b).toLowerCase();}).indexOf(f)<0,d.id+' lists the family name "'+f+'" as its own brand');});});
need(diFamilyCombos('excedrin').length===5&&diFamilyCombos('pamprin').length===3&&diFamilyCombos('mylanta').length===3&&diFamilyCombos('nyquil').length===6&&diFamilyCombos('dayquil').length===2&&diFamilyCombos('coricidin').length===5&&diFamilyCombos('midol').length===4,'family formulation choices missing');
need(diFamilyCombos('theraflu').length===0,'Theraflu must not offer guessed formulations');
need(diComboFor('NyQuil Cold & Flu')===DI_COMBINATIONS['nyquil cold']&&diComboFor('nyquil cold and flu')===DI_COMBINATIONS['nyquil cold'],'NyQuil Cold & Flu must resolve with "&" or "and"');
need(diComboFor('Midol Complete Caffeine Free')===DI_COMBINATIONS['midol complete caffeine free'],'Midol Complete Caffeine Free shadowed by Midol Complete');
['antihyp_decon','caf_decon','alb_decon','ssri_decon'].forEach(function(pid){need(has(pid,'pseudoephedrine')&&has(pid,'phenylephrine'),pid+' must cover both decongestants');});
need(DR.chlorpheniramine&&DR.chlorpheniramine.ev==='D'&&DR.pyrilamine&&DR.pyrilamine.ev==='D','chlorpheniramine and pyrilamine records (Moderate/D, own inference)');
// MED-01 Stage 3C-2a: antacids are four salts; ADHD stimulants are five medicines; Excedrin PM contains aspirin; antihistamine CNS pairs.
need(!DR.antacids&&!DR.stimulants_adhd,'a grouped antacid or ADHD-stimulant record is back');
var ANT3=['calcium_carbonate','aluminum_hydroxide','magnesium_hydroxide','magnesium_carbonate'];
ANT3.forEach(function(x){need(DR[x]&&DR[x].sev==='minor'&&DR[x].ev==='D','antacid record '+x+' missing or regraded'); need(hasF('antacid_absorption',x),'antacid_absorption lost '+x); need(has('antacid_chelation',x),'antacid_chelation lost '+x);});
['tetracycline','doxycycline','minocycline','ciprofloxacin','levofloxacin','moxifloxacin'].forEach(function(x){need(has('antacid_chelation',x),'antacid_chelation lost '+x);});
need(PR.antacid_chelation&&PR.antacid_chelation.sev==='moderate'&&PR.antacid_chelation.ev==='B','antacid_chelation must be Moderate/B (antibiotic labeling)');
// MED-01 3C-2c: the two medicines actually given in the controlled studies are Grade A; the others were not tested themselves and stay B.
var ST3={methylphenidate:['25175495','A'],dexmethylphenidate:['25175495','B'],amphetamine:['782773','B'],dextroamphetamine:['782773','A'],lisdexamfetamine:['782773','B']};
Object.keys(ST3).forEach(function(x){need(DR[x]&&DR[x].sev==='moderate'&&DR[x].ev===ST3[x][1]&&DR[x].pmid.indexOf(ST3[x][0])>=0,x+' must be its own Moderate/'+ST3[x][1]+' record citing PMID '+ST3[x][0]);});
need(DR.methylphenidate&&!/amphetamine|adderall|vyvanse/i.test(DR.methylphenidate.brands.join(' ')),'methylphenidate record lists an amphetamine brand');
need(DR.amphetamine&&!/ritalin|concerta|focalin|vyvanse/i.test(DR.amphetamine.brands.join(' ')),'amphetamine record lists another stimulant');
need(DR.aspirin&&DR.aspirin.brands.indexOf('Excedrin PM')>=0,'Excedrin PM contains aspirin (current labeling)');
need(DR.ibuprofen&&!/pamprin/i.test(DR.ibuprofen.brands.join(' ')),'no US Pamprin product contains ibuprofen');
['chlorpheniramine_cns','pyrilamine_cns'].forEach(function(id){need(PR[id]&&PR[id].sev==='moderate'&&PR[id].ev==='B'&&PR[id.replace('_cns','_opioids')]&&PR[id.replace('_cns','_opioids')].ev==='D',id+' must be Moderate/B and its opioid split Moderate/D (AC3)');
  ['oxycodone','fentanyl','alprazolam','lorazepam','alcohol','zolpidem'].forEach(function(x){need(hasF(id,x),id+' lost '+x);});});
['benz_diphen'].forEach(function(id){need(PR[id]&&PR[id].sev==='moderate'&&PR[id].ev==='B'&&PR[id].source==='label',id+' must be Moderate/B on labeling (MED-01 3C-2c; no pair-specific laboratory evidence exists for C)');});
need(diClassMatches('adhd stimulants').length===5&&diClassMatches('antacids').length>=4,'class search lost the split ADHD stimulants or antacids');
// MED-01 Stage 3C-2b: grouped records split into one record per medicine; severity, grade and pair coverage unchanged.
var SPL={semaglutide:['minor','D',['semaglutide','liraglutide','dulaglutide','exenatide','lixisenatide','tirzepatide']],
 cetirizine:['minor','D',['cetirizine','levocetirizine','loratadine','desloratadine','fexofenadine']],
 arb:['minor','D',['losartan','valsartan','olmesartan','irbesartan','candesartan','telmisartan','azilsartan']],
 lisinopril:['minor','D',['lisinopril','enalapril','ramipril','benazepril','quinapril','captopril','fosinopril','moexipril','perindopril','trandolapril']],
 ccb:['moderate','D',['amlodipine','nifedipine','felodipine','nicardipine','isradipine']],
 hydrochlorothiazide:['minor','D',['hydrochlorothiazide','chlorthalidone','indapamide','metolazone']],
 prednisone:['moderate','D',['prednisone','prednisolone','methylprednisolone','dexamethasone','hydrocortisone','cortisone','betamethasone']],
 tamsulosin:['moderate','D',['tamsulosin','alfuzosin','silodosin']]};
need(!DR.arb&&!DR.ccb,'a grouped ARB or calcium channel blocker record is back');
// MED-01 Stage 3C-2c: last grouped records split; a grade follows the medicine actually studied (change log AA4).
['tricyclics','topiramate_oxc','antihypertensives','inhaled_steroids','oph_prostaglandin','oph_cai'].forEach(function(x){need(!DR[x],'grouped record '+x+' is back');});
var S2C={amitriptyline:'moderate/A',nortriptyline:'moderate/B',imipramine:'moderate/D',desipramine:'moderate/D',clomipramine:'moderate/D',doxepin:'moderate/D',trimipramine:'moderate/D',protriptyline:'moderate/D',
 topiramate:'moderate/B',oxcarbazepine:'minor/B',eslicarbazepine:'minor/B',
 clonidine:'moderate/D',hydralazine:'moderate/D',doxazosin:'moderate/D',prazosin:'moderate/D',terazosin:'moderate/D',labetalol:'moderate/D',
 fluticasone_propionate:'minor/D',fluticasone_furoate:'minor/D',budesonide_inhaled:'minor/D',mometasone_inhaled:'minor/D',ciclesonide_inhaled:'minor/D',
 oph_timolol:'minor/D',oph_betaxolol:'minor/D',oph_latanoprost:'minor/D',oph_bimatoprost:'minor/D',oph_travoprost:'minor/D',oph_tafluprost:'minor/D',oph_latanoprostene:'minor/D',oph_omidenepag:'minor/D',
 oph_brimonidine:'minor/D',oph_apraclonidine:'minor/D',oph_dorzolamide:'minor/D',oph_brinzolamide:'minor/D'};
Object.keys(S2C).forEach(function(x){need(DR[x]&&DR[x].sev+'/'+DR[x].ev===S2C[x],x+' must be its own '+S2C[x]+' record');need(DR[x]&&!/ \/ /.test(DR[x].drug),x+' record is titled as a group again');});
need(DR.amitriptyline&&DR.amitriptyline.pmid.indexOf('41398552')>=0&&DR.nortriptyline&&DR.nortriptyline.pmid.indexOf('6303138')>=0,'tricyclic grades must cite the study of that medicine');
Object.keys(S2C).forEach(function(x){if(S2C[x].slice(-1)==='D'&&DR[x]) need(!/41398552/.test(DR[x].pmid)&&!/Graded [ABC]/.test(DR[x].mech),x+' borrows another medicine\'s grade');});
need(DR.oxcarbazepine&&/37593907/.test(DR.oxcarbazepine.pmid)&&!/28782097.*topiramate/.test(DR.oxcarbazepine.mech),'oxcarbazepine must rest on its own measurement');
var TCA=['amitriptyline','nortriptyline','imipramine','desipramine','clomipramine','doxepin','trimipramine','protriptyline'];
TCA.forEach(function(x){['antiarrhythmic_qt','laba_tricyclics','lama_anticholinergics'].forEach(function(p){need(hasF(p,x),p+' lost '+x);});});
['clonidine','hydralazine','doxazosin','prazosin','terazosin','labetalol'].forEach(function(x){need(has('antihyp_decon',x)&&(has('sild_antihyp',x)||has('pde5_alpha_blockers',x)),'antihypertensive pairs lost '+x);need(!has('spiro_ace',x),'spiro_ace (hyperkalaemia) fires for '+x);});
['fluticasone_propionate','fluticasone_furoate','budesonide_inhaled','mometasone_inhaled','ciclesonide_inhaled'].forEach(function(x){need(hasF('ics_cyp3a',x),'ics_cyp3a lost '+x);});
['oph_timolol','oph_betaxolol'].forEach(function(x){need(has('donepezil_bradycardia',x)&&has('laba_betablockers',x),'beta-blocker eye-drop pairs lost '+x);});
need(diClassMatches('tricyclics').length===8,'class search "tricyclics" must reach the 8 tricyclics');
need(diClassMatches('inhaled steroids').length===6&&diClassMatches('steroid inhaler').length===6,'class search "inhaled steroids" must reach the 6 inhaled corticosteroids');
need(diClassMatches('antihypertensives').length>=6,'class search "antihypertensives" lost the split records');
need(diClassMatches('alpha blockers').length>=6&&diClassMatches('alpha blocker').length>=6,'class search "alpha blockers" must reach the alpha-1 blockers');
var SPLX={losartan:['minor','A']};   // MED-01 3C-2c: losartan graded A on the controlled PK study of the pair (PMID 37313955)
Object.keys(SPL).forEach(function(g){SPL[g][2].forEach(function(x){var w=SPLX[x]||SPL[g]; need(DR[x]&&DR[x].sev===w[0]&&DR[x].ev===w[1],x+' must be its own '+w[0]+'/'+w[1]+' record');
  need(DR[x]&&!/ \/ /.test(DR[x].drug),x+' record is titled as a group again');});});
SPL.lisinopril[2].forEach(function(x){['ace_arb','spiro_lisinopril','sacubitril_acei','potassium_raas','triple_whammy','lithium_acearb'].forEach(function(p){need(has(p,x),p+' lost '+x);});});
SPL.arb[2].forEach(function(x){['ace_arb','spiro_ace','potassium_raas','triple_whammy','lithium_acearb','ksparing_raas'].forEach(function(p){need(has(p,x),p+' lost '+x);});});
SPL.ccb[2].forEach(function(x){['ccb_cyp3a4inh','ccb_cyp3a4ind','statins_ccb'].forEach(function(p){need(hasF(p,x),p+' lost '+x);});});
SPL.hydrochlorothiazide[2].forEach(function(x){['lithium_thiazide','triple_whammy','antiarrhythmic_diuretic'].forEach(function(p){need(has(p,x),p+' lost '+x);});});
// MED-01 3C-2c: the dofetilide labeling contraindicates hydrochlorothiazide only; the other potassium-losing diuretics carry its hypokalaemia/torsades warning.
need(has('dofetilide_contraindicated','hydrochlorothiazide'),'dofetilide_contraindicated lost hydrochlorothiazide');
need(PR.dofetilide_kdiuretic&&PR.dofetilide_kdiuretic.sev==='major'&&PR.dofetilide_kdiuretic.ev==='B','dofetilide_kdiuretic must be Major/B (dofetilide labeling)');
['chlorthalidone','indapamide','metolazone','furosemide'].forEach(function(x){need(has('dofetilide_kdiuretic',x)&&!has('dofetilide_contraindicated',x),x+' must carry the dofetilide diuretic warning, not the contraindication');});
SPL.prednisone[2].forEach(function(x){need(has('pred_nsaid',x),'pred_nsaid lost '+x);});
SPL.tamsulosin[2].forEach(function(x){need(has('alpha1_cyp3a4inh',x)&&has('ritonavir_alpha1',x),'alpha-1 blocker CYP3A4 pairs lost '+x);});
need(DR.tirzepatide&&/GIP/.test(DR.tirzepatide.cls),'tirzepatide must be its own GIP/GLP-1 record');
need(DR.irbesartan&&/CYP2C9/.test(DR.irbesartan.mech)&&DR.olmesartan&&/not metabolised by cytochrome/.test(DR.olmesartan.mech),'ARB metabolism text must follow each label');
need(DR.losartan&&!/also covers valsartan/.test(DR.losartan.mech),'losartan record still describes itself as a group');
need(DR.dexamethasone&&!/a large effect is unlikely/.test(DR.dexamethasone.effect),'dexamethasone carries the prednisone-specific reassurance');
need(diClassMatches('calcium channel blockers').length===7&&diClassMatches('arb').length===7&&diClassMatches('ace inhibitors').length===10&&diClassMatches('corticosteroids').length>=7,'class search no longer reaches every split medicine');
// MED-01 3C-3b (owner search priorities, change log AA7)
function CM(q){return diClassMatches(q).map(function(d){return d.id;});}
need(CM('ppi').length===6&&CM('ppis').length===6&&CM('ccbs').length===7&&CM('benzos').length===CM('benzodiazepines').length&&CM('tcas').length===8,'class abbreviations (ppi, ccbs, benzos, tcas) must reach their classes');
['warfarin','apixaban','rivaroxaban','dabigatran'].forEach(function(x){need(CM('doacs').indexOf(x)>=0===(x!=='warfarin')&&CM('anticoagulants').indexOf(x)>=0&&CM('blood thinners').indexOf(x)>=0,'anticoagulant/DOAC/blood thinner class search wrong for '+x);});
['aspirin','clopidogrel','ticagrelor'].forEach(function(x){need(CM('blood thinners').indexOf(x)>=0&&CM('anticoagulants').indexOf(x)<0,'blood thinners must include antiplatelet '+x+' (and anticoagulants must not)');});
['lamotrigine','levetiracetam','topiramate','gabapentin','carbamazepine'].forEach(function(x){need(CM('antiepileptics').indexOf(x)>=0&&CM('anticonvulsants').indexOf(x)>=0,'antiseizure class search misses '+x);});
need(CM('steroids').indexOf('prednisone')>=0&&CM('corticosteroids').indexOf('dexamethasone')>=0&&diClassFirst('steroids',diSearchIndices('steroids'))&&diClassFirst('corticosteroids',diSearchIndices('corticosteroids')),'"steroids" must show the corticosteroid class, not one record');
need(!diClassFirst('prednisone',diSearchIndices('prednisone'))&&!diClassFirst('flonase',diSearchIndices('flonase')),'a medication name must never be replaced by its class list');
// formulation/salt/dose words never lead to a different medicine, and never rescue a non-name
Object.keys(owners).forEach(function(n){['xl','er','25 mg','hydrochloride'].forEach(function(w){var r=diSearchIndices(n+' '+w); if(r.via) r.idx.forEach(function(i){if(!owners[n][DI_DATA[i].id]&&!owners[r.via]) need(false,'"'+n+' '+w+'" reaches '+DI_DATA[i].id);});});});
['detrol la','xyzzy er','tylenol extra strength','gabapen er'].forEach(function(q){need(!diSearchIndices(q).via,'"'+q+'" must not be rescued by formulation stripping');});
Object.keys(DI_SEARCH_FIX).forEach(function(k){need(!diSearchRaw(k).idx.length,'misspelling '+k+' is itself a medication name prefix');});
need(!diSearchIndices('calcium channel blockers').idx.length,'a record title shadows the calcium channel blocker class search');
// 3C-2b items 6-7: extended-release names resolve; FDA-discontinued brand names stay searchable and carry a note (verified list only).
need(typeof DI_HISTORICAL_BRANDS==='object'&&Object.keys(DI_HISTORICAL_BRANDS).length===48,'discontinued-brand list changed without review (expected 48: 28 + 13 in 3C-3b + Sustiva in WS5a + Luvox CR in P2R3 + Ortho Evra and Implanon in P3-1a + Razadyne, Razadyne ER and Hytrin in MED-02)');
Object.keys(DI_HISTORICAL_BRANDS||{}).forEach(function(b){var r=diSearchIndices(b),ex=r.idx.filter(function(i){return r.exact[i];});
  if(b!=='zantac'&&b!=='implanon') need(ex.length===1,'discontinued brand '+b+' must resolve to exactly one medicine'); need(diAmbiguityNote(b)===DI_HISTORICAL_BRANDS[b],'discontinued brand '+b+' lost its note');});
['wellbutrin','luvox','prozac','flomax','protonix','zantac 360','procardia xl','depakote er'].forEach(function(b){need(!diAmbiguityNote(b)||!/discontinued brand/.test(diAmbiguityNote(b)),b+' is still marketed but is marked discontinued');});
[['xtampza er','oxycodone'],['zohydro er','hydrocodone'],['depakote er','valproate'],['tegretol xr','carbamazepine'],['opana er','oxymorphone']].forEach(function(p){var r=diSearchIndices(p[0]);
  need(r.idx.some(function(i){return r.exact[i]&&DI_DATA[i].id===p[1];}),p[0]+' must open '+p[1]);});
['tacrolimus','sirolimus','everolimus','cyclosporine','apixaban','rivaroxaban','opioids','methadone','tramadol','aripiprazole','cariprazine','ivabradine'].forEach(function(d){
  need(PR[d+'_cyp3a4inh']&&PR[d+'_cyp3a4ind'],'missing CYP3A4 inhibitor or inducer pair for '+d);});
if(DR.ritonavir) DR.ritonavir.brands.forEach(function(x){ if(/^(lopinavir|nirmatrelvir)$/i.test(x)) R.combo.push('ritonavir record lists "'+x+'" as if it were ritonavir');});
// MED-01 Phase 4 dofetilide decisions (AE §5): two independent integrase pairs; A5 wording (Major/D provisional).
(function(){var d=PR.dolutegravir_dofetilide, b=PR.bictegravir_dofetilide, D=DR.dofetilide;
  need(!PR.insti_dofetilide,'insti_dofetilide was split (owner decision)');
  need(d&&/Tivicay/.test(d.mech)&&/dolutegravir and megestrol should not be used in patients on TIKOSYN/.test(d.mech)&&/79%/.test(d.mech),'dolutegravir_dofetilide must cite Tivicay §4 and the Tikosyn contraindication');
  need(b&&/Biktarvy labeling says Biktarvy is contraindicated with dofetilide/.test(b.mech)&&/Tikosyn labeling does not name bictegravir/.test(b.mech)&&/39%/.test(b.mech),'bictegravir_dofetilide must cite Biktarvy §4 only and say Tikosyn does not name bictegravir');
  [d,b].forEach(function(p){need(p&&/No direct human study/.test(p.mech)&&/indirect evidence of transporter inhibition, not measured dofetilide exposure/.test(p.mech),(p?p.id:'pair')+' must state the absence of direct data and the indirect metformin evidence');});
  need(D&&D.sev+D.ev==='majorD'&&/theoretical and precautionary/.test(D.mech)&&/None of these shows an interaction/.test(D.mech)&&/future focused clinical reassessment/.test(D.mech)&&!/has been shown to raise dofetilide/i.test(D.mech),'A5 wording: Major/D kept, theoretical, no demonstrated interaction, flagged for reassessment');
  need(PR.dofetilide_cyp3a4_caution&&PR.dofetilide_cyp3a4_caution.sev+PR.dofetilide_cyp3a4_caution.ev==='moderateD','dofetilide_cyp3a4_caution unchanged');
})();
// MED-01 Phase 4 (AE): grade linter, split families, rilpivirine QT, steroid route notes, suggestion-row CSS.
(function(){var SPLIT={"alpha1_cyp3a4inh": [[["tamsulosin", "alfuzosin", "silodosin"], ["ketoconazole"]], "majorA"], "alpha1_cyp3a4inh_class_alfuzosin": [[["alfuzosin"], ["voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"]], "majorD"], "alpha1_cyp3a4inh_class_silodosin": [[["silodosin"], ["voriconazole", "posaconazole", "cobicistat", "nefazodone"]], "majorD"], "alpha1_cyp3a4inh_class_tamsulosin": [[["tamsulosin"], ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"]], "majorD"], "alpha1_cyp3a4inh_named_clarithromycin": [[["silodosin"], ["clarithromycin"]], "majorB"], "alpha1_cyp3a4inh_named_itraconazole": [[["alfuzosin", "silodosin"], ["itraconazole"]], "majorB"], "buspirone_cyp3a4inh": [[["buspirone"], ["itraconazole", "nefazodone"]], "moderateA"], "buspirone_cyp3a4inh_class": [[["buspirone"], ["voriconazole", "posaconazole", "clarithromycin", "cobicistat"]], "moderateD"], "buspirone_cyp3a4inh_named": [[["buspirone"], ["ketoconazole", "ritonavir"]], "moderateB"], "buspirone_cyp3a4ind": [[["buspirone"], ["rifampin"]], "moderateA"], "buspirone_cyp3a4ind_class": [[["buspirone"], ["rifapentine", "stjohnswort", "apalutamide", "enzalutamide"]], "moderateD"], "buspirone_cyp3a4ind_named": [[["buspirone"], ["carbamazepine", "phenytoin"]], "moderateB"], "zolpidem_cyp3a4inh": [[["zolpidem"], ["ketoconazole"]], "moderateA"], "zolpidem_cyp3a4inh_class": [[["zolpidem"], ["voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"]], "moderateD"], "zolpidem_cyp3a4inh_itraconazole": [[["zolpidem"], ["itraconazole"]], "minorA"], "eszopiclone_cyp3a4inh": [[["eszopiclone"], ["ketoconazole"]], "moderateA"], "eszopiclone_cyp3a4inh_class": [[["eszopiclone"], ["voriconazole", "posaconazole", "cobicistat"]], "moderateD"], "eszopiclone_cyp3a4inh_named": [[["eszopiclone"], ["itraconazole", "clarithromycin", "nefazodone", "ritonavir"]], "moderateB"], "pde5_cyp3a4inh": [[["tadalafil", "vardenafil"], ["ketoconazole"]], "moderateA"], "pde5_cyp3a4inh_class": [[["sildenafil", "tadalafil"], ["voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone"]], "moderateD"], "pde5_cyp3a4inh_class_vardenafil": [[["vardenafil"], ["voriconazole", "posaconazole", "nefazodone"]], "moderateD"], "pde5_cyp3a4inh_erythromycin": [[["sildenafil", "vardenafil"], ["erythromycin"]], "moderateA"], "pde5_cyp3a4inh_named_sildenafil": [[["sildenafil"], ["ketoconazole", "itraconazole"]], "moderateB"], "pde5_cyp3a4inh_named_tadalafil": [[["tadalafil"], ["itraconazole", "erythromycin"]], "moderateB"], "pde5_cyp3a4inh_named_vardenafil": [[["vardenafil"], ["itraconazole", "clarithromycin", "cobicistat"]], "moderateB"], "almotriptan_cyp3a4inh": [[["almotriptan"], ["ketoconazole"]], "moderateA"], "almotriptan_cyp3a4inh_class": [[["almotriptan"], ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"]], "moderateD"], "eletriptan_cyp3a": [[["eletriptan"], ["itraconazole", "nefazodone", "clarithromycin", "ritonavir"]], "majorB"], "eletriptan_cyp3a_class": [[["eletriptan"], ["voriconazole", "posaconazole", "cobicistat"]], "majorD"], "eletriptan_cyp3a_ketoconazole": [[["eletriptan"], ["ketoconazole"]], "majorA"], "quetiapine_cyp3a4ind": [[["quetiapine"], ["phenytoin"]], "majorA"], "quetiapine_cyp3a4ind_class": [[["quetiapine"], ["rifapentine", "apalutamide", "enzalutamide"]], "majorD"], "quetiapine_cyp3a4ind_named": [[["quetiapine"], ["carbamazepine", "rifampin", "stjohnswort"]], "majorB"]}, MEAS=["alpha1_cyp3a4inh", "buspirone_cyp3a4inh", "pde5_cyp3a4inh", "pde5_cyp3a4inh_erythromycin", "methotrexate_nsaids", "insti_cations", "dolutegravir_inducers_dose", "tenofovir_af_carbamazepine", "atazanavir_cobicistat_drospirenone", "drospirenone_enalapril", "drospirenone_ketoconazole", "doac_voriconazole", "rifampin_morphine_hydromorphone", "methylprednisolone_azole", "mycophenolate_ppi", "carisoprodol_cyp2c19", "hiv_methadone", "clari_statin", "clari_rosuva_prava", "cyclo_rosuva_prava"], REVIEW=["abiraterone_cyp3a4ind", "acalabrutinib_cyp3a4ind", "acalabrutinib_cyp3a4inh", "ace_arb", "alc_benz", "alc_opi", "atorvastatin_cyp3a4inh", "benzos_cyp3a4inh", "bosutinib_cyp3a4ind", "bosutinib_cyp3a4inh", "buprenorphine_cyp3a4inh", "cabazitaxel_cyp3a4inh", "cariprazine_cyp3a4inh", "ccb_cyp3a4inh", "colchicine_cyp3a4inh", "crizotinib_cyp3a4inh", "cyp1a2_substrate_inhibitor", "dabrafenib_cyp3a4inh", "dasatinib_cyp3a4inh", "docetaxel_cyp3a4inh", "dofetilide_contraindicated", "efavirenz_statins", "eplerenone_ccb_nondhp", "eplerenone_cyp3a4inh", "erlotinib_cyp3a4ind", "erlotinib_cyp3a4inh", "everolimus_cyp3a4ind", "everolimus_cyp3a4inh", "exemestane_cyp3a4ind", "gaba_opi", "gefitinib_cyp3a4ind", "gefitinib_cyp3a4inh", "gemfibrozil_statin", "gepant_cyp3a", "gepant_cyp3a4ind", "gepant_cyp3a4mod", "ibrutinib_cyp3a4inh", "imatinib_cyp3a4ind", "imatinib_cyp3a4inh", "ksparing_raas", "levodopa_dopamine_antagonist", "loperamide_pgp", "lurasidone_ccb_nondhp", "lurasidone_cyp3a4ind", "lurasidone_cyp3a4inh", "methadone_cyp3a4inh", "midostaurin_cyp3a4ind", "midostaurin_cyp3a4inh", "nilotinib_cyp3a4inh", "nitro_sild", "olaparib_cyp3a4inh", "opi_benz", "opioids_cyp3a4ind", "opioids_cyp3a4inh", "osimertinib_cyp3a4ind", "palbociclib_cyp3a4ind", "palbociclib_cyp3a4inh", "potassium_raas", "pramipexole_dopamine_antagonist", "pred_nsaid", "promethazine_cns", "quetiapine_cyp3a4inh", "rasagiline_serotonergic", "ribociclib_cyp3a4inh", "risperidone_cyp3a4ind", "ropinirole_dopamine_antagonist", "salmeterol_cyp3a4inh", "saxagliptin_cyp3a4inh", "sirolimus_cyp3a4ind", "sirolimus_cyp3a4inh", "sorafenib_cyp3a4ind", "spiro_ace", "spiro_lisinopril", "sulfonylurea_fluconazole", "tacrolimus_cyp3a4ind", "tacrolimus_cyp3a4inh", "tamoxifen_cyp3a4ind", "ticagrelor_cyp3a", "tretinoin_cyp3a4inh", "vemurafenib_cyp3a4inh", "veneto_cyp3a4", "venetoclax_cyp3a4ind", "vilanterol_cyp3a4inh", "warf_ibu", "zaleplon_cyp3a4ind", "ziprasidone_qt_contraindicated", "zolpidem_cyp3a4ind"];
  // linter (permanent): every grade-A pair with a multi-member group is either reviewed as measured for each member, or on the frozen review list
  DDI_DATA.forEach(function(p){ if(p.ev!=='A') return; var g=p.groups||[[p.drugs[0]],p.drugs.slice(1)];
    if(g.some(function(x){return x.length>1;})) need(MEAS.indexOf(p.id)>=0||REVIEW.indexOf(p.id)>=0,'grade linter: '+p.id+' is A with a multi-member group and has not been reviewed (A only for measured combinations)'); });
  REVIEW.forEach(function(id){need(PR[id]&&PR[id].ev==='A',id+' left the grade-review list without being removed from it (update the linter)');});
  Object.keys(SPLIT).forEach(function(id){var p=PR[id]; need(p,id+' missing (Phase 4 split)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(SPLIT[id][0])&&p.sev+p.ev===SPLIT[id][1],id+' changed (Phase 4 split): '+JSON.stringify(p.groups)+' '+p.sev+p.ev);
    need(/Graded [ABD]:/.test(p.mech),id+' must state its grade basis');});
  need(PR.zolpidem_cyp3a4inh_itraconazole&&/34%/.test(PR.zolpidem_cyp3a4inh_itraconazole.mech)&&PR.zolpidem_cyp3a4inh_itraconazole.sev==='minor','zolpidem + itraconazole: measured +34%, no effect -> Minor/A');
  // FU-AE-1 (owner-approved 6 Nov 2026): zolpidem + itraconazole wording
  (function(){var z=PR.zolpidem_cyp3a4inh_itraconazole; need(z&&z.ev==='A'&&/9626922/.test(z.pmid)&&!/ketoconazole|voriconazole|posaconazole|clarithromycin/.test(z.mech+z.monitor)&&!/lower dose/i.test(z.monitor)&&/Older adults/.test(z.monitor)&&/opioids, benzodiazepines/.test(z.monitor)&&/next-day impairment/.test(z.effect)&&/not been shown to be necessary/.test(z.mech),'zolpidem + itraconazole wording (FU-AE-1): PMID 9626922, no other-inhibitor or lower-dose text, older-adult and CNS-depressant caution');})();
  var rq=PR.rilpivirine_qt; need(rq&&rq.sev+rq.ev==='moderateD'&&rq.drugs.indexOf('erythromycin')<0&&JSON.stringify(rq.groups[1])===JSON.stringify(PR.efavirenz_qt.groups[1].filter(function(x){return x!=='erythromycin';})),'rilpivirine_qt must follow the QT member rule (efavirenz list, erythromycin excluded), Moderate/D');
  ['juluca','rilpivirine','edurant'].forEach(function(q){need(/known risk of torsades de pointes/.test(diAmbiguityNote(q))&&/reserved for future review/.test(diAmbiguityNote(q)),q+': rilpivirine note must list the QT check and keep the partial-review statement');});
  need(/skin products are not yet reviewed separately/.test(diAmbiguityNote('betamethasone'))&&/skin and rectal products are not yet reviewed separately/.test(diAmbiguityNote('hydrocortisone')),'bare steroid generics must state which route their record covers');
})();
// MED-01 Phase 3 Package A4 (AD2 §3): "TAF" and "Orap" must not open records they only prefix, in any of the three tools; their neighbours are unchanged.
(function(){function ex(q){var r=diSearchIndices(q),x=[]; r.idx.forEach(function(i){x.push(DI_DATA[i].id+(r.exact[i]?'':'~'));}); return x.join();}
  need(typeof DI_EXACT_ABBREV==='object'&&JSON.stringify(DI_EXACT_ABBREV)==='{"taf":"tenofovir_af","orap":""}','DI_EXACT_ABBREV must hold exactly taf and orap (owner-approved scope)');
  ['taf','TAF','Taf',' taf ','orap','Orap'].forEach(function(q){need(ex(q)==='',q+' must open no interaction record (opens '+ex(q)+')');});
  need(diReviewedMatches('TAF').map(function(r){return r.id;}).join()==='tenofovir_af','"TAF" must offer the tenofovir alafenamide entry');
  var KEEP={'tafinlar':'dabrafenib','tafluprost':'oph_tafluprost','zioptan':'oph_tafluprost','dabrafenib':'dabrafenib','orapred':'prednisolone','orapred odt':'prednisolone','tafl':'oph_tafluprost~','tafi':'dabrafenib~','orapr':'prednisolone~'};
  Object.keys(KEEP).forEach(function(q){need(ex(q)===KEEP[q],'"'+q+'" must still open '+KEEP[q]+' (opens '+ex(q)+')');});
})();
// MED-02 (change log AF): Erivedge, galantamine, Avodart/Jalyn, finasteride/Entadfi, Hytrin note, darunavir/elvitegravir recognition + A2 + A3.
(function(){var G={"galantamine_paroxetine": [["galantamine"], ["paroxetine"]], "galantamine_ketoconazole": [["galantamine"], ["ketoconazole"]], "galantamine_cyp2d6_named": [["galantamine"], ["amitriptyline", "fluoxetine", "fluvoxamine", "quinidine"]], "galantamine_bradycardia": [["galantamine"], ["metoprolol", "atenolol", "bisoprolol", "nebivolol", "nadolol", "carvedilol", "digoxin", "oph_timolol", "oph_betaxolol", "propranolol"]], "galantamine_cholinesterase": [["galantamine"], ["donepezil", "pyridostigmine"]], "galantamine_anticholinergics": [["galantamine"], ["diphenhydramine", "hydroxyzine", "meclizine", "amitriptyline", "nortriptyline", "imipramine", "desipramine", "clomipramine", "doxepin", "trimipramine", "protriptyline"]], "dutasteride_cyp3a4inh": [["dutasteride"], ["ritonavir", "cobicistat", "ketoconazole", "itraconazole", "clarithromycin", "voriconazole", "posaconazole", "nefazodone"]], "methotrexate_ppi": [["methotrexate"], ["omeprazole", "esomeprazole", "lansoprazole", "dexlansoprazole", "pantoprazole", "rabeprazole"]], "samidorphan_rifampin": [["samidorphan"], ["rifampin"]], "samidorphan_cyp3a4ind": [["samidorphan"], ["rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"]]}, SE={"galantamine_paroxetine": "minorA", "galantamine_ketoconazole": "minorA", "galantamine_cyp2d6_named": "minorB", "galantamine_bradycardia": "moderateD", "galantamine_cholinesterase": "moderateB", "galantamine_anticholinergics": "moderateB", "dutasteride_cyp3a4inh": "minorB", "methotrexate_ppi": "moderateB", "samidorphan_rifampin": "moderateA", "samidorphan_cyp3a4ind": "moderateB"}, NM={"erivedge": "vismodegib", "vismodegib": "vismodegib", "avodart": "dutasteride", "dutasteride": "dutasteride", "jalyn": "dutasteride,tamsulosin", "dutasteride/tamsulosin": "dutasteride,tamsulosin", "dutasteride and tamsulosin": "dutasteride,tamsulosin", "proscar": "finasteride", "propecia": "finasteride", "finasteride": "finasteride", "entadfi": "finasteride,tadalafil", "finasteride/tadalafil": "finasteride,tadalafil", "galantamine": "galantamine", "razadyne": "galantamine", "razadyne er": "galantamine", "galantamine er": "galantamine", "galantamine hydrobromide": "galantamine", "hytrin": "terazosin", "terazosin": "terazosin", "prezista": "darunavir", "darunavir": "darunavir", "elvitegravir": "elvitegravir", "flomax": "tamsulosin", "cialis": "tadalafil", "aricept": "donepezil", "tybost": "cobicistat"};
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (MED-02)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify([].concat.apply([],G[id])),id+' members changed (MED-02): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (MED-02)'); need(/<em>Graded [ABD]/.test(p.mech),id+' must explain its grade');});
  // no other pair may name the MED-02 medicines (no silent additions)
  DDI_DATA.forEach(function(p){['galantamine','dutasteride','vismodegib','finasteride','darunavir','elvitegravir'].forEach(function(x){if(p.drugs.indexOf(x)>=0) need(G[p.id],x+' appears in '+p.id+' (not approved in MED-02)');});});
  ['vismodegib','dutasteride','finasteride','darunavir','elvitegravir'].forEach(function(id){var r=REV[id]; need(r&&!r.sev&&!r.ev&&!r.mols&&/not evidence of safety/.test(r.basis)&&!DR[id],id+' must be a reviewed-only entry with the evidence-gap statement (MED-02)');});
  var g=DR.galantamine; need(g&&g.sev==='moderate'&&g.ev==='D'&&g.mols.join()==='CBD,THC9'&&/What has been measured/.test(g.mech)&&/What is theoretical/.test(g.mech)&&/not a demonstrated interaction/.test(g.mech),'galantamine must be Moderate/D and separate measured from theoretical (MED-02)');
  // owner conditions: no CBD-insignificance claim for dutasteride; Erivedge does not infer CBD has no effect
  [REV.dutasteride.basis,diAmbiguityNote('avodart')].forEach(function(t){need(!/tolerat|not (?:be )?clinically significant|80 times|80×/i.test(t),'dutasteride text must not argue a CBD effect is insignificant (MED-02)');});
  [REV.vismodegib.basis,diAmbiguityNote('erivedge')].forEach(function(t){need(/never been measured/.test(t)&&!/not expected|cannot change|no effect on vismodegib/i.test(t),'vismodegib text must say the CBD effect is unmeasured (MED-02)');});
  need(/boxed warning/.test(diAmbiguityNote('erivedge'))&&/24 months/.test(diAmbiguityNote('vismodegib'))&&/donate blood/.test(diAmbiguityNote('erivedge')),'Erivedge note must carry the boxed warning, 24-month and blood-donation rules');
  need(/6 months/.test(diAmbiguityNote('avodart'))&&/handle/.test(diAmbiguityNote('dutasteride'))&&/PSA/.test(diAmbiguityNote('avodart')),'Avodart note must carry blood donation, handling and PSA');
  need(/6 months/.test(diAmbiguityNote('jalyn'))&&/alpha-blocker/.test(diAmbiguityNote('jalyn'))&&/cataract/.test(diAmbiguityNote('jalyn')),'Jalyn note must carry its own cautions');
  need(/PSA/.test(diAmbiguityNote('proscar'))&&/pregnan/.test(diAmbiguityNote('propecia')),'finasteride note must carry pregnancy and PSA');
  need(/discontinued/.test(diAmbiguityNote('entadfi')),'Entadfi must carry the discontinued note');
  if(PR.methotrexate_ppi){var t=PR.methotrexate_ppi.mech+' '+PR.methotrexate_ppi.effect+' '+PR.methotrexate_ppi.monitor; need(/High-dose methotrexate/.test(t)&&/Low weekly doses/.test(t)&&/delayed elimination/.test(t)&&/serious toxicity/.test(t)&&/prescriber or pharmacist to review/.test(t)&&/not proof of safety for every PPI/.test(t)&&/does not establish safety for other PPIs/.test(t),'methotrexate_ppi must separate high and low dose, name delayed elimination and toxicity, ask for review, and not generalise the lansoprazole result (MED-02 A2)');}
  if(PR.samidorphan_rifampin){[PR.samidorphan_rifampin,PR.samidorphan_cyp3a4ind].forEach(function(p){need(/48%/.test(p.mech)&&/73%/.test(p.mech)&&/destabiliz/.test(p.effect)&&/plain olanzapine \(Zyprexa\)/.test(p.mech),p.id+' must state the measured reductions, loss of antipsychotic effect, and exclude plain olanzapine (MED-02 A3)');});
    need(/none of them was studied with Lybalvi/.test(PR.samidorphan_cyp3a4ind.mech),'samidorphan_cyp3a4ind must say the other inducers were not studied (MED-02 A3)');}
  ['razadyne','razadyne er','hytrin'].forEach(function(b){need(/discontinued brand name/.test(DI_HISTORICAL_BRANDS[b]||''),b+' must carry the discontinued note');});
  ['prezista','darunavir','elvitegravir','genvoya','stribild','symtuza','prezcobix'].forEach(function(q){need(/not yet reviewed in The Table/.test(diAmbiguityNote(q)),q+': the note must say the own interactions are not yet reviewed');});
  function ids(n){var c=diComboFor(n); if(c) return c.ids.slice(); var r=diSearchIndices(n),x=[]; r.idx.forEach(function(i){if(r.exact[i]) x.push(DI_DATA[i].id);}); if(!x.length) diReviewedMatches(n).forEach(function(v){x.push(v.id);}); return x;}
  Object.keys(NM).forEach(function(n){var got=ids(n).slice().sort().join(),want=NM[n].split(',').sort().join(); need(got===want,'"'+n+'" must resolve to '+want+' (resolves to '+(got||'nothing')+')');});
  // no silent wrong-drug match from typed prefixes
  var OK={galant:['galantamine'],razad:['galantamine'],dutas:['dutasteride'],avod:['dutasteride'],jaly:['dutasteride','tamsulosin'],finas:['finasteride'],proscar:['finasteride'],
    propec:['finasteride'],entad:['finasteride','tadalafil'],vismo:['vismodegib'],eriv:['vismodegib'],prezis:['darunavir'],darun:['darunavir'],elvit:['elvitegravir'],hytr:['terazosin']};
  Object.keys(OK).forEach(function(q){var r=diSearchIndices(q),c=diComboFor(q),got=r.idx.map(function(i){return DI_DATA[i].id;}).concat(diReviewedMatches(q).map(function(v){return v.id;})).concat(c?c.ids:[]);
    need(got.length&&got.every(function(x){return OK[q].indexOf(x)>=0;}),'"'+q+'" must reach only '+OK[q]+' (reaches '+got.join()+')');});
  function fire(names){var all=[]; names.forEach(function(n){ids(n).forEach(function(i){if(all.indexOf(i)<0) all.push(i);});});
    return DDI_DATA.filter(function(p){return (p.groups||[[p.drugs[0]],[p.drugs[1]]]).every(function(g){return g.some(function(x){return all.indexOf(x)>=0;});});}).map(function(p){return p.id;}).sort().join(',');}
  var EXP={"galantamine|paxil": "galantamine_paroxetine", "galantamine|nizoral": "galantamine_ketoconazole", "razadyne|prozac": "galantamine_cyp2d6_named", "galantamine|elavil": "galantamine_anticholinergics,galantamine_cyp2d6_named", "razadyne er|metoprolol": "galantamine_bradycardia", "galantamine|aricept": "galantamine_cholinesterase", "galantamine|benadryl": "galantamine_anticholinergics", "galantamine|erythromycin": "", "galantamine|memantine": "", "avodart|norvir": "dutasteride_cyp3a4inh", "avodart|verapamil": "", "proscar|norvir": "", "erivedge|nizoral": "", "jalyn|nizoral": "alpha1_cyp3a4inh,dutasteride_cyp3a4inh", "jalyn|cialis": "pde5_alpha_blockers", "entadfi|cardura": "pde5_alpha_blockers", "prezista|yaz": "", "trexall|prilosec": "methotrexate_ppi", "otrexup|protonix": "methotrexate_ppi", "lybalvi|rifampin": "samidorphan_rifampin", "lybalvi|tegretol": "samidorphan_cyp3a4ind", "zyprexa|rifampin": "", "zyprexa|tegretol": ""};
  Object.keys(EXP).forEach(function(k){var got=fire(k.split('|')); need(got===EXP[k],k.replace(/\|/g,' + ')+' must fire '+(EXP[k]||'nothing')+' (fires '+(got||'nothing')+')');});
})();
// MED-01 Phase 3 Package A3 (AD2 §2): essential HIV recognition and label pairs.
(function(){var G={"dolutegravir_dofetilide": [["dolutegravir"], ["dofetilide"]], "bictegravir_dofetilide": [["bictegravir"], ["dofetilide"]], "bictegravir_rifampin": [["bictegravir"], ["rifampin"]], "insti_cations": [["bictegravir", "dolutegravir"], ["aluminum_hydroxide", "magnesium_hydroxide", "calcium_carbonate"]], "insti_magnesium_carbonate": [["bictegravir", "dolutegravir"], ["magnesium_carbonate"]], "dolutegravir_metformin": [["dolutegravir"], ["metformin"]], "bictegravir_metformin": [["bictegravir"], ["metformin"]], "dolutegravir_inducers_dose": [["dolutegravir"], ["rifampin", "carbamazepine", "efavirenz"]], "dolutegravir_inducers_avoid": [["dolutegravir"], ["oxcarbazepine", "phenytoin", "phenobarbital", "stjohnswort"]], "tenofovir_af_rifamycins": [["tenofovir_af", "vemlidy"], ["rifampin", "rifabutin", "rifapentine", "stjohnswort"]], "tenofovir_af_carbamazepine": [["tenofovir_af", "vemlidy"], ["carbamazepine"]], "tenofovir_af_anticonvulsants": [["tenofovir_af"], ["oxcarbazepine", "phenobarbital", "phenytoin"]], "rilpivirine_contraindicated": [["rilpivirine"], ["omeprazole", "esomeprazole", "lansoprazole", "dexlansoprazole", "pantoprazole", "rabeprazole", "carbamazepine", "oxcarbazepine", "phenobarbital", "phenytoin", "rifampin", "rifapentine", "dexamethasone", "stjohnswort"]], "rilpivirine_h2_blockers": [["rilpivirine"], ["famotidine", "nizatidine", "cimetidine"]], "rilpivirine_antacids": [["rilpivirine"], ["aluminum_hydroxide", "magnesium_hydroxide", "calcium_carbonate", "magnesium_carbonate"]], "rilpivirine_rifabutin": [["rilpivirine"], ["rifabutin"]], "rilpivirine_macrolides": [["rilpivirine"], ["clarithromycin", "erythromycin"]], "atazanavir_ppi": [["atazanavir"], ["omeprazole", "esomeprazole", "lansoprazole", "dexlansoprazole", "pantoprazole", "rabeprazole"]], "atazanavir_h2_blockers": [["atazanavir"], ["famotidine", "nizatidine", "cimetidine"]], "atazanavir_antacids": [["atazanavir"], ["aluminum_hydroxide", "magnesium_hydroxide", "calcium_carbonate", "magnesium_carbonate", "sodium_bicarbonate"]], "vemlidy_anticonvulsants": [["vemlidy"], ["oxcarbazepine", "phenobarbital", "phenytoin"]]}, SE={"dolutegravir_dofetilide": "majorB", "bictegravir_dofetilide": "majorB", "bictegravir_rifampin": "majorA", "insti_cations": "moderateA", "insti_magnesium_carbonate": "moderateB", "dolutegravir_metformin": "moderateA", "bictegravir_metformin": "minorA", "dolutegravir_inducers_dose": "moderateA", "dolutegravir_inducers_avoid": "majorB", "tenofovir_af_rifamycins": "majorB", "tenofovir_af_carbamazepine": "moderateA", "tenofovir_af_anticonvulsants": "moderateB", "rilpivirine_contraindicated": "majorB", "rilpivirine_h2_blockers": "moderateB", "rilpivirine_antacids": "moderateB", "rilpivirine_rifabutin": "moderateB", "rilpivirine_macrolides": "moderateB", "atazanavir_ppi": "majorB", "atazanavir_h2_blockers": "moderateB", "atazanavir_antacids": "moderateB", "vemlidy_anticonvulsants": "majorB"};
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (Pkg A3)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify([].concat.apply([],G[id])),id+' members changed (Pkg A3): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (Pkg A3)'); need(p.source==='label',id+' must cite its labeling');
    need(!/contraindicat/i.test(p.mech)||p.sev==='major'||/contraindicated \(separate warning\)|Proton-pump inhibitors are contraindicated|makes rifampin and carbamazepine contraindicated|rifampin: contraindicated/.test(p.mech),id+': a contraindication must be Major');});
  ['bictegravir','dolutegravir','tenofovir_af','abacavir','rilpivirine','vemlidy'].forEach(function(id){var r=REV[id]; need(r&&!r.sev&&!r.ev&&!r.mols&&/not evidence of safety/.test(r.basis)&&!DR[id],id+' must be a reviewed-only entry with the evidence-gap statement (Pkg A3)');});
  // dose-adjusted and avoid/contraindicated combinations stay in separate pairs (owner condition)
  need(PR.dolutegravir_inducers_dose.sev!=='major'&&PR.dolutegravir_inducers_avoid.sev==='major'&&G.dolutegravir_inducers_dose[1].every(function(x){return G.dolutegravir_inducers_avoid[1].indexOf(x)<0;}),'dolutegravir dose-adjusted and avoid inducers must be separate pairs');
  function ids(n){var c=diComboFor(n); if(c) return c.ids.slice(); var r=diSearchIndices(n),x=[]; r.idx.forEach(function(i){if(r.exact[i]) x.push(DI_DATA[i].id);}); if(!x.length) diReviewedMatches(n).forEach(function(v){x.push(v.id);}); return x;}
  var NM={'biktarvy':'bictegravir,emtricitabine,tenofovir_af','descovy':'emtricitabine,tenofovir_af','truvada':'emtricitabine,tenofovir_df','triumeq':'abacavir,dolutegravir,lamivudine','triumeq pd':'abacavir,dolutegravir,lamivudine',
    'dovato':'dolutegravir,lamivudine','juluca':'dolutegravir,rilpivirine','genvoya':'cobicistat,elvitegravir,emtricitabine,tenofovir_af','symtuza':'cobicistat,darunavir,emtricitabine,tenofovir_af','stribild':'cobicistat,elvitegravir,emtricitabine,tenofovir_df',
    'atripla':'efavirenz,emtricitabine,tenofovir_df','vemlidy':'vemlidy','tenofovir alafenamide':'tenofovir_af,vemlidy','taf':'tenofovir_af','tdf':'tenofovir_df','viread':'tenofovir_df','tivicay':'dolutegravir','edurant':'rilpivirine',
    'emtriva':'emtricitabine','epivir':'lamivudine','ziagen':'abacavir','bictegravir':'bictegravir','dolutegravir':'dolutegravir','rilpivirine':'rilpivirine','abacavir':'abacavir','tenofovir disoproxil fumarate':'tenofovir_df'};
  Object.keys(NM).forEach(function(n){var got=ids(n).slice().sort().join(); need(got===NM[n],'"'+n+'" must resolve to '+NM[n]+' (resolves to '+(got||'nothing')+')');});
  need(/not recommended/.test(PR.vemlidy_anticonvulsants.mech)&&/VEMLIDY with oxcarbazepine, phenobarbital, or phenytoin/.test(PR.vemlidy_anticonvulsants.mech)&&!/not recommended/.test(PR.tenofovir_af_anticonvulsants.mech.replace('says the combination is not recommended','')),'Vemlidy wording only on the Vemlidy pair (no extrapolation to the combination products)');
  need(diReviewedMatches('tenofovir').map(function(r){return r.id;}).sort().join()==='tenofovir_af,tenofovir_df,vemlidy'&&/two forms/.test(diAmbiguityNote('tenofovir')),'"tenofovir" must offer both forms with a note');
  ['juluca','rilpivirine','edurant'].forEach(function(q){need(/not comprehensively reviewed/.test(diAmbiguityNote(q))&&/reserved for future review/.test(diAmbiguityNote(q)),q+': the visible note must say rilpivirine is not comprehensively reviewed');});
  function fire(names){var all=[]; names.forEach(function(n){ids(n).forEach(function(i){if(all.indexOf(i)<0) all.push(i);});});
    return DDI_DATA.filter(function(p){return (p.groups||[[p.drugs[0]],[p.drugs[1]]]).every(function(g){return g.some(function(x){return all.indexOf(x)>=0;});});}).map(function(p){return p.id;}).sort().join(',');}
  var EXP={'biktarvy|tikosyn':'bictegravir_dofetilide','tivicay|tikosyn':'dolutegravir_dofetilide','triumeq|tikosyn':'dolutegravir_dofetilide','dovato|tikosyn':'dolutegravir_dofetilide','juluca|tikosyn':'dolutegravir_dofetilide,rilpivirine_qt','biktarvy|rifampin':'bictegravir_rifampin,tenofovir_af_rifamycins','tivicay|rifampin':'dolutegravir_inducers_dose',
    'dovato|dilantin':'dolutegravir_inducers_avoid','descovy|rifampin':'tenofovir_af_rifamycins','descovy|tegretol':'tenofovir_af_carbamazepine','vemlidy|dilantin':'vemlidy_anticonvulsants','descovy|dilantin':'tenofovir_af_anticonvulsants','biktarvy|trileptal':'tenofovir_af_anticonvulsants','vemlidy|rifampin':'tenofovir_af_rifamycins','vemlidy|tegretol':'tenofovir_af_carbamazepine','truvada|rifampin':'','truvada|tums':'',
    'juluca|omeprazole':'rilpivirine_contraindicated','juluca|rifampin':'dolutegravir_inducers_dose,rilpivirine_contraindicated','juluca|pepcid':'rilpivirine_h2_blockers','juluca|tums':'insti_cations,rilpivirine_antacids',
    'triumeq|metformin':'dolutegravir_metformin','biktarvy|metformin':'bictegravir_metformin','biktarvy|tums':'insti_cations','reyataz|prilosec':'atazanavir_ppi','reyataz|pepcid':'atazanavir_h2_blockers',
    'reyataz|tums':'atazanavir_antacids','dovato|prilosec':'','biktarvy|prilosec':'','descovy|sprintec':'','biktarvy|sprintec':'','tivicay|sprintec':''};
  Object.keys(EXP).forEach(function(k){var got=fire(k.split('|')); need(got===EXP[k],k.replace('|',' + ')+' must fire '+(EXP[k]||'nothing')+' (fires '+(got||'nothing')+')');});
})();
// MED-01 Phase 3 Package A2 (AD2): emergency contraception; aspirin and topical diclofenac names.
(function(){var G={"lng_ec_efavirenz": [["levonorgestrel_ec"], ["efavirenz"]], "lng_ec_rifampin": [["levonorgestrel_ec"], ["rifampin"]], "lng_ec_inducers": [["levonorgestrel_ec"], ["carbamazepine", "oxcarbazepine", "phenytoin", "phenobarbital", "primidone", "topiramate", "eslicarbazepine", "clobazam", "rifabutin", "rifapentine", "stjohnswort", "apalutamide", "enzalutamide"]], "ulipristal_rifampin": [["ulipristal"], ["rifampin"]], "ulipristal_inducers": [["ulipristal"], ["phenobarbital", "primidone", "carbamazepine", "oxcarbazepine", "phenytoin", "topiramate", "stjohnswort"]], "ulipristal_inducers_unnamed": [["ulipristal"], ["rifabutin", "rifapentine", "eslicarbazepine", "efavirenz", "apalutamide", "enzalutamide"]], "ulipristal_progestin_contraceptives": [["ulipristal"], ["contraceptive_ee", "drospirenone", "drospirenone_pop", "contraceptive_pop", "etonogestrel_implant", "dmpa_im", "dmpa_subq", "levonorgestrel_ec"]]}, SE={"lng_ec_efavirenz": "moderateA", "lng_ec_rifampin": "moderateB", "lng_ec_inducers": "moderateD", "ulipristal_rifampin": "majorA", "ulipristal_inducers": "majorB", "ulipristal_inducers_unnamed": "majorD", "ulipristal_progestin_contraceptives": "moderateB"}, PM={"lng_ec_efavirenz": "22536010, 36641094", "lng_ec_rifampin": "36641094"};
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (Pkg A2)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify([].concat.apply([],G[id])),id+' members changed (Pkg A2): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (Pkg A2)'); need(p.source==='label'&&p.pmid===(PM[id]||''),id+' must cite exactly its labeling and studies');});
  ['levonorgestrel_ec','ulipristal'].forEach(function(id){var d=DR[id]; need(d&&d.sev+d.ev==='minorD'&&JSON.stringify(d.mols)==='["CBD"]'&&d.pmid==='37313955'&&/Graded D: a theoretical interaction with an identifiable mechanism/.test(d.mech)&&/not evidence of safety/.test(d.mech),id+' must be Minor/D, CBD only, with the evidence-gap statement (Pkg A2)');});
  need(/22536010/.test(PR.lng_ec_efavirenz.pmid)&&/56%/.test(PR.lng_ec_efavirenz.mech),'Plan B + efavirenz must cite Carten 2012 (56%)');
  need(/3&nbsp;mg dose/.test(PR.lng_ec_rifampin.mech)&&/not tested with rifampicin/.test(PR.lng_ec_rifampin.mech),'Plan B + rifampin must say the standard dose was not measured (why B, not A)');
  need(/not something to do on your own/.test(PR.lng_ec_efavirenz.mech)&&!/take two tablets/i.test(JSON.stringify(G)),'no dose instruction to the user');
  need(/93%/.test(PR.ulipristal_rifampin.mech)&&/no sooner than 5 days/.test(PR.ulipristal_progestin_contraceptives.mech),'ella pairs must carry the labeling figures and the 5-day rule');
  function ids(n){var c=diComboFor(n); if(c) return c.ids.slice(); var r=diSearchIndices(n),x=[]; r.idx.forEach(function(i){if(r.exact[i]) x.push(DI_DATA[i].id);}); if(!x.length) diReviewedMatches(n).forEach(function(v){x.push(v.id);}); return x;}
  function fire(names){var all=[]; names.forEach(function(n){ids(n).forEach(function(i){if(all.indexOf(i)<0) all.push(i);});});
    return DDI_DATA.filter(function(p){return (p.groups||[[p.drugs[0]],[p.drugs[1]]]).every(function(g){return g.some(function(x){return all.indexOf(x)>=0;});});}).map(function(p){return p.id;}).sort().join(',');}
  var EXP={'plan b one-step|sustiva':'lng_ec_efavirenz','plan b|rifampin':'lng_ec_rifampin','my way|tegretol':'lng_ec_inducers','aftera|keppra':'','plan b|lamictal':'',
    'ella|rifampin':'ulipristal_rifampin','ella|dilantin':'ulipristal_inducers','ella|sustiva':'ulipristal_inducers_unnamed','ella|sprintec':'ulipristal_progestin_contraceptives',
    'ella|nexplanon':'ulipristal_progestin_contraceptives','ella|plan b':'ulipristal_progestin_contraceptives','ella|mirena':'','ella|nizoral':'','ella|omeprazole':'','plan b|norvir':''};
  Object.keys(EXP).forEach(function(k){var got=fire(k.split('|')); need(got===EXP[k],k.replace('|',' + ')+' must fire '+(EXP[k]||'nothing')+' (fires '+(got||'nothing')+')');});
  var NM={'baby aspirin':'aspirin','low dose aspirin':'aspirin','low-dose aspirin':'aspirin','asa':'aspirin','acetylsalicylic acid':'aspirin','ecotrin low strength':'aspirin','st joseph':'aspirin','st. joseph':'aspirin',
    'flector':'diclofenac','pennsaid':'diclofenac','voltaren arthritis pain':'diclofenac','voltaren gel':'diclofenac','plan b':'levonorgestrel_ec','plan b one-step':'levonorgestrel_ec','ella':'ulipristal','ulipristal':'ulipristal'};
  Object.keys(NM).forEach(function(n){var r=diSearchIndices(n),x=[]; r.idx.forEach(function(i){if(r.exact[i]) x.push(DI_DATA[i].id);}); need(x.join()===NM[n],'"'+n+'" must open '+NM[n]+' exactly (opens '+(x.join()||'nothing')+')');});
  need(/much lower than with tablets but has not been measured/.test(PR.methotrexate_diclofenac.mech)&&PR.methotrexate_diclofenac.sev+PR.methotrexate_diclofenac.ev==='majorB','topical diclofenac wording (FU-P3-0-2), severity unchanged');
  ['emergency contraception','emergency contraceptive','morning after pill'].forEach(function(q){var r=diSearchIndices(q); need(!r.idx.length&&diClassMatches(q).map(function(d){return d.id;}).sort().join()==='levonorgestrel_ec,ulipristal','"'+q+'" must list both emergency pills as a class, never one by name');});
  need(/baby aspirin, Ecotrin Low Strength/.test(PR.methotrexate_aspirin.mech),'low-dose names on the aspirin pair (FU-P3-0-1)');
})();
// MED-01 Phase 3 Package A1 (AD1 §9): P3-1a-ii pairs; Major-first sort.
(function(){var G={"hc_inducers": [["contraceptive_ee", "drospirenone", "drospirenone_pop", "contraceptive_pop", "etonogestrel_implant"], ["rifampin", "rifabutin", "rifapentine", "carbamazepine", "phenytoin", "phenobarbital", "primidone", "oxcarbazepine", "stjohnswort", "clobazam"]], "hc_topiramate": [["contraceptive_ee", "drospirenone", "contraceptive_pop", "etonogestrel_implant"], ["topiramate"]], "hc_eslicarbazepine": [["contraceptive_ee", "drospirenone"], ["eslicarbazepine"]], "hc_inducers_unnamed": [["contraceptive_ee", "drospirenone", "drospirenone_pop", "contraceptive_pop", "etonogestrel_implant"], ["apalutamide", "enzalutamide"]], "hc_eslicarbazepine_progestin": [["drospirenone_pop", "contraceptive_pop", "etonogestrel_implant"], ["eslicarbazepine"]], "slynd_topiramate": [["drospirenone_pop"], ["topiramate"]], "dmpa_im_inducers": [["dmpa_im"], ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "phenobarbital", "primidone", "oxcarbazepine", "topiramate", "stjohnswort", "clobazam"]], "dmpa_subq_inducers": [["dmpa_subq"], ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "phenobarbital", "primidone", "stjohnswort", "apalutamide", "enzalutamide", "clobazam"]], "lamotrigine_contraceptive_ee": [["lamotrigine"], ["contraceptive_ee"]], "drospirenone_potassium": [["drospirenone", "drospirenone_pop"], ["spironolactone", "eplerenone", "amiloride", "triamterene", "potassium", "lisinopril", "ramipril", "benazepril", "quinapril", "captopril", "fosinopril", "moexipril", "perindopril", "trandolapril", "losartan", "valsartan", "olmesartan", "irbesartan", "candesartan", "telmisartan", "azilsartan", "ibuprofen", "naproxen", "meloxicam", "celecoxib", "diclofenac"]], "drospirenone_enalapril": [["drospirenone", "drospirenone_pop"], ["enalapril"]], "drospirenone_cyp3a4inh": [["drospirenone", "drospirenone_pop"], ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "nefazodone"]], "drospirenone_ketoconazole": [["drospirenone", "drospirenone_pop"], ["ketoconazole"]], "drospirenone_ritonavir": [["drospirenone", "drospirenone_pop"], ["ritonavir"]]}, SE={"hc_inducers": "moderateB", "hc_topiramate": "moderateB", "hc_eslicarbazepine": "moderateB", "hc_inducers_unnamed": "moderateD", "hc_eslicarbazepine_progestin": "moderateD", "slynd_topiramate": "moderateD", "dmpa_im_inducers": "minorB", "dmpa_subq_inducers": "moderateB", "lamotrigine_contraceptive_ee": "moderateA", "drospirenone_potassium": "moderateB", "drospirenone_enalapril": "moderateA", "drospirenone_cyp3a4inh": "minorB", "drospirenone_ketoconazole": "minorA", "drospirenone_ritonavir": "minorB"};
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (Pkg A1)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify([].concat.apply([],G[id])),id+' members changed (Pkg A1): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (Pkg A1)'); need(p.source==='label',id+' must cite its labeling');});
  function ids(n){var c=diComboFor(n); if(c) return c.ids.slice(); var r=diSearchIndices(n),x=[]; r.idx.forEach(function(i){if(r.exact[i]) x.push(DI_DATA[i].id);}); if(!x.length) diReviewedMatches(n).forEach(function(v){x.push(v.id);}); return x;}
  function fire(names){var all=[]; names.forEach(function(n){ids(n).forEach(function(i){if(all.indexOf(i)<0) all.push(i);});});
    return DDI_DATA.filter(function(p){return (p.groups||[[p.drugs[0]],[p.drugs[1]]]).every(function(g){return g.some(function(x){return all.indexOf(x)>=0;});});}).map(function(p){return p.id;}).sort().join(',');}
  var EXP={'sprintec|rifampin':'hc_inducers','yaz|tegretol':'hc_inducers','slynd|rifampin':'hc_inducers','nexplanon|dilantin':'hc_inducers','camila|rifampin':'hc_inducers',
    'opill|st johns wort':'hc_inducers','depo-provera|rifampin':'dmpa_im_inducers','depo-subq provera 104|rifampin':'dmpa_subq_inducers','mirena|rifampin':'','mirena|tegretol':'',
    'sprintec|lamictal':'lamotrigine_contraceptive_ee','yaz|lamictal':'lamotrigine_contraceptive_ee','camila|lamictal':'','nexplanon|lamictal':'','depo-provera|lamictal':'','slynd|lamictal':'',
    'slynd|aldactone':'drospirenone_potassium','yaz|aldactone':'drospirenone_potassium','sprintec|aldactone':'','camila|aldactone':'','yaz|enalapril':'drospirenone_enalapril',
    'yaz|aspirin':'','slynd|topamax':'slynd_topiramate','sprintec|topamax':'hc_topiramate','depo-subq provera 104|topamax':'','sprintec|aptiom':'hc_eslicarbazepine','nexplanon|aptiom':'hc_eslicarbazepine_progestin',
    'yaz|nizoral':'drospirenone_ketoconazole','yaz|prezcobix':'cobicistat_contraceptive_ee,cobicistat_drospirenone','slynd|norvir':'drospirenone_ritonavir','yaz|norvir':'drospirenone_ritonavir,ritonavir_contraceptive_ee',
    'yaz|evotaz':'atazanavir_cobicistat_drospirenone,cobicistat_contraceptive_ee,cobicistat_drospirenone'};
  Object.keys(EXP).forEach(function(k){var got=fire(k.split('|')); need(got===EXP[k],k.replace('|',' + ')+' must fire '+(EXP[k]||'nothing')+' (fires '+(got||'nothing')+')');});
  DDI_DATA.forEach(function(p){need(p.drugs.indexOf('lng_iud')<0,'the LNG-IUD must have no drug-drug pair ('+p.id+')');});
  ['drospirenone_cyp3a4inh','drospirenone_potassium'].forEach(function(id){need(PR[id]&&PR[id].drugs.indexOf('cobicistat')<0,id+' must not include cobicistat (own pairs)');});
})();
// MED-01 Phase 3 P3-1a-i (AD1): hormonal contraceptives - 3 records (cannabis class b) + 5 reviewed-only (class c) + reviewed-only atazanavir; booster, Evotaz-regimen and efavirenz pairs.
(function(){var RECS={"contraceptive_ee": "minorD", "drospirenone": "minorD", "drospirenone_pop": "minorD"}, REVS=["contraceptive_pop", "etonogestrel_implant", "dmpa_im", "dmpa_subq", "lng_iud", "atazanavir"], BR={"contraceptive_ee": ["Sprintec", "Tri-Sprintec", "Tri-Lo-Sprintec", "Estarylla", "Mili", "Tri-Mili", "Ortho Tri-Cyclen", "Loestrin", "Lo Loestrin Fe", "Junel", "Junel Fe", "Microgestin", "Blisovi", "Larin", "Taytulla", "Nortrel", "Seasonique", "Seasonale", "Camrese", "Jolessa", "Amethyst", "Aviane", "Lutera", "Levora", "Portia", "Altavera", "Vienva", "Balcoltra", "Apri", "Kariva", "Azurette", "Enskyce", "Cryselle", "Low-Ogestrel", "Kelnor", "Zovia", "Xulane", "Zafemy", "Twirla", "NuvaRing", "EluRyng", "Annovera"], "drospirenone": ["Yaz", "Yasmin", "Beyaz", "Safyral", "Loryna", "Nikki", "Ocella", "Syeda", "Zarah", "Jasmiel", "Zumandimine", "Nextstellis"], "drospirenone_pop": ["Slynd"], "contraceptive_pop": ["Camila", "Errin", "Heather", "Jencycla", "Incassia", "Opill"], "etonogestrel_implant": ["Nexplanon"], "dmpa_im": ["Depo-Provera CI"], "dmpa_subq": ["depo-subQ provera 104"], "lng_iud": ["Mirena", "Kyleena", "Liletta", "Skyla"], "atazanavir": ["Reyataz"]}, G={"ritonavir_contraceptive_ee": [["ritonavir"], ["contraceptive_ee"]], "cobicistat_contraceptive_ee": [["cobicistat"], ["contraceptive_ee"]], "cobicistat_drospirenone": [["cobicistat"], ["drospirenone", "drospirenone_pop"]], "atazanavir_cobicistat_drospirenone": [["atazanavir"], ["cobicistat"], ["drospirenone", "drospirenone_pop"]], "efavirenz_contraceptive_ee": [["efavirenz"], ["contraceptive_ee"]], "efavirenz_contraceptive_pop": [["efavirenz"], ["contraceptive_pop", "drospirenone_pop"]], "efavirenz_etonogestrel_implant": [["efavirenz"], ["etonogestrel_implant"]], "efavirenz_dmpa_im": [["efavirenz"], ["dmpa_im"]], "efavirenz_dmpa_subq": [["efavirenz"], ["dmpa_subq"]]}, SE={"ritonavir_contraceptive_ee": "moderateA", "cobicistat_contraceptive_ee": "moderateB", "cobicistat_drospirenone": "moderateB", "atazanavir_cobicistat_drospirenone": "majorA", "efavirenz_contraceptive_ee": "moderateA", "efavirenz_contraceptive_pop": "moderateB", "efavirenz_etonogestrel_implant": "moderateA", "efavirenz_dmpa_im": "minorA", "efavirenz_dmpa_subq": "moderateB"}, PM={"ritonavir_contraceptive_ee": "39106314, 34407424", "cobicistat_contraceptive_ee": "31933482, 39106314", "cobicistat_drospirenone": "31933482", "atazanavir_cobicistat_drospirenone": "31933482", "efavirenz_contraceptive_ee": "21447863, 39106314", "efavirenz_contraceptive_pop": "21447863, 39106314", "efavirenz_etonogestrel_implant": "28692531, 38458408, 34407424, 22036046, 39106314", "efavirenz_dmpa_im": "17192768, 31504342, 39106314", "efavirenz_dmpa_subq": "17192768, 39106314"}, NM={"sprintec": "contraceptive_ee", "tri-sprintec": "contraceptive_ee", "loestrin": "contraceptive_ee", "lo loestrin fe": "contraceptive_ee", "junel fe": "contraceptive_ee", "xulane": "contraceptive_ee", "twirla": "contraceptive_ee", "nuvaring": "contraceptive_ee", "annovera": "contraceptive_ee", "ortho evra": "contraceptive_ee", "ethinyl estradiol": "contraceptive_ee", "the pill": "contraceptive_ee", "birth control pill": "contraceptive_ee", "levonorgestrel and ethinyl estradiol": "contraceptive_ee", "norgestimate": "contraceptive_ee", "levonorgestrel": "contraceptive_ee", "yaz": "contraceptive_ee,drospirenone", "yasmin": "contraceptive_ee,drospirenone", "beyaz": "contraceptive_ee,drospirenone", "drospirenone/ethinyl estradiol": "contraceptive_ee,drospirenone", "nextstellis": "drospirenone", "slynd": "drospirenone_pop", "camila": "contraceptive_pop", "opill": "contraceptive_pop", "minipill": "contraceptive_pop", "nexplanon": "etonogestrel_implant", "implanon": "etonogestrel_implant", "depo-provera": "dmpa_im", "depo-provera ci": "dmpa_im", "birth control shot": "dmpa_im", "depo-subq provera 104": "dmpa_subq", "mirena": "lng_iud", "kyleena": "lng_iud", "liletta": "lng_iud", "skyla": "lng_iud", "hormonal iud": "lng_iud", "medroxyprogesterone": "medroxyprogesterone,dmpa_im,dmpa_subq", "reyataz": "atazanavir", "atazanavir": "atazanavir", "evotaz": "atazanavir,cobicistat", "atazanavir/cobicistat": "atazanavir,cobicistat", "prezcobix": "darunavir,cobicistat", "estradiol": "estrogen_hrt", "estrace": "estrogen_hrt", "premarin": "estrogen_hrt", "prempro": "estrogen_hrt,medroxyprogesterone", "plan b": "levonorgestrel_ec", "ella": "ulipristal"}, FAMK=["birth control", "contraceptive", "levonorgestrel", "norethindrone", "norgestrel", "etonogestrel", "drospirenone", "medroxyprogesterone", "provera", "iud", "atazanavir", "reyataz"];
  var CID=Object.keys(RECS).concat(REVS.filter(function(x){return x!=='atazanavir';}));
  function ids(n){var c=diComboFor(n); if(c) return c.ids.slice(); var r=diSearchIndices(n),x=[]; r.idx.forEach(function(i){if(r.exact[i]) x.push(DI_DATA[i].id);}); if(!x.length) diReviewedMatches(n).forEach(function(v){x.push(v.id);}); return x;}
  Object.keys(RECS).forEach(function(id){var d=DR[id]; need(d,id+' record missing (P3-1a)'); if(!d) return;
    need(d.sev+d.ev===RECS[id]&&JSON.stringify(d.mols)==='["CBD"]'&&d.pmid==='37313955','%s must be Minor/D, CBD only, citing 37313955 (AD1 cannabis class b)'.replace('%s',id));
    need(/Graded D: a theoretical interaction with an identifiable mechanism/.test(d.mech),id+' must state the cannabis classification (theoretical, identifiable mechanism)');
    need(!REV[id],id+' must not also be reviewed-only');});
  REVS.forEach(function(id){var r=REV[id]; need(r,id+' reviewed-only entry missing (P3-1a)'); if(!r) return;
    need(!r.sev&&!r.ev&&!r.mols&&r.reviewed==='2026-11-05',id+' must be a reviewed-only entry (cannabis class c)'); need(/This is an evidence gap, not evidence of safety/.test(r.basis),id+' must give the neutral evidence-gap statement'); need(!DR[id],id+' must not also be a record');});
  need(/39002624/.test(REV.etonogestrel_implant.basis)&&/not evidence of safety/.test(REV.etonogestrel_implant.basis)&&!/39002624/.test(JSON.stringify(DI_DATA)+JSON.stringify(DDI_DATA)),'PMID 39002624 is cited only on the implant entry, for what it shows');
  need(/cigarette/.test(DR.contraceptive_ee.mech)&&/not applied to cannabis/.test(DR.contraceptive_ee.mech),'contraceptive_ee must not equate cannabis smoking with the cigarette warning');
  Object.keys(BR).forEach(function(id){var x=DR[id]||REV[id]; need(x&&JSON.stringify(x.brands)===JSON.stringify(BR[id]),id+' brands changed (DailyMed-verified list, AD1 §2)');});
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (P3-1a)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify([].concat.apply([],G[id])),id+' members changed (AD1): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (AD1)'); need(p.source==='label'&&p.pmid===PM[id],id+' must cite exactly its studies');});
  // the only pairs touching a contraceptive id are the P3-1a-i pairs (inducers, lamotrigine, potassium are P3-1a-ii)
  var GA1={"hc_inducers": [["contraceptive_ee", "drospirenone", "drospirenone_pop", "contraceptive_pop", "etonogestrel_implant"], ["rifampin", "rifabutin", "rifapentine", "carbamazepine", "phenytoin", "phenobarbital", "primidone", "oxcarbazepine", "stjohnswort", "clobazam"]], "hc_topiramate": [["contraceptive_ee", "drospirenone", "contraceptive_pop", "etonogestrel_implant"], ["topiramate"]], "hc_eslicarbazepine": [["contraceptive_ee", "drospirenone"], ["eslicarbazepine"]], "hc_inducers_unnamed": [["contraceptive_ee", "drospirenone", "drospirenone_pop", "contraceptive_pop", "etonogestrel_implant"], ["apalutamide", "enzalutamide"]], "hc_eslicarbazepine_progestin": [["drospirenone_pop", "contraceptive_pop", "etonogestrel_implant"], ["eslicarbazepine"]], "slynd_topiramate": [["drospirenone_pop"], ["topiramate"]], "dmpa_im_inducers": [["dmpa_im"], ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "phenobarbital", "primidone", "oxcarbazepine", "topiramate", "stjohnswort", "clobazam"]], "dmpa_subq_inducers": [["dmpa_subq"], ["rifampin", "rifapentine", "carbamazepine", "phenytoin", "phenobarbital", "primidone", "stjohnswort", "apalutamide", "enzalutamide", "clobazam"]], "lamotrigine_contraceptive_ee": [["lamotrigine"], ["contraceptive_ee"]], "drospirenone_potassium": [["drospirenone", "drospirenone_pop"], ["spironolactone", "eplerenone", "amiloride", "triamterene", "potassium", "lisinopril", "ramipril", "benazepril", "quinapril", "captopril", "fosinopril", "moexipril", "perindopril", "trandolapril", "losartan", "valsartan", "olmesartan", "irbesartan", "candesartan", "telmisartan", "azilsartan", "ibuprofen", "naproxen", "meloxicam", "celecoxib", "diclofenac"]], "drospirenone_enalapril": [["drospirenone", "drospirenone_pop"], ["enalapril"]], "drospirenone_cyp3a4inh": [["drospirenone", "drospirenone_pop"], ["itraconazole", "voriconazole", "posaconazole", "clarithromycin", "nefazodone"]], "drospirenone_ketoconazole": [["drospirenone", "drospirenone_pop"], ["ketoconazole"]], "drospirenone_ritonavir": [["drospirenone", "drospirenone_pop"], ["ritonavir"]]}; DDI_DATA.forEach(function(p){ if(CID.some(function(c){return p.drugs.indexOf(c)>=0;})) need(!!G[p.id]||!!GA1[p.id]||["lng_ec_efavirenz", "lng_ec_inducers", "lng_ec_rifampin", "ulipristal_inducers", "ulipristal_inducers_unnamed", "ulipristal_progestin_contraceptives", "ulipristal_rifampin"].indexOf(p.id)>=0,p.id+' involves a contraceptive entry but is not a P3-1a/Package A pair');});
  function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  // explicit negative guards (owner amendments)
  DDI_DATA.forEach(function(p){need(p.drugs.indexOf('lng_iud')<0,'the LNG-IUD must have no drug-drug pair ('+p.id+')');});
  ['ritonavir','cobicistat'].forEach(function(b){['etonogestrel_implant','contraceptive_pop','dmpa_im','dmpa_subq','lng_iud'].forEach(function(c){need(links(b,c)==='',b+' + '+c+' must fire nothing (no booster reduced-effectiveness pair; AD1)');});});
  need(links('efavirenz','lng_iud')==='','efavirenz + LNG-IUD must fire nothing');
  DDI_DATA.forEach(function(p){ if(p.drugs.indexOf('cobicistat')>=0&&(p.drugs.indexOf('drospirenone')>=0||p.drugs.indexOf('drospirenone_pop')>=0)&&p.sev==='major') need(p.id==='atazanavir_cobicistat_drospirenone'&&JSON.stringify(p.groups[0])==='["atazanavir"]','no Evotaz-specific contraindication on every cobicistat product ('+p.id+')');});
  var cd=PR.cobicistat_drospirenone; need(cd&&!/contraindicat/i.test(cd.mech+cd.effect+cd.monitor)&&/separate Major/.test(cd.mech)&&/Prezcobix/.test(cd.mech)&&/Genvoya/.test(cd.mech)&&/not been measured with elvitegravir/.test(cd.mech)&&/1.6-fold/.test(cd.mech),'cobicistat_drospirenone must describe the darunavir/elvitegravir regimens only and point Evotaz users to the Major warning (owner item 3)');
  var ad=PR.atazanavir_cobicistat_drospirenone; need(ad&&/Evotaz/.test(ad.mech)&&/Tybost given with atazanavir/.test(ad.mech)&&/2.3-fold/.test(ad.mech)&&/contraindicated with TYBOST/.test(ad.mech),'regimen pair must name Evotaz and Tybost with atazanavir, Majeed 2019 and the Tybost wording');
  DDI_DATA.forEach(function(p){ if(p.drugs.indexOf('atazanavir')>=0) need(['atazanavir_cobicistat_drospirenone','atazanavir_ppi','atazanavir_h2_blockers','atazanavir_antacids'].indexOf(p.id)>=0,'atazanavir must have no other pair until P3-3b ('+p.id+')');});
  ['reyataz','atazanavir'].forEach(function(q){need(/Its own drug&ndash;drug interactions are not yet reviewed/.test(diAmbiguityNote(q)),q+' must show the visible not-yet-reviewed note (reviewed-only basis text is not displayed)');});
  need(/not yet reviewed in The Table/.test(REV.atazanavir.basis)&&/not evidence that the combination is safe/.test(REV.atazanavir.basis),'atazanavir entry must say its own interactions are not yet reviewed');
  function fire(names){var all=[]; names.forEach(function(n){ids(n).forEach(function(i){if(all.indexOf(i)<0) all.push(i);});});   // same rule as findDdiInteractions: every group must be present
    return DDI_DATA.filter(function(p){return (p.groups||[[p.drugs[0]],[p.drugs[1]]]).every(function(g){return g.some(function(x){return all.indexOf(x)>=0;});});});}
  function majDrsp(names){return fire(names).filter(function(p){return p.sev==='major'&&(p.drugs.indexOf('drospirenone')>=0||p.drugs.indexOf('drospirenone_pop')>=0);}).map(function(p){return p.id;}).join(',');}
  [['evotaz','yaz'],['evotaz','slynd'],['tybost','reyataz','yaz'],['atazanavir/cobicistat','yasmin']].forEach(function(n){need(majDrsp(n)==='atazanavir_cobicistat_drospirenone',n.join(' + ')+' must give the Major regimen pair');});
  [['reyataz','yaz'],['reyataz','norvir','yaz'],['prezcobix','yaz'],['symtuza','yaz'],['genvoya','yaz'],['stribild','yaz'],['tybost','yaz'],['prezcobix','slynd']].forEach(function(n){need(majDrsp(n)==='',n.join(' + ')+' must give no Major drospirenone pair');});
  need(fire(['prezcobix','yaz']).map(function(p){return p.id;}).sort().join()==='cobicistat_contraceptive_ee,cobicistat_drospirenone','Prezcobix + Yaz must give the two Moderate cobicistat cards');
  need(/4&nbsp;mg/.test(DR.drospirenone_pop.mech)&&!/3&nbsp;mg/.test(DR.drospirenone_pop.mech)&&/3&nbsp;mg/.test(DR.drospirenone.mech),'Slynd is 4 mg drospirenone; combined pills 3 mg');
  var sq=PR.efavirenz_dmpa_subq; need(sq&&/not confirmed by a clinical study/.test(sq.mech)&&/one category for both/.test(sq.mech)&&!/do not address/.test(sq.mech)&&/No contraceptive failure/.test(sq.mech),'subQ pair must separate the product labeling from the CDC category (owner item 4)');
  need(/Paxlovid has not been studied directly/.test(PR.ritonavir_contraceptive_ee.mech),'ritonavir pair must say Paxlovid was not studied directly');
  // P3-1a-ii boundary pin retired in Package A1 (the inducer, lamotrigine and potassium pairs exist; see the Package A1 block)
  // identity: estradiol stays on estrogen_hrt only; no contraceptive name reaches estrogen_hrt
  Object.keys(NM).forEach(function(n){need(ids(n).join(',')===NM[n],'"'+n+'" must reach '+(NM[n]||'nothing')+' (reaches '+ids(n).join(',')+')');});
  ['estradiol','estrad','estradiol patch'].forEach(function(q){var r=diSearchIndices(q); need(r.idx.every(function(i){return DI_DATA[i].id==='estrogen_hrt';})&&!diComboFor(q)&&!diReviewedMatches(q).some(function(v){return CID.indexOf(v.id)>=0;}),'"'+q+'" must reach estrogen_hrt only');});
  Object.keys(BR).forEach(function(id){BR[id].forEach(function(b){var r=diSearchIndices(b.toLowerCase()),c=diComboFor(b); need(!r.idx.some(function(i){return DI_DATA[i].id==='estrogen_hrt';})&&!(c&&c.ids.indexOf('estrogen_hrt')>=0),b+' must not reach estrogen_hrt');});});
  FAMK.forEach(function(k){need(DI_AMBIGUOUS_FAMILY[k]&&diAmbiguityNote(k)===DI_AMBIGUOUS_FAMILY[k],k+' must carry its disambiguation note (AD1)');});
  need(/Depo-Provera CI/.test(DI_AMBIGUOUS_FAMILY.medroxyprogesterone)&&/Prempro/.test(DI_AMBIGUOUS_FAMILY.medroxyprogesterone),'medroxyprogesterone note must list the injections and the oral menopausal product');
  need(DI_HISTORICAL_BRANDS.implanon&&diAmbiguityNote('implanon')===DI_HISTORICAL_BRANDS.implanon&&ids('implanon').join()==='etonogestrel_implant','implanon must open the implant entry with its discontinued note');
  // deliberate text changes: the contraceptive entries exist
  ['ritonavir','cobicistat','efavirenz'].forEach(function(id){need(!/The Table has no birth-control record/.test(DR[id].monitor)&&/My Medication List now checks/.test(DR[id].monitor),id+' contraception sentence must say the entries now exist (AD1)');});
})();
// MED-01 Phase 3 P3-0b (AD0 §7): methotrexate x NSAIDs Major (owner decision), graded per medicine; aspirin separate and dose-qualified.
(function(){function H32(t){var x=5381;for(var i=0;i<t.length;i++){x=(((x<<5)+x)^t.charCodeAt(i))>>>0;}return x;}
  var G={"methotrexate_nsaids": [["methotrexate"], ["ibuprofen", "naproxen", "meloxicam", "celecoxib"]], "methotrexate_diclofenac": [["methotrexate"], ["diclofenac"]], "methotrexate_aspirin": [["methotrexate"], ["aspirin"]]}, SE={"methotrexate_nsaids": "majorA", "methotrexate_diclofenac": "majorB", "methotrexate_aspirin": "majorA"}, PM={"methotrexate_nsaids": "2213774, 1618240, 2328562, 9150070, 10606360", "methotrexate_aspirin": "1747136, 2273459"}, PIN={"aspirin": 4007431707, "ibuprofen": 2970549789, "naproxen": 604710129, "meloxicam": 445471778, "celecoxib": 2557436154, "diclofenac": 4042110804, "methotrexate": 3797171385}, PINP={"methotrexate_tmp_smx": 2866569475, "methotrexate_sulfadiazine": 4024637154, "samidorphan_opioids": 2422812736, "samidorphan_buprenorphine": 557219649}, TXT=[["high-dose methotrexate", "who is at risk: high dose"], ["reduced kidney function", "who is at risk: kidney function"], ["planned blood tests are not being done", "who is at risk: inadequate monitoring"], ["often taken with an NSAID under a prescriber&rsquo;s supervision", "low-dose weekly use under supervision"], ["it does not mean that every combination must be avoided", "what Major means"], ["The studies measured methotrexate blood levels, not harm|blood-level findings, not documented harm|No study that gave diclofenac", "PK findings distinguished from harm"]];
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (P3-0b)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify(G[id][0].concat(G[id][1])),id+' members changed (AD0 §7): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (AD0 §7)'); need(p.source==='label'&&p.pmid===(PM[id]||''),id+' must cite exactly its labeling and studies');
    var t=p.mech+' '+p.monitor; TXT.forEach(function(x){need(new RegExp(x[0]).test(t),id+' text must state: '+x[1]);});
    need(/before adding an over-the-counter NSAID|before taking aspirin-containing/.test(p.monitor)&&/do not stop/i.test(p.monitor),id+' must advise checking before OTC use and not stopping medicines');});
  need(PR.methotrexate_aspirin&&/cannot tell which dose you take/.test(PR.methotrexate_aspirin.mech)&&/Graded A for anti-inflammatory doses only/.test(PR.methotrexate_aspirin.mech)&&/Low-dose aspirin for the heart is commonly taken with methotrexate, but it should still be reviewed with your prescriber/.test(PR.methotrexate_aspirin.mech),'aspirin pair must qualify the dose its evidence covers (AD0 §7)');
  need(PR.methotrexate_diclofenac&&/pooled/.test(PR.methotrexate_diclofenac.mech),'diclofenac pair must say why it is not A');
  function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  Object.keys(G).forEach(function(id){G[id][1].forEach(function(x){need(links('methotrexate',x)===id,'methotrexate + '+x+' must fire '+id+' only');});});
  var now={}; DI_DATA.forEach(function(d){if(PIN[d.id]!==undefined) now[d.id]=H32(JSON.stringify(d));});
  Object.keys(PIN).forEach(function(k){need(now[k]===PIN[k],k+' record changed (frozen in P3-0b)');});
  Object.keys(PINP).forEach(function(k){need(PR[k]&&H32(JSON.stringify(PR[k]))===PINP[k],k+' pair changed (frozen in P3-0b)');});
  // alias display (owner condition 5): abbreviations keep capitals; the search keys are unchanged
  var bi=-1; DI_DATA.forEach(function(d,i){if(d.id==='bactrim') bi=i;}); var cov=(typeof DI_COVERED!=='undefined'&&DI_COVERED[bi])||[];
  need(cov.indexOf('SMX-TMP')>=0&&cov.indexOf('SMX/TMP')>=0&&cov.indexOf('Smx-tmp')<0&&cov.indexOf('Smx/tmp')<0,'bactrim must list SMX-TMP and SMX/TMP in capitals (AD0 §7): '+cov.join(' | '));
  ['smx-tmp','smx/tmp','SMX-TMP','SMX/TMP'].forEach(function(q){var r=diSearchIndices(q); need(r.idx.length===1&&DI_DATA[r.idx[0]].id==='bactrim',q+' must still open bactrim only');});
})();
// MED-01 Phase 3 P3-0 (AD0): Lybalvi/samidorphan contraindication with opioids; methotrexate with TMP-SMX and sulfadiazine; identity.
(function(){function H32(t){var x=5381;for(var i=0;i<t.length;i++){x=(((x<<5)+x)^t.charCodeAt(i))>>>0;}return x;}
  var G={"samidorphan_opioids": [["samidorphan"], ["oxycodone", "hydrocodone", "morphine", "hydromorphone", "oxymorphone", "fentanyl", "codeine", "tapentadol", "meperidine", "methadone", "tramadol"]], "samidorphan_buprenorphine": [["samidorphan"], ["buprenorphine"]], "methotrexate_tmp_smx": [["methotrexate"], ["bactrim"]], "methotrexate_sulfadiazine": [["methotrexate"], ["sulfadiazine"]]}, SE={"samidorphan_opioids": "majorB", "samidorphan_buprenorphine": "majorA", "methotrexate_tmp_smx": "majorA", "methotrexate_sulfadiazine": "majorB"}, PIN={"olanzapine": 689064139, "bactrim": 3202178192, "sulfadiazine": 3708175451, "methotrexate_nobrands": 2303127097}, PINP={"opi_benz": 4281502643, "naltrexone_opioids": 3298796046, "ropinirole_dopamine_antagonist": 1051605825, "levodopa_dopamine_antagonist": 3892467171, "pramipexole_dopamine_antagonist": 1851641655, "metoclopramide_eps": 3843505128, "bact_warf": 4286875798, "dofetilide_contraindicated": 1213856529}, AL=["sulfatrim", "sulfamethoxazole/trimethoprim", "sulfamethoxazole-trimethoprim", "sulfamethoxazole and trimethoprim", "trimethoprim/sulfamethoxazole", "trimethoprim-sulfamethoxazole", "smx-tmp", "smx/tmp"];
  Object.keys(G).forEach(function(id){var p=PR[id]; need(p,id+' missing (P3-0)'); if(!p) return;
    need(JSON.stringify(p.groups)===JSON.stringify(G[id])&&JSON.stringify(p.drugs)===JSON.stringify(G[id][0].concat(G[id][1])),id+' members changed (AD0): '+JSON.stringify(p.groups));
    need(p.sev+p.ev===SE[id],id+' must be '+SE[id]+' (AD0)'); need(p.source==='label'&&p.pmid,id+' must cite its labeling and PMIDs');});
  // exclusions: loperamide, dextromethorphan and the antagonists are in no samidorphan pair; samidorphan is in no other pair (CYP3A4 inducers are P3-5)
  DDI_DATA.forEach(function(p){ if(p.drugs.indexOf('samidorphan')<0) return;
    need(['samidorphan_opioids','samidorphan_buprenorphine','samidorphan_rifampin','samidorphan_cyp3a4ind'].indexOf(p.id)>=0,'samidorphan appears in '+p.id+' (not part of P3-0 or MED-02 A3)');
    ['loperamide','dextromethorphan','naltrexone','naloxone'].forEach(function(x){need(p.drugs.indexOf(x)<0,p.id+' must not include '+x+' (AD0 exclusion)');});});
  // methotrexate: only the two P3-0 pairs; NSAIDs await the owner's severity decision; PPIs and penicillins are P3-11
  DDI_DATA.forEach(function(p){ if(p.drugs.indexOf('methotrexate')>=0) need(['methotrexate_tmp_smx','methotrexate_sulfadiazine','methotrexate_nsaids','methotrexate_diclofenac','methotrexate_aspirin','methotrexate_ppi'].indexOf(p.id)>=0,'methotrexate appears in '+p.id+' (not authorised in P3-0/P3-0b or MED-02 A2)');});
  ['samidorphan_opioids','samidorphan_buprenorphine'].forEach(function(id){ if(PR[id]) need(/Leaving loperamide out is not evidence that the two do not interact/.test(PR[id].mech),id+' must state that the loperamide exclusion is not evidence of no interaction');});
  need(PR.methotrexate_sulfadiazine&&/kept separate from TMP-SMX on purpose/.test(PR.methotrexate_sulfadiazine.mech),'sulfadiazine pair must state why it is separate from TMP-SMX');
  var SM=REV.samidorphan; need(SM&&SM.cls==='Opioid antagonist (in Lybalvi, with olanzapine)'&&!SM.sev&&!SM.ev&&!SM.mols,'samidorphan must be a reviewed-only entry');
  need(SM&&/not evidence that the two do not interact/.test(SM.basis),'samidorphan entry must state the loperamide exclusion is not evidence of no interaction');
  ['lybalvi','olanzapine/samidorphan','olanzapine and samidorphan'].forEach(function(k){var c=DI_COMBINATIONS[k]; need(c&&JSON.stringify(c.ids)==='["olanzapine","samidorphan"]'&&c.name==='Lybalvi'&&/must not be taken with opioid/.test(c.more||''),k+' must resolve to olanzapine + samidorphan with the opioid note');});
  need(DI_COMBINATIONS.sulfatrim&&JSON.stringify(DI_COMBINATIONS.sulfatrim.ids)==='["bactrim"]','sulfatrim must resolve to bactrim');
  need(DI_REVIEWED.length===33,'DI_REVIEWED count changed without review (expected 33: 15 + samidorphan in P3-0 + 5 contraceptive entries + atazanavir in P3-1a + 5 HIV in Pkg A3 + Vemlidy + 5 in MED-02)');
  need(Object.keys(DI_COMBINATIONS).length===208,'DI_COMBINATIONS count changed without review (expected 208: 149 + Lybalvi x3 + Sulfatrim in P3-0 + 38 contraceptive in P3-1a + 12 HIV in Pkg A3 + Jalyn x3 and Entadfi x2 in MED-02)');
  AL.forEach(function(a){need(DI_ALIASES[a]==='bactrim',a+' must open the bactrim record (AD0)');});
  need(DR.methotrexate&&JSON.stringify(DR.methotrexate.brands)==='["Trexall","Otrexup","Rasuvo","Xatmep","Jylamvo"]','methotrexate brands must be Trexall, Otrexup, Rasuvo, Xatmep, Jylamvo');
  var mx=JSON.parse(JSON.stringify(DR.methotrexate||{})); delete mx.brands;
  var now={olanzapine:H32(JSON.stringify(DR.olanzapine)),bactrim:H32(JSON.stringify(DR.bactrim)),sulfadiazine:H32(JSON.stringify(DR.sulfadiazine)),methotrexate_nobrands:H32(JSON.stringify(mx))};
  Object.keys(PIN).forEach(function(k){need(now[k]===PIN[k],k+' record changed (frozen in P3-0, AD0)');});
  Object.keys(PINP).forEach(function(k){need(PR[k]&&H32(JSON.stringify(PR[k]))===PINP[k],k+' pair changed (frozen in P3-0, AD0)');});
  need(DDI_DATA.filter(function(p){return p.drugs.indexOf('olanzapine')>=0;}).map(function(p){return p.id;}).join(',')==='ropinirole_dopamine_antagonist,levodopa_dopamine_antagonist,pramipexole_dopamine_antagonist,metoclopramide_eps','olanzapine pair set changed (P3-0 must not touch olanzapine)');
  function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  G.samidorphan_opioids[1].forEach(function(o){need(links('samidorphan',o)==='samidorphan_opioids','samidorphan + '+o+' must fire samidorphan_opioids only');});
  need(links('samidorphan','buprenorphine')==='samidorphan_buprenorphine','samidorphan + buprenorphine must fire samidorphan_buprenorphine only');
  ['loperamide','dextromethorphan','naltrexone','naloxone','olanzapine'].forEach(function(x){need(links('samidorphan',x)==='','samidorphan + '+x+' must fire nothing in P3-0');});   // rifampin/carbamazepine: MED-02 A3
  need(links('methotrexate','bactrim')==='methotrexate_tmp_smx'&&links('methotrexate','sulfadiazine')==='methotrexate_sulfadiazine','methotrexate + TMP-SMX / sulfadiazine must fire their own pairs');
  ['amoxicillin'].forEach(function(x){need(links('methotrexate',x)==='','methotrexate + '+x+' must fire nothing until authorised (P3-11)');});   // omeprazole: MED-02 A2
})();
// MED-01 Phase 2 R3 (AC3): one grade rule applied combination by combination (B named / mechanism-class; D outcome-group or generic enzyme class); identity notes.
(function(){function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  var E={"sotalol": {"doxepin": "antiarrhythmic_qt_class_sotalol", "ondansetron": "antiarrhythmic_qt_class_sotalol", "citalopram": "antiarrhythmic_qt"}, "quinidine": {"loperamide": "antiarrhythmic_qt_named_quinidine,loperamide_pgp", "ondansetron": "antiarrhythmic_qt_class_quinidine"}, "dofetilide": {"amitriptyline": "antiarrhythmic_qt_named_dofetilide", "loperamide": "antiarrhythmic_qt_class_dofetilide"}, "meperidine": {"ketoconazole": "meperidine_cyp3a4inh", "ritonavir": "ritonavir_meperidine"}, "oxycodone": {"diphenhydramine": "opi_diphen", "chlorpheniramine": "chlorpheniramine_opioids"}, "amlodipine": {"rifampin": "ccb_cyp3a4ind_class"}, "felodipine": {"carbamazepine": "felodipine_anticonvulsant_inducers", "rifampin": "felodipine_cyp3a4ind_class"}, "ciclesonide_inhaled": {"ketoconazole": "ciclesonide_ketoconazole", "itraconazole": "ciclesonide_cyp3a4inh_class"}, "levothyroxine": {"calcium_carbonate": "antacid_absorption", "aluminum_hydroxide": "antacid_absorption_al_mg"}}; Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var want=E[a][b],got=links(a,b); need(got.split(',').filter(function(x){return want.split(',').indexOf(x)>=0;}).length===want.split(',').length,a+' + '+b+' must fire '+want+' (fires '+(got||'none')+')');});});
  var SE={"antiarrhythmic_qt": "majorB", "antipsychotic_qt_antiarrhythmic": "majorB", "benz_diphen": "moderateB", "lama_anticholinergics": "moderateB", "opi_diphen": "moderateD", "carisoprodol_cns": "majorB", "carisoprodol_zdrug_class": "majorD", "chlorpheniramine_opioids": "moderateD", "pyrilamine_opioids": "moderateD", "eszopiclone_cyp3a4ind": "moderateD", "benzos_cyp3a4inh_partial": "moderateD", "ccb_cyp3a4ind": "moderateB", "felodipine_anticonvulsant_inducers": "moderateB", "ccb_cyp3a4ind_class": "moderateD", "felodipine_cyp3a4ind_class": "moderateD", "ciclesonide_ketoconazole": "moderateB", "ciclesonide_cyp3a4inh_class": "moderateD", "taxane_cyp3a4": "moderateB", "paclitaxel_cyp3a4_class": "moderateD", "nab_paclitaxel_cyp3a4_class": "moderateD", "meperidine_cyp3a4inh": "majorD", "opioids_cyp3a4inh": "majorA", "antacid_absorption": "moderateA", "antacid_absorption_al_mg": "moderateB", "tramadol_cyp3a4inh": "majorB", "tramadol_cyp3a4inh_class": "majorD", "cyclosporine_cyp3a4inh": "majorB", "cyclosporine_cyp3a4inh_class": "majorD", "cyclosporine_cyp3a4ind": "majorB", "cyclosporine_cyp3a4ind_class": "majorD", "methadone_cyp3a4ind": "moderateB", "methadone_cyp3a4ind_class": "moderateD", "tramadol_cyp3a4ind": "moderateB", "tramadol_cyp3a4ind_class": "moderateD"}; Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id],id+' must be '+SE[id]+' (change log AC3)');});
  var NEWP={"antiarrhythmic_qt_class_sotalol": ["sotalol", "ondansetron", "chlorpromazine", "escitalopram", "amitriptyline", "nortriptyline", "desipramine", "clomipramine", "doxepin", "trimipramine", "protriptyline", "loperamide"], "antiarrhythmic_qt_named_dofetilide": ["dofetilide", "chlorpromazine", "amitriptyline", "nortriptyline", "desipramine", "clomipramine", "doxepin", "trimipramine", "protriptyline"], "antiarrhythmic_qt_class_dofetilide": ["dofetilide", "ondansetron", "escitalopram", "loperamide"], "antiarrhythmic_qt_named_quinidine": ["quinidine", "chlorpromazine", "escitalopram", "amitriptyline", "nortriptyline", "desipramine", "clomipramine", "doxepin", "trimipramine", "protriptyline", "loperamide"], "antiarrhythmic_qt_class_quinidine": ["quinidine", "ondansetron"], "antiarrhythmic_qt_named_procainamide": ["procainamide", "amitriptyline", "nortriptyline", "desipramine", "clomipramine", "doxepin", "trimipramine", "protriptyline"], "antiarrhythmic_qt_class_procainamide": ["procainamide", "ondansetron", "chlorpromazine", "escitalopram", "loperamide"], "antiarrhythmic_qt_named_disopyramide": ["disopyramide", "amitriptyline", "nortriptyline", "desipramine", "clomipramine", "doxepin", "trimipramine", "protriptyline"], "antiarrhythmic_qt_class_disopyramide": ["disopyramide", "ondansetron", "chlorpromazine", "escitalopram", "loperamide"], "chlorpheniramine_opioids": ["chlorpheniramine", "oxycodone", "hydrocodone", "morphine", "hydromorphone", "oxymorphone", "fentanyl", "buprenorphine", "codeine", "tapentadol", "meperidine"], "pyrilamine_opioids": ["pyrilamine", "oxycodone", "hydrocodone", "morphine", "hydromorphone", "oxymorphone", "fentanyl", "buprenorphine", "codeine", "tapentadol", "meperidine"], "carisoprodol_zdrug_class": ["carisoprodol", "zolpidem", "eszopiclone"], "cyclosporine_cyp3a4ind_class": ["cyclosporine", "rifapentine", "apalutamide", "enzalutamide"], "cyclosporine_cyp3a4inh_class": ["cyclosporine", "posaconazole", "cobicistat", "isavuconazonium"], "methadone_cyp3a4ind_class": ["methadone", "rifapentine", "apalutamide", "enzalutamide"], "tramadol_cyp3a4ind_class": ["tramadol", "rifapentine", "stjohnswort", "apalutamide", "enzalutamide"], "tramadol_cyp3a4inh_class": ["tramadol", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone", "fluconazole", "isavuconazonium", "verapamil", "diltiazem", "grapefruit"], "felodipine_anticonvulsant_inducers": ["felodipine", "carbamazepine", "phenytoin"], "ccb_cyp3a4ind_class": ["amlodipine", "nicardipine", "isradipine", "rifampin", "rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "felodipine_cyp3a4ind_class": ["felodipine", "rifampin", "rifapentine", "stjohnswort", "apalutamide", "enzalutamide"], "ciclesonide_ketoconazole": ["ciclesonide_inhaled", "ketoconazole"], "ciclesonide_cyp3a4inh_class": ["ciclesonide_inhaled", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone"], "paclitaxel_cyp3a4_class": ["paclitaxel", "voriconazole", "posaconazole", "cobicistat", "rifapentine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "nab_paclitaxel_cyp3a4_class": ["nab-paclitaxel", "ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "ritonavir", "cobicistat", "nefazodone", "rifampin", "rifapentine", "carbamazepine", "phenytoin", "stjohnswort", "apalutamide", "enzalutamide"], "meperidine_cyp3a4inh": ["meperidine", "ketoconazole", "itraconazole", "voriconazole", "posaconazole", "clarithromycin", "cobicistat", "nefazodone", "fluconazole", "isavuconazonium", "erythromycin", "verapamil", "diltiazem", "grapefruit"], "antacid_absorption_al_mg": ["aluminum_hydroxide", "magnesium_hydroxide", "magnesium_carbonate", "levothyroxine"]}; Object.keys(NEWP).forEach(function(id){need(PR[id],id+' (AC3) missing'); if(PR[id]&&PR[id].ev==='D') need(/AC3/.test(PR[id].mech),id+' must state its AC3 grade basis');});
  DDI_DATA.forEach(function(p){if(p.id.indexOf('antiarrhythmic_qt_class_')===0) need(p.sev==='major'&&p.ev==='D',p.id+' must be Major/D');});
  need(/11716045/.test(PR.antacid_absorption.pmid)&&/7942938/.test(PR.antacid_absorption_al_mg.pmid),'antacid pairs must cite Singh 2001 / Liel 1994');
  need(DR.canagliflozin&&DR.canagliflozin.brands.indexOf('Invokana')>=0,'Invokana must open canagliflozin (W5-7)');
  need(/luvox cr/.test(Object.keys(DI_HISTORICAL_BRANDS).join('|')),'Luvox CR must carry the discontinued note');
  ['unisom','alka-seltzer','alka seltzer','vicks'].forEach(function(k){need(DI_AMBIGUOUS_FAMILY[k],k+' family note missing (AC3)');});
  need(/Drugs@FDA lists both Tybost strengths as discontinued/.test(DR.cobicistat.mech),'cobicistat must state the Tybost marketing status (W5-2)');
})();
// MED-01 Phase 2 R2 (AC2): clinical corrections; one documented QT member rule; beclomethasone nasal split; contraceptive names reach nothing.
(function(){function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  var E={"quinidine": {"diltiazem": "quinidine_diltiazem", "verapamil": "quinidine_verapamil"}, "amiodarone": {"nefazodone": "amiodarone_nefazodone"}, "quinidine_nuedexta": {"furosemide": "nuedexta_kdiuretic", "sertraline": "nuedexta_qt", "levofloxacin": "nuedexta_qt", "diltiazem": "nuedexta_cyp3a4_ecg"}, "ritonavir": {"prednisone": "ritonavir_prednisone", "prednisolone": "", "clarithromycin": "ritonavir_clarithromycin", "fluoxetine": "ritonavir_ssri_nefazodone", "nefazodone": "ritonavir_ssri_nefazodone", "rivaroxaban": "ritonavir_rivaroxaban", "rosuvastatin": "ritonavir_rosuvastatin", "atorvastatin": "ritonavir_statins_dose", "loperamide": "ritonavir_loperamide", "beclomethasone_nasal": "", "intranasal_steroids": "booster_nasal_steroids", "methadone": "hiv_methadone"}, "cobicistat": {"paroxetine": "cobicistat_ssri", "sertraline": "cobicistat_ssri", "prednisone": "", "methadone": "", "buprenorphine": "buprenorphine_cyp3a4inh", "beclomethasone_nasal": "", "clarithromycin": "cobicistat_clarithromycin"}, "loperamide": {"voriconazole": "loperamide_cyp3a4_other", "erythromycin": "loperamide_cyp3a4_other", "quinidine": "antiarrhythmic_qt_named_quinidine,loperamide_pgp", "itraconazole": "loperamide_pgp", "diltiazem": "loperamide_diltiazem"}, "dofetilide": {"metformin": "dofetilide_cationic_secretion", "amiloride": "dofetilide_cationic_secretion", "triamterene": "dofetilide_cationic_secretion"}, "methylprednisolone": {"ketoconazole": "methylprednisolone_azole", "itraconazole": "methylprednisolone_azole", "clarithromycin": "steroid_cyp3a4inh_labeled", "voriconazole": "steroid_azole_inferred"}, "dexamethasone": {"itraconazole": "dexamethasone_itraconazole", "ketoconazole": "dexamethasone_ketoconazole", "erythromycin": "steroid_cyp3a4inh_labeled", "posaconazole": "steroid_azole_inferred"}, "prednisone": {"ketoconazole": ""}, "sildenafil": {"tamsulosin": "pde5_alpha_blockers", "doxazosin": "pde5_alpha_blockers", "clonidine": "sild_antihyp", "amlodipine": ""}, "tadalafil": {"silodosin": "pde5_alpha_blockers", "alfuzosin": "pde5_alpha_blockers"}, "albuterol": {"rasagiline": "beta_agonist_maoi", "amitriptyline": "laba_tricyclics"}, "lopinavir": {"sertraline": "kaletra_qt", "chlorpromazine": "kaletra_qt", "ondansetron": "kaletra_qt"}, "efavirenz": {"levofloxacin": "efavirenz_qt", "chlorpromazine": "efavirenz_qt", "escitalopram": "efavirenz_qt", "sertraline": "efavirenz_sertraline", "tacrolimus": "efavirenz_immunosuppressants"}}; Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var want=E[a][b],got=links(a,b); need(got===want,a+' + '+b+' must fire '+(want||'no pair')+' (fires '+(got||'none')+')');});});
  var SE={"quinidine_diltiazem": "moderateA", "quinidine_verapamil": "moderateB", "amiodarone_nefazodone": "majorD", "nuedexta_kdiuretic": "moderateD", "ritonavir_prednisone": "minorA", "ritonavir_clarithromycin": "minorB", "ritonavir_ssri_nefazodone": "minorB", "cobicistat_ssri": "minorB", "ritonavir_rivaroxaban": "majorA", "ritonavir_rosuvastatin": "moderateA", "ritonavir_loperamide": "moderateA", "loperamide_cyp3a4_other": "moderateD", "dofetilide_cationic_secretion": "moderateD", "methylprednisolone_azole": "moderateA", "dexamethasone_itraconazole": "moderateA", "steroid_cyp3a4inh_labeled": "moderateB", "dexamethasone_ketoconazole": "moderateB", "steroid_azole_inferred": "moderateD", "pde5_alpha_blockers": "majorB", "sild_antihyp": "moderateD", "beta_agonist_maoi": "moderateD", "kaletra_qt": "moderateD", "efavirenz_qt": "moderateD", "nuedexta_qt": "majorD", "loperamide_pgp": "majorA", "rivaroxaban_cyp3a4inh": "majorB", "ritonavir_statins_dose": "moderateB", "booster_nasal_steroids": "moderateB", "triamcinolone_cyp3a4inh": "majorB"}; Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id],id+' must be '+SE[id]+' (change log AC2)');});
  var U=["amiodarone", "sotalol", "quinidine", "procainamide", "disopyramide", "dofetilide", "flecainide", "haloperidol", "chlorpromazine", "citalopram", "escitalopram", "ondansetron", "azithromycin", "erythromycin", "fluconazole", "propafenone", "clarithromycin", "ciprofloxacin", "levofloxacin", "moxifloxacin", "voriconazole", "posaconazole", "fluoxetine", "sertraline", "mirtazapine", "trazodone", "quetiapine", "paliperidone", "clozapine", "ziprasidone", "methadone", "hydroxyzine", "loperamide", "tacrolimus", "nilotinib", "ribociclib", "vemurafenib", "oxaliplatin", "abiraterone"], EFV=["amiodarone", "sotalol", "quinidine", "procainamide", "disopyramide", "dofetilide", "flecainide", "haloperidol", "chlorpromazine", "citalopram", "escitalopram", "ondansetron", "azithromycin", "erythromycin", "fluconazole", "propafenone", "ciprofloxacin", "levofloxacin", "moxifloxacin", "fluoxetine", "mirtazapine", "trazodone", "quetiapine", "paliperidone", "clozapine", "ziprasidone", "hydroxyzine", "loperamide", "nilotinib", "ribociclib", "vemurafenib", "oxaliplatin", "abiraterone"]; U.forEach(function(x){need(has('kaletra_qt',x),'kaletra_qt must include '+x+' (QT member rule)');}); EFV.forEach(function(x){need(has('efavirenz_qt',x),'efavirenz_qt must include '+x+' (QT member rule)');});
  // MED-01 P3-1a (AD1): the R2 boundary pin ("contraceptive names reach nothing") is retired; see the P3-1a block for what each name reaches
  need(DR.estrogen_hrt&&/Birth-control pills/.test(DR.estrogen_hrt.mech)&&/their own entries in The Table/.test(DR.estrogen_hrt.mech),'estrogen_hrt must say birth-control products are different medicines with their own entries');
  ['beconase aq','qnasl'].forEach(function(q){var r=diSearchIndices(q),ids=r.idx.map(function(i){return DI_DATA[i].id;}); need(ids.join()==='beclomethasone_nasal',q+' must open beclomethasone_nasal only ('+ids.join(',')+')');});
  need(DR.intranasal_steroids&&!/beclomethasone/i.test(DR.intranasal_steroids.drug+DR.intranasal_steroids.brands.join()),'intranasal_steroids must no longer include beclomethasone');
  need(/16284534/.test(PR.ritonavir_prednisone.pmid)&&/8841148/.test(PR.quinidine_diltiazem.pmid)&&/3709030/.test(PR.methylprednisolone_azole.pmid)&&/9797792/.test(PR.methylprednisolone_azole.pmid)&&/11103751/.test(PR.dexamethasone_itraconazole.pmid),'R2 Grade A citations must stay');
})();
// MED-01 Phase 2 R1 (AC1): cobicistat products, verapamil/diltiazem with the boosters, Kaletra labeling pairs, linezolid, contraception text.
(function(){function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  var E={"cobicistat": {"verapamil": "booster_ndhp_ccb", "diltiazem": "booster_ndhp_ccb"}, "ritonavir": {"verapamil": "booster_ndhp_ccb", "diltiazem": "booster_ndhp_ccb"}, "lopinavir": {"verapamil": "kaletra_pr", "diltiazem": "kaletra_pr", "metoprolol": "kaletra_pr", "atenolol": "kaletra_pr", "digoxin": "kaletra_pr", "amlodipine": "", "nifedipine": "", "oph_timolol": "", "metronidazole": "kaletra_solution_alcohol", "tinidazole": "kaletra_solution_alcohol"}, "linezolid": {"meperidine": "linezolid_serotonergic_opioids", "tramadol": "linezolid_serotonergic_opioids", "methadone": "linezolid_serotonergic_opioids", "tapentadol": "linezolid_serotonergic_opioids", "fentanyl": "linezolid_serotonergic_opioids", "oxycodone": "linezolid_other_opioids", "morphine": "linezolid_other_opioids", "buprenorphine": "linezolid_other_opioids", "sumatriptan": "linezolid_triptans_lithium", "lithium": "linezolid_triptans_lithium", "amphetamine": "linezolid_stimulants", "methylphenidate": "linezolid_stimulants", "bupropion": "linezolid_stimulants", "pseudoephedrine": "linezolid_pseudoephedrine", "phenylephrine": "linezolid_phenylephrine", "midodrine": "linezolid_midodrine", "rasagiline": "linezolid_rasagiline", "dextromethorphan": "linezolid_dextromethorphan", "quinidine_nuedexta": "linezolid_nuedexta", "cyclobenzaprine": "linezolid_maoi_class", "carbidopa_levodopa": "linezolid_maoi_class", "stjohnswort": "linezolid_maoi_class", "ondansetron": "", "oxymetazoline": "", "albuterol": "beta_agonist_maoi", "warfarin": "", "citalopram": "linezolid_serotonergic", "sertraline": "linezolid_serotonergic", "fluoxetine": "linezolid_serotonergic", "paroxetine": "linezolid_serotonergic", "duloxetine": "linezolid_serotonergic", "venlafaxine": "linezolid_serotonergic", "amitriptyline": "linezolid_serotonergic", "protriptyline": "linezolid_serotonergic", "trazodone": "linezolid_serotonergic", "mirtazapine": "linezolid_serotonergic", "buspirone": "linezolid_serotonergic", "ziprasidone": "linezolid_serotonergic"}}; Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var want=E[a][b],got=links(a,b); need(got===want,a+' + '+b+' must fire '+(want||'no pair')+' (fires '+(got||'none')+')');});});
  var SE={"booster_ndhp_ccb": "moderateB", "kaletra_pr": "moderateB", "kaletra_solution_alcohol": "minorB", "linezolid_serotonergic": "majorB", "linezolid_serotonergic_opioids": "majorB", "linezolid_other_opioids": "moderateB", "linezolid_triptans_lithium": "moderateB", "linezolid_stimulants": "majorB", "linezolid_pseudoephedrine": "moderateA", "linezolid_phenylephrine": "moderateB", "linezolid_midodrine": "majorB", "linezolid_rasagiline": "majorB", "linezolid_dextromethorphan": "minorA", "linezolid_nuedexta": "majorD", "linezolid_maoi_class": "moderateD"}; Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id],id+' must be '+SE[id]+' (change log AC1)');});
  ['genvoya','stribild','symtuza','prezcobix','evotaz','prezcobix ped','darunavir/cobicistat','atazanavir/cobicistat'].forEach(function(q){var r=diSearchIndices(q),c0=diComboFor(q)||{ids:[]},ids=r.idx.map(function(i){return DI_DATA[i].id;}).concat(c0.ids.filter(function(x){return DR[x];})).filter(function(x,i,a){return a.indexOf(x)===i;});   // onDiSearch adds the combination's ingredient records
    need(ids.join()==='cobicistat','"'+q+'" must open the cobicistat record only (opens '+ids.join(',')+')'); var c=diComboFor(q);
    need(c&&((c.notrev&&c.notrev.length&&/not yet in The Table/.test(diComboNote(c)))||(/its own drug interactions are not yet reviewed/.test(diComboNote(c)))),q+': the note must say which ingredients are not yet in The Table or are reviewed only (atazanavir P3-1a; darunavir, elvitegravir MED-02 A4)');});
  ['darunavir','atazanavir','elvitegravir','tenofovir alafenamide','prezista','reyataz','vitekta'].forEach(function(q){var r=diSearchIndices(q); need(!r.idx.length,'"'+q+'" must not open any interaction record (Phase 3)');});
  need(/Elvitegravir binds to antacids/.test(diComboNote(diComboFor('genvoya')))&&/Atazanavir needs stomach acid/.test(diComboNote(diComboFor('evotaz'))),'Genvoya/Evotaz notes must name the uncovered partner-drug interactions');
  need(/Hormonal birth control/.test(DR.ritonavir.monitor)&&/Hormonal birth control/.test(DR.cobicistat.monitor)&&/Hormonal birth control/.test(DR.efavirenz.monitor),'ritonavir, cobicistat and efavirenz cards must carry the contraception text (W5-11)');
  need(/11361053/.test(PR.linezolid_pseudoephedrine.pmid)&&/11361053/.test(PR.linezolid_dextromethorphan.pmid)&&/36534400/.test(PR.linezolid_serotonergic.pmid)&&/12022894/.test(PR.kaletra_solution_alcohol.pmid),'R1 citations (Hendershot 2001, Bai 2022, Visapaa 2002) must stay');
  need(!has('kaletra_pr','amlodipine')&&!has('kaletra_pr','nifedipine'),'kaletra_pr: dihydropyridines do not slow AV conduction');
  need(/oral solution/i.test(PR.kaletra_solution_alcohol.label),'kaletra_solution_alcohol must say it applies to the oral solution');
})();
// MED-01 WS5c (AB3): P-glycoprotein pairs with the boosters; efavirenz's own labeling.
(function(){function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  var E={"ritonavir": {"loperamide": "ritonavir_loperamide", "dabigatran": "ritonavir_dabigatran"}, "cobicistat": {"loperamide": "cobicistat_loperamide", "dabigatran": "cobicistat_dabigatran"}, "efavirenz": {"voriconazole": "efavirenz_voriconazole", "itraconazole": "efavirenz_itraconazole", "ketoconazole": "efavirenz_ketoconazole", "posaconazole": "efavirenz_posaconazole", "warfarin": "efavirenz_warfarin", "bupropion": "efavirenz_bupropion", "sertraline": "efavirenz_sertraline", "atorvastatin": "efavirenz_statins", "pravastatin": "efavirenz_statins", "simvastatin": "efavirenz_statins", "clarithromycin": "efavirenz_clarithromycin", "cyclosporine": "efavirenz_immunosuppressants", "tacrolimus": "efavirenz_immunosuppressants", "sirolimus": "efavirenz_immunosuppressants", "diltiazem": "efavirenz_diltiazem", "nifedipine": "efavirenz_ccb", "verapamil": "efavirenz_ccb", "rifabutin": "efavirenz_inducers", "methadone": "hiv_methadone", "lorazepam": "", "famotidine": "", "cetirizine": "", "fluoxetine": "efavirenz_qt", "amiodarone": "efavirenz_qt", "sotalol": "efavirenz_qt", "quinidine": "efavirenz_qt", "procainamide": "efavirenz_qt", "disopyramide": "efavirenz_qt", "dofetilide": "efavirenz_qt", "flecainide": "efavirenz_qt", "haloperidol": "efavirenz_qt", "chlorpromazine": "efavirenz_qt", "citalopram": "efavirenz_qt", "escitalopram": "efavirenz_qt", "ondansetron": "efavirenz_qt", "azithromycin": "efavirenz_qt", "erythromycin": "efavirenz_qt", "fluconazole": "efavirenz_qt"}}; Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var want=E[a][b],got=links(a,b); need(got===want,a+' + '+b+' must fire '+(want||'no pair')+' (fires '+(got||'none')+')');});});
  var SE={"cobicistat_loperamide": "moderateD", "ritonavir_dabigatran": "majorA", "cobicistat_dabigatran": "majorB", "efavirenz_voriconazole": "majorA", "efavirenz_itraconazole": "moderateA", "efavirenz_ketoconazole": "moderateB", "efavirenz_posaconazole": "majorA", "efavirenz_warfarin": "moderateB", "efavirenz_bupropion": "moderateA", "efavirenz_sertraline": "moderateA", "efavirenz_statins": "minorA", "efavirenz_clarithromycin": "moderateA", "efavirenz_immunosuppressants": "moderateB", "efavirenz_diltiazem": "minorA", "efavirenz_ccb": "minorB", "efavirenz_qt": "moderateD", "loperamide_pgp": "majorA", "dabigatran_pgp": "majorB"}; Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id],id+' must be '+SE[id]+' (change log AB3)');});
  need(/11719726/.test(PR.ritonavir_loperamide.pmid)&&/no evidence of P-glycoprotein involvement/.test(PR.ritonavir_loperamide.mech)&&!/11719726/.test(PR.loperamide_pgp.pmid),'ritonavir_loperamide must cite Tayrouz 2001 with its metabolic (not P-gp) finding');   // MED-01 P2R2
  need(!has('dabigatran_pgp','ritonavir')&&!has('dabigatran_pgp','cobicistat'),'the boosters have their own dabigatran pairs');
  need(/darunavir and cobicistat no change/.test(PR.cobicistat_dabigatran.mech),'cobicistat_dabigatran must state the darunavir exception (Tybost)');
  need(/18025525/.test(PR.efavirenz_voriconazole.pmid)&&/not at standard doses/.test(PR.efavirenz_voriconazole.label),'efavirenz_voriconazole: Liu 2008 and the standard-dose rule');
  need(/efavirenz lowered rifabutin by about 38%/.test(PR.efavirenz_inducers.mech),'efavirenz_inducers must say efavirenz lowers rifabutin (increase rifabutin 50%)');
  DDI_DATA.forEach(function(p){ if(/^efavirenz_/.test(p.id)) need(p.drugs.indexOf('ritonavir')<0&&p.drugs.indexOf('cobicistat')<0,p.id+' is efavirenz-specific but contains a booster');});})();
// MED-01 WS5b (AB2): ritonavir and cobicistat decided separately, pair by pair, from their own labelings and studies.
(function(){function links(a,b){return DDI_DATA.filter(function(p){var g=p.groups||[p.drugs]; return g.length===2&&((g[0].indexOf(a)>=0&&g[1].indexOf(b)>=0)||(g[1].indexOf(a)>=0&&g[0].indexOf(b)>=0));}).map(function(p){return p.id;}).sort().join(',');}
  var E={ritonavir:{"digoxin": "ritonavir_digoxin", "warfarin": "ritonavir_warfarin", "clopidogrel": "ritonavir_clopidogrel", "voriconazole": "ritonavir_voriconazole", "ketoconazole": "ritonavir_ketoconazole", "itraconazole": "ritonavir_itraconazole", "isavuconazonium": "ritonavir_isavuconazonium", "rifabutin": "ritonavir_rifabutin", "dexamethasone": "booster_corticosteroids", "methylprednisolone": "booster_corticosteroids", "betamethasone": "booster_corticosteroids", "intranasal_steroids": "booster_nasal_steroids", "clozapine": "ritonavir_clozapine", "risperidone": "booster_risperidone", "desipramine": "ritonavir_desipramine", "amitriptyline": "ritonavir_tca", "nortriptyline": "ritonavir_tca", "doxepin": "ritonavir_tca", "metoprolol": "ritonavir_betablockers", "oph_timolol": "ritonavir_betablockers", "carvedilol": "", "lamotrigine": "ritonavir_lamotrigine", "valproate": "ritonavir_valproate", "bupropion": "ritonavir_bupropion", "theophylline": "ritonavir_theophylline", "meperidine": "ritonavir_meperidine", "efavirenz": "ritonavir_efavirenz", "oxcarbazepine": "", "rosuvastatin": "ritonavir_rosuvastatin", "amiodarone": "ritonavir_antiarrhythmics", "flecainide": "ritonavir_antiarrhythmics", "prednisone": "ritonavir_prednisone", "prednisolone": "", "sertraline": "ritonavir_ssri_nefazodone"},cobicistat:{"carbamazepine": "cobicistat_inducers", "phenobarbital": "cobicistat_inducers", "phenytoin": "cobicistat_inducers", "rifampin": "cobicistat_inducers", "stjohnswort": "cobicistat_inducers", "rifapentine": "cobicistat_inducers_named_combo", "apalutamide": "cobicistat_inducers_named_combo", "primidone": "cobicistat_inducers_unnamed", "enzalutamide": "cobicistat_inducers_unnamed", "oxcarbazepine": "cobicistat_weak_inducers", "eslicarbazepine": "cobicistat_weak_inducers", "efavirenz": "cobicistat_efavirenz", "digoxin": "cobicistat_digoxin", "warfarin": "cobicistat_warfarin", "clopidogrel": "cobicistat_clopidogrel", "voriconazole": "cobicistat_voriconazole", "ketoconazole": "cobicistat_ketoconazole_itraconazole", "itraconazole": "cobicistat_ketoconazole_itraconazole", "isavuconazonium": "cobicistat_isavuconazonium", "rifabutin": "cobicistat_rifabutin", "dexamethasone": "booster_corticosteroids", "intranasal_steroids": "booster_nasal_steroids", "risperidone": "booster_risperidone", "desipramine": "cobicistat_desipramine", "imipramine": "cobicistat_tca", "carvedilol": "cobicistat_betablockers", "metoprolol": "cobicistat_betablockers", "amiodarone": "amiodarone_cobicistat", "flecainide": "flecainide_cobicistat", "rosuvastatin": "cobicistat_rosuvastatin", "clarithromycin": "cobicistat_clarithromycin", "lamotrigine": "", "valproate": "", "bupropion": "", "theophylline": "", "meperidine": "meperidine_cyp3a4inh", "methadone": "", "clozapine": "", "canagliflozin": ""}};
  Object.keys(E).forEach(function(a){Object.keys(E[a]).forEach(function(b){var want=E[a][b].split(',').filter(String).sort().join(','),got=links(a,b); need(got===want,a+' + '+b+' must fire '+(want||'no pair')+' (fires '+(got||'none')+')');});});
  var SE={"cobicistat_inducers": "majorB", "cobicistat_inducers_named_combo": "majorB", "cobicistat_inducers_unnamed": "majorD", "cobicistat_weak_inducers": "moderateB", "cobicistat_efavirenz": "majorB", "ritonavir_efavirenz": "moderateA", "ritonavir_digoxin": "majorA", "cobicistat_digoxin": "moderateA", "ritonavir_warfarin": "moderateA", "cobicistat_warfarin": "moderateB", "ritonavir_clopidogrel": "majorA", "cobicistat_clopidogrel": "majorB", "ritonavir_voriconazole": "majorA", "cobicistat_voriconazole": "moderateB", "ritonavir_ketoconazole": "moderateA", "ritonavir_itraconazole": "moderateB", "cobicistat_ketoconazole_itraconazole": "moderateB", "ritonavir_isavuconazonium": "moderateA", "cobicistat_isavuconazonium": "majorD", "ritonavir_rifabutin": "majorA", "cobicistat_rifabutin": "majorB", "booster_corticosteroids": "moderateB", "booster_nasal_steroids": "moderateB", "ritonavir_clozapine": "moderateB", "booster_risperidone": "moderateB", "ritonavir_desipramine": "moderateA", "ritonavir_tca": "moderateB", "cobicistat_desipramine": "moderateA", "cobicistat_tca": "moderateB", "ritonavir_betablockers": "minorB", "cobicistat_betablockers": "minorB", "ritonavir_lamotrigine": "moderateA", "ritonavir_valproate": "moderateB", "ritonavir_bupropion": "moderateA", "ritonavir_theophylline": "moderateA", "ritonavir_meperidine": "majorA", "amiodarone_cobicistat": "majorB", "flecainide_cobicistat": "moderateB", "cobicistat_rosuvastatin": "moderateB", "cobicistat_clarithromycin": "moderateB"}; Object.keys(SE).forEach(function(id){need(PR[id]&&PR[id].sev+PR[id].ev===SE[id],id+' must be '+SE[id]+' (change log AB2)');});
  var CI={"ritonavir_digoxin": "15229466", "ritonavir_clopidogrel": "29696643", "cobicistat_clopidogrel": "29453687", "ritonavir_voriconazole": "17646413", "ritonavir_lamotrigine": "16890577", "ritonavir_bupropion": "20484617", "ritonavir_meperidine": "10809341", "booster_nasal_steroids": "10615321", "cobicistat_warfarin": "27898595"}; Object.keys(CI).forEach(function(id){need(PR[id]&&PR[id].pmid.indexOf(CI[id])>=0,id+' must cite PMID '+CI[id]);});
  // a ritonavir-specific pair never contains cobicistat and vice versa; induction pairs say cobicistat does not induce
  DDI_DATA.forEach(function(p){ if(/^ritonavir_/.test(p.id)) need(p.drugs.indexOf('cobicistat')<0,p.id+' is ritonavir-specific but contains cobicistat');
    if(/^cobicistat_/.test(p.id)) need(p.drugs.indexOf('ritonavir')<0,p.id+' is cobicistat-specific but contains ritonavir');});
  ['ritonavir_lamotrigine','ritonavir_valproate','ritonavir_bupropion','ritonavir_theophylline','ritonavir_voriconazole'].forEach(function(id){need(PR[id]&&/does not induce/.test(PR[id].mech),id+' must say cobicistat does not induce these enzymes');});
  need(!has('canagliflozin_ugt_inducers','cobicistat')&&!has('hiv_methadone','cobicistat'),'ritonavir induction pairs (canagliflozin, methadone) must not include cobicistat');
  need(/normeperidine rises/.test(PR.opioids_cyp3a4inh.mech)&&/normeperidine rises/.test(PR.meperidine_cyp3a4inh.mech)&&/raised its metabolite normeperidine by 47%/.test(PR.ritonavir_meperidine.mech),'meperidine: ritonavir lowers meperidine and raises normeperidine; the shared opioid pair must point to it');
  need(/Beconase AQ, Qnasl\) is not included/.test(PR.booster_nasal_steroids.mech)&&!has('booster_nasal_steroids','beclomethasone_nasal'),'booster_nasal_steroids must exclude beclomethasone (named as an alternative; own record since P2R2)');
  need(/the Norvir labeling in turn says a lower dronabinol dose/.test(DR.ritonavir.mech)&&DR.ritonavir.sev+DR.ritonavir.ev==='moderateB','ritonavir record: Norvir names dronabinol; Moderate/B kept');})();
// MED-01 WS5a (AB1): HIV identities. An ingredient name never opens a different medicine through a combination's generic string.
if(DR.efavirenz) need(!DR.efavirenz.brands.some(function(b){return /\//.test(b);}),'efavirenz brands must not carry combination generic strings (they open Efavirenz for emtricitabine/tenofovir)');
['emtricitabine','tenofovir','lamivudine','emtricitabine/tenofovir','lamivudine/tenofovir','tenofovir disoproxil','tenofovir alafenamide','truvada','descovy','biktarvy'].forEach(function(q){
  var r=diSearchIndices(q); need(!r.idx.length,'"'+q+'" must not open any interaction record (reaches '+r.idx.map(function(i){return DI_DATA[i].id;}).join(',')+')');});
[['efavirenz/emtricitabine/tenofovir disoproxil fumarate','efavirenz,emtricitabine,tenofovir_df'],['efavirenz/lamivudine/tenofovir disoproxil fumarate','efavirenz,lamivudine,tenofovir_df'],
 ['kaletra oral solution','lopinavir,ritonavir'],['paxlovid pack','nirmatrelvir,ritonavir']].forEach(function(p){var c=diComboFor(p[0]); need(c&&c.ids.join()===p[1],p[0]+' must resolve to '+p[1]);});
['norvir oral solution','norvir oral powder','norvir powder','ritonavir oral powder'].forEach(function(q){var r=diSearchIndices(q); need(r.idx.length===1&&DI_DATA[r.idx[0]].id==='ritonavir'&&r.exact[r.idx[0]],q+' must open ritonavir');});
need(/Sustiva is a discontinued brand/.test(diAmbiguityNote('sustiva'))&&/Atripla is a discontinued brand/.test(diAmbiguityNote('atripla'))&&/Symfi Lo[^.]*discontinued/.test(diAmbiguityNote('symfi lo'))&&/Symfi \(600&nbsp;mg\) is still marketed/.test(diAmbiguityNote('symfi')),'Sustiva, Atripla and Symfi Lo must carry their discontinued notes (Symfi itself is marketed)');
need(!/discontinued/.test(diAmbiguityNote('kaletra')+diAmbiguityNote('paxlovid')+diAmbiguityNote('norvir')+diAmbiguityNote('tybost')),'Kaletra, Paxlovid, Norvir and Tybost are marketed');
if(DR.cobicistat) need(/not interchangeable with ritonavir/.test(DR.cobicistat.mech)&&/not an enzyme inducer/.test(DR.cobicistat.mech)&&/not an enzyme inducer/.test(DR.cobicistat.cls)&&DR.cobicistat.sev==='moderate'&&DR.cobicistat.ev==='D','cobicistat record must state it is not an inducer and not interchangeable with ritonavir (Moderate/D kept)');
var TD=DI_REVIEWED.filter(function(x){return x.id==='tenofovir_df';})[0]; need(TD&&/Tenofovir alafenamide[^.]*different form/.test(TD.basis),'the tenofovir entry must say tenofovir alafenamide is a different, unreviewed form');
need(has('pred_nsaid','triamcinolone')&&PR.pred_nsaid.groups,'pred_nsaid must pair triamcinolone and prednisone with NSAIDs (and not with each other)');
R.tableN=Object.keys(T).length;
print(JSON.stringify(R));
'''


def _fn_src(src, header):
    i = src.index(header)
    j = src.index('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{': depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0: return src[i:k + 1]
    raise ValueError(header)


def check_medication_identity(src, data):
    # MED-01 Phase 4: informational suggestion rows must be text blocks (bold phrases split flex rows into columns)
    if '#gxOverlay .dj-sug.none{display:block;' not in src or '.med-suggestion-item[style*="cursor:default"]{display:block}' not in src:
        fail('medication identity', 'Guided Match / My Medication List informational rows must use display:block (Phase 4 CSS fix)')
    # MED-01 Pkg A1 (DRUG-19 pattern): {major:0,...} read through `||` ranks Major with unknown severities (0 is falsy)
    for ln, line in enumerate(src.split('\n'), 1):
        if re.search(r'\{\s*major\s*:\s*0\b', line) and '||' in line:
            fail('medication identity', 'line %d: a severity map starting at major:0 is read through || (Major sorts last)' % ln)
    for fn in ('function findDdiInteractions', 'function getMolDrugInteractions'):
        i = src.find(fn); body = src[i:i + 1500] if i >= 0 else ''
        if 'var o={major:1,moderate:2,minor:3}; ' not in body or '(o[a.sev]||4)-(o[b.sev]||4)' not in body:
            fail('medication identity', fn[9:] + ' must sort Major first: {major:1,moderate:2,minor:3} with ||4 (Pkg A1)')
    """MED-01 (guard 49): every medication name resolves to its own medicine — never to a class or another drug.

    Owner-approved 2026-10-30, after the Phase 1 audit found 281 of 292 records reachable through words in
    their therapeutic-class text, brands filed under the wrong ingredient (Onfi, Dyazide, Maxzide, Ezallor,
    Roszet, Robitussin) and a combined antipsychotic record. BEHAVIOURAL: runs the app's own index builder and
    search in JavaScriptCore over the WHOLE database and asserts that (1) no therapeutic-class text is a search
    key unless it is also a real name; (2) every generic, brand and alias ranks its own record first, and no
    other record is an exact match unless that record names the query itself (combinations, formulations);
    (3) the identities verified in MED-01 resolve to exactly the right records; (4) family brands (Robitussin,
    Mucinex, Dramamine) are never an exact match and always carry a note; (5) every alias targets a record;
    (6) MED-01 Stage 1b: the nine antipsychotics keep the QT, dopamine and metoclopramide pair memberships decided
    drug by drug from their labels, and quetiapine, lurasidone, triamcinolone and eplerenone keep their CYP3A4 pairs.
    """
    name = 'medication identity'
    if 'addKeyFor(entry.cls' in src:
        fail(name, 'the therapeutic-class text is being indexed as a search key again')
    try:
        di = js_literal(src, 'var DI_DATA = [', '[')
        rv = js_literal(src, 'var DI_REVIEWED = [', '[')
        ddi = js_literal(src, 'var DDI_DATA = [', '[')
        al = js_literal(src, 'var DI_ALIASES = {', '{')
        fam = js_literal(src, 'var DI_AMBIGUOUS_FAMILY = {', '{')
        cmb = js_literal(src, 'var DI_COMBINATIONS = {', '{')
        famp = js_literal(src, 'var DI_FAMILY_PRODUCTS = {', '{')   # MED-01 3C-1
        hist = js_literal(src, 'var DI_HISTORICAL_BRANDS = {', '{')   # MED-01 3C-2b
        msg = re.search(r"var DI_AMBIGUOUS_MSG = '[^\n]*';", src).group(0)
        # MED-01 3C-3: misspelling/abbreviation map, formulation words and class terms
        msg += '\nvar DI_SEARCH_FIX=%s;\nvar DI_CLASS_TERMS=%s;\nvar DI_EXACT_ABBREV=%s;\n' % (js_literal(src, 'var DI_SEARCH_FIX = {', '{'), js_literal(src, 'var DI_CLASS_TERMS = {', '{'), js_literal(src, 'var DI_EXACT_ABBREV = {', '{'))   # MED-01 Pkg A4
        msg += re.search(r"var DI_FORM_WORDS = /[^\n]*/;", src).group(0)
        fns = [_fn_src(src, 'function ' + f + '(') for f in
               ('diFlatName', 'diFormBase', 'diSearchRaw', 'diClassTerm', 'diClassFirst',
                'diTokenStarts', 'diKeyMatches', 'diExactName', 'diSearchIndices', 'diRankBy', 'diClassMatches', 'diComboFor', 'diComboNote', 'diFamilyCombos', 'diAmbiguityNote', 'diReviewedMatches')]
        build = _fn_src(src, '(function buildDiIndex() {') + ')();'
    except (ValueError, AttributeError):
        fail(name, 'search module not found (zero-match tripwire)')
        return
    prog = '\n'.join(['var DI_DATA=%s;' % di, 'var DDI_DATA=%s;' % ddi, 'var DI_REVIEWED=%s;' % rv, 'var DI_ALIASES=%s;' % al,
                      'var DI_INDEX={}, DI_COVERED={};', 'var DI_AMBIGUOUS_FAMILY=%s;' % fam, 'var DI_COMBINATIONS=%s;' % cmb, 'var DI_FAMILY_PRODUCTS=%s;' % famp, 'var DI_HISTORICAL_BRANDS=%s;' % hist, msg]
                     + fns + [build, MED_IDENTITY_TEST])
    tmp = os.path.join(HERE, '_preflight_med49.js')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(prog)
    try:
        res = subprocess.run([JSC, tmp], capture_output=True, text=True, timeout=180)
    finally:
        os.remove(tmp)
    try:
        r = json.loads((res.stdout or '').strip().splitlines()[-1])
    except (ValueError, IndexError):
        fail(name, 'identity test did not run: ' + (res.stderr or res.stdout)[:300])
        return
    if r['names'] < 1400:
        fail(name, 'only %d medication names were tested — expected about 1,560' % r['names'])
    for key, label in [('classKeys', 'therapeutic-class text indexed as a name'),
                       ('rank', 'a name does not rank its own medication first'),
                       ('exactOther', 'another medication is an exact match for a name it does not carry'),
                       ('table', 'verified identity broken'), ('amb', 'family-brand handling'),
                       ('missingTarget', 'alias points to no record'),
                       ('pairs', 'antipsychotic / corticosteroid / antiretroviral interaction decision broken'),
                       ('combo', 'combination product shown as a single ingredient'),
                       ('cyp', 'CYP3A4 grouping can miss a medicine')]:
        for x in r[key][:12]:
            fail(name, '%s: %s' % (label, x))
    for fn, needle in (('medAddSearch', "addMedsToList([\\'' + combo.ids.join("), ('djRenderMedSearch', 'diComboFor(q)'), ('onDiSearch', 'if (cmbo) cmbo.ids.forEach(')):   # MED-01 WS5a: single search too   # MED-01 3C: the combination row itself, not the family rows
        try:
            body = _fn_src(src, 'function %s(' % fn)
        except ValueError:
            body = ''
        if needle not in body:
            fail(name, 'combination product: %s no longer offers the one-step "add all ingredients" row' % fn)
    if not any(r[k] for k in ('classKeys', 'rank', 'exactOther', 'table', 'amb', 'missingTarget', 'pairs', 'combo', 'cyp')):
        note('medication identity: %d names each resolve to their own medicine first; no class text indexed; '
             '%d verified identities exact; family brands flagged; antipsychotic and antiretroviral pair decisions intact; combination products list every ingredient' % (r['names'], r['tableN']))


def check_build_identifier(src, data):
    """RELEASE-01 (guard 47): every approved release carries one unique, visible build identifier.

    Owner-approved 2026-10-26. The footer label and the <meta name="v2-build"> tag must agree, use the form
    YYYY.MM.DD-N, and have a release record (V4 Build/Release_Records/<id>.md) listing the changes it contains.
    """
    name = 'build identifier'
    m1 = re.search(r'<meta name="v2-build" content="([^"]+)">', src)
    m2 = re.search(r'<div id="buildId"[^>]*>Build ([^<]+)</div>', src)
    if not m1 or not m2:
        fail(name, 'build identifier missing from the head or the footer (zero-match tripwire)')
        return
    if m1.group(1) != m2.group(1):
        fail(name, 'head (%s) and footer (%s) build identifiers differ' % (m1.group(1), m2.group(1)))
    bid = m1.group(1)
    if not re.fullmatch(r'20\d\d\.\d\d\.\d\d-\d+', bid):
        fail(name, 'build identifier %r is not YYYY.MM.DD-N' % bid)
    rec = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Release_Records', bid + '.md')
    if not os.path.exists(rec):
        fail(name, 'no release record for build %s (expected %s)' % (bid, rec))
    if src.count('id="buildId"') != 1:
        fail(name, 'the footer build label must appear exactly once')
    if os.path.exists(rec):
        note('build identifier: %s (footer + head agree; release record present)' % bid)

def check_molecule_profile_terms(src, data):
    """SCI-05 (guard 48): combination labels describe composition, never a demonstrated interaction.

    Owner-approved 2026-10-26. The explorer and Guided Match label rules count molecule classes only, so they
    must use "Multi-/Two-/Single-class molecule profile" and carry the "not evidence of an interaction"
    qualifier. The benefit phrases that implied clinical value from pathway coverage stay removed, and no
    user-facing Guided Match, shelf, medication or print string calls a molecule "recommended".
    """
    name = 'molecule profile terms'
    labels = ("'Multi-class molecule profile'", "'Two-class molecule profile'", "'Single-class molecule profile'")
    rules = [ln for ln in src.split('\n') if 'molecule profile' in ln and '?' in ln and ('effectType =' in ln or 'effType=' in ln)]
    if len(rules) != 2:
        fail(name, 'expected 2 molecule-profile label rules (explorer + Guided), found %d (zero-match tripwire)' % len(rules))
    for ln in rules:
        for lab in labels:
            if lab not in ln:
                fail(name, 'label rule missing %s: %s' % (lab, ln.strip()[:120]))
    for bad in ("'Potentially Synergistic'", "'Potentially synergistic'", "'Complementary'", "'Additive'",
                'Interaction type: <strong>'):
        if bad in src:
            fail(name, 'interaction label reintroduced: %s' % bad)
    for q in ('not evidence that these molecules interact or work better together',
              'not a demonstrated interaction or benefit'):
        if src.count(q) != 1:
            fail(name, 'qualifier missing or duplicated: %r (found %d)' % (q, src.count(q)))
    for phrase in ('broader biological coverage', 'potentially addressing the central and peripheral',
                   "' that may be relevant to '", 'distinct biological pathway', 'to enhance medicinal properties',
                   'A suggested combination for'):
        if phrase in src:
            fail(name, 'benefit wording reintroduced: %r' % phrase)
    for phrase in ('<span class="recflag">recommended</span>', "+' recommended — '", 'recommended for you',
                   'not among the molecules recommended for'):
        if phrase in src:
            fail(name, 'recommendation wording reintroduced: %r' % phrase)
    if src.count('<span class="recflag">research-linked</span>') != 1 or src.count("research-linked molecules — '") != 2:
        fail(name, 'research-linked replacements missing (badge 1, shelf cards 2)')
    note('molecule profile terms: 2 label rules use composition terms with qualifiers; no synergy/additive labels, '
         'benefit phrases or "recommended" wording on Guided, shelf, medication or print surfaces')

def check_research_only_cancer(src, data):
    """SCI-02 (guard 42): the 13 malignancy topics are research-only — never a ranked recommendation.

    Owner-approved 2026-10-24. None of the 148 molecule–cancer pairings has controlled human evidence of
    antitumor efficacy, yet Cancer / Anti-Tumor ranked Δ9-THC "Primary" and Guided Match's Cancer Symptom
    Support "Skip" button led straight to that list. Asserts the topic set, every entry point's branch
    (tiles, Table panel, Table print, Entourage, Guided browse / scRec / print), the qualifier and ASCO
    wording, and that Cancer Symptom Support offers no skip.
    """
    name = 'research-only cancer'
    want = {'cancer', 'glioblastoma', 'breast-cancer', 'lung-cancer', 'colorectal', 'leukemia', 'prostate-cancer',
            'melanoma', 'pancreatic-cancer', 'liver-cancer', 'ovarian-cancer', 'cervical-cancer', 'testicular-cancer'}
    m = re.search(r"var RESEARCH_ONLY_CONDS = \{(.*?)\};", src, re.S)
    if not m:
        fail(name, 'RESEARCH_ONLY_CONDS not found (zero-match tripwire)')
        return
    got = set(re.findall(r"'([a-z-]+)':1", m.group(1)))
    if got != want:
        fail(name, 'research-only set differs from the 13 malignancy topics: missing %s, extra %s'
             % (sorted(want - got), sorted(got - want)))
    ids = {c.get('id') for c in data['CONDITIONS']}
    for c in sorted(want - ids):
        fail(name, 'malignancy topic "%s" no longer exists in CONDITIONS' % c)
    note_m = re.search(r"var RESEARCH_ONLY_NOTE = (.*?);\n", src, re.S)
    nt = note_m.group(1) if note_m else ''
    for needle in ('not treatment recommendations', 'does not establish effectiveness in people',
                   'recommends against using cannabis or', 'cancer-directed treatment outside a clinical trial'):
        if needle not in nt:
            fail(name, 'RESEARCH_ONLY_NOTE lost "%s"' % needle)
    checks = [
        ("if (isResearchOnlyCond(condId)) {          // SCI-02: unranked", 'tiles: unranked neutral highlight'),
        ("if (_ro) {                                   // SCI-02: research-only", 'Table panel: research-only branch'),
        ("if (isResearchOnlyCond(cond.id)) {           // SCI-02: research-only print", 'Table print: research-only'),
        ("if (isResearchOnlyCond(condId)) {            // SCI-02: no \"combination\"", 'Entourage: no combination'),
        ("if(isResearchOnlyCond(id)){st.researchCond=id;go('researchOnly',scResearchOnly);return;}", 'Guided browse → research-only'),
        ("var _ro=currentCondIds().filter(isResearchOnlyCond)[0];          // SCI-02 defence", 'scRec never ranks a cancer topic'),
        ("var _ro=currentCondIds().filter(isResearchOnlyCond)[0];      // SCI-02: research-only print", 'Guided print: research-only'),
        ("express:function(){if(st.intent==='cancer'&&!st.refineKey){go('cancerHelp',scCancerHelp);return;}", 'no cancer skip via express()'),
        ("(st.intent==='cancer'\n      ? '<p class=\"gx-hint\"", 'Cancer Symptom Support shows no Skip button'),
        ("'cond-research','rec-dim'", 'highlight cleared on condition change'),
    ]
    for needle, what in checks:
        if needle not in src:
            fail(name, 'missing: %s' % what)
    fn = js_literal(src, 'function researchOnlyMolIds(', '{')
    if 'molecules[' in fn or '.w' in fn or 'weight' in fn:
        fail(name, 'researchOnlyMolIds() reads scores — the research-only list must be unranked')
    note('research-only cancer: %d malignancy topics unranked at every entry point (Table, print, Entourage, '
         'Guided Match); Cancer Symptom Support has no skip' % len(got))


def main():
    online = '--online' in sys.argv
    src = open(SRC, encoding='utf-8').read()
    print('preflight — index.html (%d KB)%s\n' % (len(src) // 1024,
                                                 '  [--online]' if online else ''))
    data = load_data(src)

    check_scripts_parse(src)
    check_v2fact_keys(src, data)
    check_demo_guided_parity(src, data)
    check_entities(src)
    check_banned_terms(src)
    check_brand_lockups(src)
    check_standing_rules(src)
    check_data_integrity(src, data)
    check_faq_parity(src)
    check_terminology(src)
    check_recommendation_wording(src)
    check_greek_notation(src)
    check_pinch_zoom(src)
    check_unwrapped_counts(src)
    check_pubmed_links(src, data)
    check_pmids(src, online)
    check_ddi_reachable(data)
    check_ddi_referenced_ids(data)
    check_routes(data, src)
    check_route_wording(data)
    check_route_fingerprint(data)
    check_record_schema(data)
    check_hasrisk_invariant(data)
    check_folded_aliases(src, data)
    check_cross_listed(src, data)
    check_legacy_safari(src)
    check_tablet_default_view(src)
    check_legacy_fallback_scoping(src)
    check_gxwrap_flex(src)
    check_demo_ribbon_offset(src)
    check_reset_overlay_display(src)
    check_route_hard_filter(src)
    check_print_reset(src)
    check_session_isolation(src)
    check_trusted_markup(src, data)
    check_reviewed_registry(src, data)
    check_demo_pos_copy(src)
    check_research_association_labels(src, data)
    check_evidence_classification(src, data)
    check_research_only_cancer(src, data)
    check_lead_attribution(src, data)
    check_evidence_mapping(src, data)
    check_primary_designation(src, data)
    check_evidence_presentation(src, data)
    check_build_identifier(src, data)
    check_molecule_profile_terms(src, data)
    check_medication_identity(src, data)
    check_evidence_attribution(data)
    check_backlog()

    for n in NOTES:
        print('  ·', n)
    if FAILURES:
        print('\n%d PROBLEM(S) — do not deploy:\n' % len(FAILURES))
        width = max(len(c) for c, _ in FAILURES)
        for check, msg in FAILURES:
            print('  ✗ [%s] %s' % (check.ljust(width), msg))
        return 1
    print('\nAll preflight checks passed — safe to deploy.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

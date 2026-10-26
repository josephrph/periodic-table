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
    ids = {e.get('id') for e in di}
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
ROUTE_FINGERPRINT = '60137541509fc426'


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

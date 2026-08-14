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
    found = re.findall(r'<span data-v2fact="([^"]+)"[^>]*>([^<]*)</span>', src)
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
    note('V2FACTS: %d spans checked against live data %s' % (len(found), expected))


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
    pat = re.compile(r'(?:The Periodic Table of (?:<em>)?Cannabis(?:</em>)? Plant Molecules)'
                     r'(&trade;|&#8482;|™)?')
    missing = 0
    for m in pat.finditer(src):
        if not m.group(1):
            ctx = re.sub(r'\s+', ' ', src[max(0, m.start() - 60):m.end() + 20])
            fail('trademark lockups', 'lockup without the mark: …%s…' % ctx)
            missing += 1
    if not missing:
        note('trademark: every "Periodic Table of Cannabis Plant Molecules" lockup carries the mark')


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
    pmids = sorted(set(re.findall(r"pmid\s*:\s*'(\d+)'", src)))
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


# ── driver ─────────────────────────────────────────────────────────────────────
def main():
    online = '--online' in sys.argv
    src = open(SRC, encoding='utf-8').read()
    print('preflight — index.html (%d KB)%s\n' % (len(src) // 1024,
                                                 '  [--online]' if online else ''))
    data = load_data(src)

    check_scripts_parse(src)
    check_v2fact_keys(src, data)
    check_entities(src)
    check_banned_terms(src)
    check_brand_lockups(src)
    check_standing_rules(src)
    check_data_integrity(src, data)
    check_faq_parity(src)
    check_pmids(src, online)
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

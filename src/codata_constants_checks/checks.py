#!/usr/bin/env python3
"""
Reproducible checks for the CODATA DRUM Constants review (October 2026).

Each check reproduces one finding from the review, so the result can be
verified independently of the reviewer. The data checks run against a local
clone of https://github.com/codata/drum-constants; the API checks (optional)
run against a running instance of https://github.com/codata/drum-constants-api.

Usage
-----
    pip install .            # from the codata-constants-checks checkout
    git clone https://github.com/codata/drum-constants
    codata-constants-checks --repo drum-constants
    codata-constants-checks --repo drum-constants --api http://127.0.0.1:8000
    codata-constants-checks --repo drum-constants --api <base> --probe-service

(`python -m codata_constants_checks` is equivalent to `codata-constants-checks`.)

--api is the base URL under which the API serves /constants and /sparql.
--probe-service sends one SPARQL query containing SERVICE <http://127.0.0.1:1/>
to the API. Port 1 on the server's own loopback is closed, so no request
reaches any third party; the check only looks at how the server reacts.
Run it only against a server you are entitled to test.

Exit status is 1 if any check FAILs, otherwise 0.

Requires Python >= 3.10 and rdflib >= 7.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, getcontext
from pathlib import Path

import rdflib
from rdflib.namespace import OWL, RDF, SKOS

MODEL = rdflib.Namespace("https://w3id.org/codata/model/")
CONSTANT = rdflib.Namespace("https://w3id.org/codata/constants/")
SCHEMA = rdflib.Namespace("https://schema.org/")

VERSIONS = ["1998", "2002", "2006", "2010", "2014", "2018", "2022"]
LATEST = "2022"

getcontext().prec = 60


# --------------------------------------------------------------------------
# Result bookkeeping
# --------------------------------------------------------------------------

@dataclass
class Result:
    check_id: str
    title: str
    status: str  # PASS | FAIL | WARN | INFO | SKIP
    summary: str
    details: list[str] = field(default_factory=list)


RESULTS: list[Result] = []


def record(check_id, title, status, summary, details=None):
    RESULTS.append(Result(check_id, title, status, summary, details or []))


# --------------------------------------------------------------------------
# Data loading helpers
# --------------------------------------------------------------------------

def load_graph(repo: Path) -> rdflib.Graph:
    ttl = repo / "dist" / "rdf" / "codata_constants.ttl"
    g = rdflib.Graph()
    g.parse(ttl, format="turtle")
    return g


def nist_id_to_constant(g: rdflib.Graph) -> dict[str, rdflib.URIRef]:
    """Map NIST short ids (e.g. 'kjos') to DRUM constant IRIs."""
    out = {}
    for c in g.subjects(RDF.type, MODEL.Constant):
        for pv in g.objects(c, SCHEMA.identifier):
            if (pv, SCHEMA.propertyID, rdflib.Literal("NIST")) in g:
                out[str(g.value(pv, SCHEMA.value))] = c
    return out


def load_nist_json(repo: Path, year: str) -> list[dict]:
    path = repo / "nist" / year / f"allascii_{year}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def value_node(constant: rdflib.URIRef, year: str) -> rdflib.URIRef:
    return rdflib.URIRef(f"{constant}/{year}")


def is_true(g, s, p) -> bool:
    return (s, p, rdflib.Literal(True)) in g


def short(uri) -> str:
    return str(uri).split("/")[-1]


# --------------------------------------------------------------------------
# Data checks
# --------------------------------------------------------------------------

def check_truncation_flags(g, repo, idmap):
    """D1: codata:isTruncated must agree with NIST's '...' marker, per release."""
    details, failures = [], 0
    for year in VERSIONS:
        nist = load_nist_json(repo, year)
        if not nist:
            continue
        missing, spurious, n_nist, n_rdf = [], [], 0, 0
        for e in nist:
            c = idmap.get(e.get("nist_id"))
            if c is None:
                continue
            v = value_node(c, year)
            if (v, RDF.type, MODEL.ConstantValue) not in g:
                continue
            nist_t = bool(e.get("is_truncated"))
            rdf_t = is_true(g, v, MODEL.isTruncated)
            n_nist += nist_t
            n_rdf += rdf_t
            if nist_t and not rdf_t:
                missing.append(short(c))
            if rdf_t and not nist_t:
                spurious.append(short(c))
        line = f"{year}: NIST truncated={n_nist}, RDF isTruncated true={n_rdf}"
        if missing:
            line += f", MISSING flag on {len(missing)} (e.g. {', '.join(missing[:4])})"
        if spurious:
            line += f", SPURIOUS flag on {len(spurious)} (e.g. {', '.join(spurious[:4])})"
        details.append(line)
        failures += len(missing) + len(spurious)
    record("D1", "isTruncated agrees with NIST '...' marker",
           "FAIL" if failures else "PASS",
           f"{failures} disagreements between RDF and NIST source", details)


def check_csv_root_cause(repo):
    """D2: the NIST repackaging CSV and the Sheet snapshots must keep truncation info."""
    details, failures = [], 0
    for year in VERSIONS:
        txt = repo / "nist" / year / f"allascii_{year}.txt"
        csvp = repo / "nist" / year / f"allascii_{year}.csv"
        sheet = repo / "utils" / "csv" / f"v{year}.csv"
        if not txt.exists():
            continue
        n_dots = txt.read_text(encoding="utf-8", errors="replace").count("...")
        csv_info = "absent"
        if csvp.exists():
            with csvp.open(encoding="utf-8") as f:
                rows = list(csv.reader(f))
            header = [h.lower() for h in rows[0]] if rows else []
            has_col = any("truncat" in h for h in header)
            has_dots = any("..." in cell for r in rows[1:] for cell in r)
            csv_info = f"truncation column={has_col}, '...' kept={has_dots}"
            if n_dots and not (has_col or has_dots):
                failures += 1
                csv_info += "  <-- truncation information lost"
        sheet_info = "absent"
        if sheet.exists():
            with sheet.open(encoding="utf-8") as f:
                srows = list(csv.DictReader(f))
            n_true = sum(r.get("is_truncated") == "True" for r in srows)
            sheet_info = f"is_truncated True={n_true}"
            if n_dots and n_true != n_dots:
                failures += 1
                sheet_info += f"  <-- expected {n_dots}"
        details.append(f"{year}: NIST txt '...' markers={n_dots}; "
                       f"nist/{year}/allascii_{year}.csv: {csv_info}; "
                       f"utils/csv/v{year}.csv: {sheet_info}")
    record("D2", "Truncation survives NIST CSV export and Sheet snapshot",
           "FAIL" if failures else "PASS",
           f"{failures} files lose or contradict the NIST truncation marker", details)


def check_exact_relations(g):
    """D3: values flagged isExact must equal the exact relation, unless flagged truncated."""
    def val(cid):
        v = g.value(CONSTANT[f"{cid}/{LATEST}"], MODEL.value)
        return Decimal(str(v)) if v is not None else None

    k, na, e, h = (val(x) for x in ("BoltzmannConstant", "AvogadroConstant",
                                    "ElementaryCharge", "PlanckConstant"))
    if None in (k, na, e, h):
        record("D3", "Exact derived constants", "SKIP", "defining constants not found")
        return
    relations = {
        "MolarGasConstant": ("R = k N_A", k * na),
        "FaradayConstant": ("F = N_A e", na * e),
        "JosephsonConstant": ("K_J = 2e/h", 2 * e / h),
        "VonKlitzingConstant": ("R_K = h/e^2", h / (e * e)),
    }
    details, failures = [], 0
    for cid, (label, exact) in relations.items():
        v = CONSTANT[f"{cid}/{LATEST}"]
        published = val(cid)
        if published is None:
            details.append(f"{cid}: not found")
            continue
        rel = abs(published - exact) / abs(exact)
        exact_flag = is_true(g, v, MODEL.isExact)
        trunc_flag = is_true(g, v, MODEL.isTruncated)
        bad = exact_flag and rel != 0 and not trunc_flag
        failures += bad
        details.append(
            f"{cid} ({label}): published {g.value(v, MODEL.value)}, "
            f"rel. diff to exact = {float(rel):.2e}, isExact={exact_flag}, "
            f"isTruncated={trunc_flag}" + ("  <-- truncated value presented as exact" if bad else ""))
    record("D3", f"{LATEST} exact derived constants are flagged truncated where truncated",
           "FAIL" if failures else "PASS",
           f"{failures} values are not the exact value but carry no truncation flag", details)


def check_deprecation(g, repo):
    """D4: when were the owl:deprecated constants last published?"""
    details, wrong = [], 0
    nist_by_year = {y: {e.get("nist_id") for e in load_nist_json(repo, y)} for y in VERSIONS}
    for c in sorted(g.subjects(OWL.deprecated, rdflib.Literal(True))):
        if (c, RDF.type, MODEL.Constant) not in g:
            continue
        years = sorted(str(g.value(v, MODEL.versionId)) for v in g.objects(c, MODEL.hasValue))
        nist_ids = [str(g.value(pv, SCHEMA.value)) for pv in g.objects(c, SCHEMA.identifier)
                    if (pv, SCHEMA.propertyID, rdflib.Literal("NIST")) in g]
        nist_years = [y for y in VERSIONS if any(i in nist_by_year[y] for i in nist_ids)]
        last = years[-1] if years else "none"
        ok = last == "2018"
        wrong += not ok
        details.append(f"{short(c)}: RDF versions {years[0] if years else '-'}..{last}; "
                       f"NIST source years {nist_years}"
                       + ("" if ok else "  <-- not 'discontinued in the 2022 adjustment'"))
    record("D4", "Deprecated constants were last published in 2018",
           "FAIL" if wrong else "PASS",
           f"{wrong} deprecated constants were dropped before 2022 "
           "(CHANGELOG 0.2.0 says 'discontinued in the 2022 adjustment')", details)


# Case-sensitive symbols occurring in NIST quantity names (same list as
# SYMBOL_TOKENS in the proposed utils/package.py patch).
SYMBOLS = frozenset({
    "u", "s", "m", "g", "K", "J", "T", "W", "Hz", "MHz", "GHz", "Pa", "kPa",
    "eV", "keV", "MeV", "GeV", "fm", "kg", "mol",
    "c", "h", "hc", "bar", "pi", "d", "p", "t", "x", "Z", "HD", "HT", "Cs", "Si",
})


def check_exact_flags(g, repo, idmap):
    """D9: codata:isExact must agree with NIST, per release."""
    details, failures = [], 0
    for year in VERSIONS:
        nist = load_nist_json(repo, year)
        wrong = []
        for e in nist:
            c = idmap.get(e.get("nist_id"))
            if c is None:
                continue
            v = value_node(c, year)
            if (v, RDF.type, MODEL.ConstantValue) not in g:
                continue
            if is_true(g, v, MODEL.isExact) != bool(e.get("is_exact")):
                wrong.append(f"{short(c)} (NIST {bool(e.get('is_exact'))})")
        if wrong:
            failures += len(wrong)
            details.append(f"{year}: {len(wrong)} disagreements, e.g. {', '.join(wrong[:5])}")
        elif nist:
            details.append(f"{year}: agrees")
    record("D9", "isExact agrees with NIST", "FAIL" if failures else "PASS",
           f"{failures} disagreements between RDF and NIST source", details)


def check_label_symbol_case(g):
    """D5: symbols in the NIST name (u, c, hc, g, HD, kPa, ...) must keep their case in prefLabel@en."""
    syms = SYMBOLS
    details = []
    for c in sorted(g.subjects(RDF.type, MODEL.Constant)):
        ref = [str(l) for l in g.objects(c, SKOS.altLabel) if l.language == "en"]
        pref = [str(l) for l in g.objects(c, SKOS.prefLabel) if l.language == "en"]
        if not ref or not pref:
            continue
        ref_toks = re.findall(r"[A-Za-z]+", ref[0])
        pref_toks = re.findall(r"[A-Za-z]+", pref[0])
        pref_lower = {t.lower(): t for t in pref_toks}
        bad = [f"'{t}'->'{pref_lower[t.lower()]}'" for t in ref_toks
               if t in syms and t not in pref_toks and t.lower() in pref_lower]
        if bad:
            details.append(f"{short(c)}: \"{pref[0]}\" (NIST: \"{ref[0]}\"): {', '.join(bad)}")
    record("D5", "Unit/physics symbols keep their case in English prefLabel",
           "FAIL" if details else "PASS",
           f"{len(details)} labels change the case of a symbol", details)


def check_correlations(g, repo):
    """D6: are NIST correlation coefficients exposed?"""
    details, available = [], 0
    for year in VERSIONS:
        p = repo / "nist" / year / f"corrcoef{year}.txt"
        if p.exists():
            n = sum(1 for line in p.open(encoding="utf-8", errors="replace")
                    if re.fullmatch(r"\s*\S+\s+\S+\s+-?\d+\.\d+\s*", line))
            available += 1
            details.append(f"{year}: corrcoef{year}.txt present, {n} coefficient lines")
        else:
            details.append(f"{year}: no corrcoef file in repository")
    n_triples = sum(1 for s, p, o in g if "correl" in str(p).lower()
                    or (p == RDF.type and "correl" in str(o).lower()))
    details.append(f"RDF triples mentioning correlation: {n_triples}")
    record("D6", "Correlation coefficients exposed in RDF",
           "FAIL" if n_triples == 0 else "PASS",
           f"{available} NIST corrcoef files in repo, {n_triples} correlation triples in RDF",
           details)


def check_snapshot_consistency(g, repo):
    """D7: does the git-tracked Sheet snapshot reproduce the published labels?"""
    p = repo / "utils" / "csv" / "Constants.csv"
    if not p.exists():
        record("D7", "Sheet snapshot matches published RDF", "SKIP", "Constants.csv absent")
        return
    with p.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    details = []
    for r in rows:
        if not r.get("id"):
            continue
        en = [str(l) for l in g.objects(CONSTANT[r["id"]], SKOS.prefLabel) if l.language == "en"]
        if en and en[0] != r.get("name"):
            details.append(f"{r['id']}: snapshot \"{r.get('name')}\" vs RDF \"{en[0]}\"")
    record("D7", "Sheet snapshot (utils/csv/Constants.csv) matches published labels",
           "WARN" if details else "PASS",
           f"{len(details)} labels differ, i.e. corrected in code rather than in the source",
           details)


def check_decimal_literals(g):
    """D8: valueDecimal is numerically equal to the authoritative value string."""
    mismatches, longest, n = [], 0, 0
    for v in g.subjects(RDF.type, MODEL.ConstantValue):
        s = g.value(v, MODEL.value)
        d = g.value(v, MODEL.valueDecimal)
        if s is None or d is None:
            continue
        n += 1
        longest = max(longest, len(str(d)))
        try:
            if Decimal(str(s).replace(" ", "")) != Decimal(str(d)):
                mismatches.append(f"{short(v.rsplit('/', 1)[0])}/{short(v)}: {s} vs {d}")
        except Exception as exc:  # malformed literal
            mismatches.append(f"{v}: {exc}")
    record("D8", "valueDecimal equals value numerically",
           "FAIL" if mismatches else "PASS",
           f"{n} values compared, {len(mismatches)} mismatches; longest fixed-point "
           f"xsd:decimal literal = {longest} characters", mismatches[:10])


# --------------------------------------------------------------------------
# API checks
# --------------------------------------------------------------------------

def http_get(url, accept="application/json", timeout=60):
    req = urllib.request.Request(url, headers={"Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def check_api(base, probe_service):
    base = base.rstrip("/")

    # A1 default listing
    status, body = http_get(f"{base}/constants")
    if status != 200:
        record("A1", "GET /constants", "FAIL", f"HTTP {status}", [body[:300]])
        return
    items = json.loads(body)
    versions = {(i.get("value") or {}).get("versionId") for i in items}
    deprecated = [i["id"] for i in items if i.get("isDeprecated")]
    record("A1", "GET /constants returns only current values",
           "PASS" if versions == {LATEST} and not deprecated else "FAIL",
           f"{len(items)} constants, versions {sorted(v for v in versions if v)}, "
           f"{len(deprecated)} deprecated")

    # A2 version parameter validation
    details, bad = [], 0
    for q in ("1999", "abc"):
        s, b = http_get(f"{base}/constants?version={urllib.parse.quote(q)}")
        n = len(json.loads(b)) if s == 200 else None
        bad += s == 200
        details.append(f"?version={q}: HTTP {s}" + (f", {n} items" if n is not None else ""))
    record("A2", "Unknown ?version= is rejected", "FAIL" if bad else "PASS",
           "unknown versions return 200 with an empty list" if bad else "rejected", details)

    # A3 valueDecimal / uncertaintyDecimal lexical forms
    s, b = http_get(f"{base}/constants?include_deprecated=true")
    forms, unequal, examples = {"scientific": 0, "fixed": 0}, [], {}
    for i in json.loads(b) if s == 200 else []:
        v = i.get("value") or {}
        for src, dec in (("value", "valueDecimal"), ("uncertainty", "uncertaintyDecimal")):
            if v.get(dec) is None:
                continue
            form = "scientific" if re.search(r"[eE]", v[dec]) else "fixed"
            forms[form] += 1
            examples.setdefault(form, f"{i['id']}.{dec} = \"{v[dec]}\" (string: \"{v.get(src)}\")")
            if v.get(src) and Decimal(v[src]) != Decimal(v[dec]):
                unequal.append(f"{i['id']}.{dec}")
    mixed = forms["scientific"] and forms["fixed"]
    record("A3", "JSON *Decimal fields use one lexical form",
           "FAIL" if mixed or unequal else "PASS",
           f"scientific={forms['scientific']}, fixed={forms['fixed']}, "
           f"numerically unequal to string={len(unequal)}",
           list(examples.values()) + unequal[:5])

    # A4 truncation visible through the API
    details, bad = [], 0
    for cid in ("JosephsonConstant", "VonKlitzingConstant", "MolarGasConstant", "FaradayConstant"):
        s, b = http_get(f"{base}/constants/{cid}")
        if s != 200:
            details.append(f"{cid}: HTTP {s}")
            continue
        v = json.loads(b).get("value") or {}
        flag = v.get("isTruncated")
        bad += bool(v.get("isExact")) and not flag
        details.append(f"{cid}: value {v.get('value')}, isExact={v.get('isExact')}, isTruncated={flag}")
    record("A4", "Truncated exact values are flagged in API responses",
           "FAIL" if bad else "PASS", f"{bad} truncated values served as plainly exact", details)

    # A5 unit names
    s, b = http_get(f"{base}/constants")
    same = sum(1 for i in json.loads(b) if i.get("unit") and i["unit"].get("name") == i["unit"].get("id"))
    record("A5", "Embedded unit.name is a label, not the id",
           "WARN" if same else "PASS", f"{same} constants embed unit.name == unit.id")

    # A6 SPARQL SERVICE (opt-in)
    if not probe_service:
        record("A6", "SPARQL SERVICE is refused", "SKIP", "run with --probe-service to test")
        return
    q = "SELECT * WHERE { SERVICE <http://127.0.0.1:1/codata-constants-checks-probe> { ?s ?p ?o } } LIMIT 1"
    s, b = http_get(f"{base}/sparql?query={urllib.parse.quote(q)}",
                    accept="application/sparql-results+json")
    refused = s == 400 and "service" in b.lower()
    attempted = s >= 500 or re.search(r"refused|urlopen|URLError|Errno", b, re.I)
    status = "PASS" if refused else ("FAIL" if attempted else "WARN")
    record("A6", "SPARQL SERVICE is refused (outbound requests from the server)", status,
           f"HTTP {s}: " + ("explicitly refused" if refused else
                            "server attempted the outbound request" if attempted else
                            "inconclusive, inspect response"), [b[:300]])


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def print_report():
    width = max(len(r.title) for r in RESULTS)
    for r in RESULTS:
        print(f"[{r.status:4}] {r.check_id:3} {r.title:<{width}}  {r.summary}")
        for d in r.details:
            print(f"              {d}")
    counts = {s: sum(r.status == s for r in RESULTS) for s in ("PASS", "FAIL", "WARN", "INFO", "SKIP")}
    print("\n" + ", ".join(f"{k}={v}" for k, v in counts.items() if v))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, required=True, help="path to a drum-constants clone")
    ap.add_argument("--api", help="base URL of a running drum-constants-api")
    ap.add_argument("--probe-service", action="store_true",
                    help="also test whether /sparql executes SERVICE clauses (see docstring)")
    ap.add_argument("--json", type=Path, help="also write results as JSON to this file")
    args = ap.parse_args(argv)

    RESULTS.clear()
    g = load_graph(args.repo)
    idmap = nist_id_to_constant(g)
    check_truncation_flags(g, args.repo, idmap)
    check_exact_flags(g, args.repo, idmap)
    check_csv_root_cause(args.repo)
    check_exact_relations(g)
    check_deprecation(g, args.repo)
    check_label_symbol_case(g)
    check_correlations(g, args.repo)
    check_snapshot_consistency(g, args.repo)
    check_decimal_literals(g)
    if args.api:
        check_api(args.api, args.probe_service)

    print_report()
    if args.json:
        args.json.write_text(json.dumps([r.__dict__ for r in RESULTS], indent=2), encoding="utf-8")
    return 1 if any(r.status == "FAIL" for r in RESULTS) else 0


if __name__ == "__main__":
    sys.exit(main())

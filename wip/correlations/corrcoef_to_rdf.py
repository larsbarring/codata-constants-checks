#!/usr/bin/env python3
"""
PROTOTYPE: publish NIST correlation coefficients of the CODATA recommended
values as RDF, linked to the existing DRUM codata:ConstantValue resources.

Input   nist/<year>/corrcoef<year>.txt   (NIST, as already stored in this repo)
        dist/rdf/codata_constants.ttl    (to resolve NIST ids to DRUM IRIs)
Output  dist/rdf/codata_correlations_<year>.ttl

The vocabulary below is a proposal for discussion in the DRUM group, not part
of the published ontology (docs/codata.ttl):

    codata:Correlation              a owl:Class
    codata:correlatedValue          ConstantValue, exactly two per Correlation
    codata:correlationCoefficient   xsd:decimal, as printed by NIST
    codata:hasVersion               reused from the existing ontology

    <https://w3id.org/codata/correlations/2018/ElectronMass--ProtonMass>
        a codata:Correlation ;
        codata:hasVersion version:2018 ;
        codata:correlatedValue <.../constants/ElectronMass/2018>,
                               <.../constants/ProtonMass/2018> ;
        codata:correlationCoefficient 0.98058 .

Design notes
- One resource per unordered pair; the diagonal (r = 1) is not emitted.
  The member order in the IRI is alphabetical by DRUM id and carries no meaning.
- Coefficients are kept as the decimal strings NIST prints (5 decimals for
  2018, 3 for 1998), so xsd:decimal is exact here and needs no exponent.
- Zero coefficients are emitted by default: NIST's '0.00000' means
  |r| < 0.000005, which is a statement, not missing data. --omit-zero drops
  them to save space; consumers must then read absence as "r = 0 at the
  printed precision".
- The https://w3id.org/codata/correlations/ namespace would need a w3id
  redirect.

Covariance of two values (SPARQL, using the existing uncertainty fields):

    SELECT ?a ?b ?r ((?ua * ?ub * ?r) AS ?cov) WHERE {
      ?c a codata:Correlation ; codata:correlationCoefficient ?r ;
         codata:correlatedValue ?a, ?b .
      FILTER (STR(?a) < STR(?b))
      ?a codata:uncertaintyFloat ?ua . ?b codata:uncertaintyFloat ?ub .
    }

Validation performed before writing: symmetric duplicates agree, diagonal is
1, |r| <= 1, and (if numpy is installed) the smallest eigenvalue of the
matrix, which should be >= 0 apart from rounding of the printed values.

Exact constants (u = 0) appear in the older files (e.g. c in 1998); their
coefficients are emitted as printed, and any covariance computed from them is 0.

No CODATA 2022 correlation file is in the repository; add it as
nist/2022/corrcoef2022.txt if NIST publishes one.

Usage:
    python utils/corrcoef_to_rdf.py 2018
    python utils/corrcoef_to_rdf.py 1998 2002 2006 2010 2014 2018 --omit-zero
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from rdflib import OWL, RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

ROOT = Path(__file__).resolve().parent.parent
MODEL = Namespace("https://w3id.org/codata/model/")
VERSION = Namespace("https://w3id.org/codata/constants/versions/")
CORRELATION = Namespace("https://w3id.org/codata/correlations/")
SCHEMA = Namespace("https://schema.org/")

NAME_LINE = re.compile(r"\s*(\S+)\s+---\s+(.+?)\s*$")
DATA_LINE = re.compile(r"\s*(\S+)\s+(\S+)\s+(-?\d+\.\d+)\s*$")

logger = logging.getLogger("corrcoef_to_rdf")


def parse_corrcoef(path: Path) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """
    Return ({nist_id: name}, {(id_a, id_b): coefficient_string}) with id_a <= id_b.
    Raises ValueError if the two triangles of the matrix disagree.
    """
    names: dict[str, str] = {}
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if m := NAME_LINE.match(line):
                names[m.group(1)] = m.group(2)
            elif m := DATA_LINE.match(line):
                a, b = sorted((m.group(1), m.group(2)))
                pairs[(a, b)].add(m.group(3))
    conflicts = {p: v for p, v in pairs.items() if len(v) > 1}
    if conflicts:
        raise ValueError(f"{path}: asymmetric coefficients for {len(conflicts)} pairs, e.g. "
                         f"{next(iter(conflicts.items()))}")
    return names, {p: next(iter(v)) for p, v in pairs.items()}


def drum_value_index(g: Graph, year: str) -> dict[str, URIRef]:
    """Map NIST id -> DRUM ConstantValue IRI for the given release."""
    out = {}
    for c in g.subjects(RDF.type, MODEL.Constant):
        for pv in g.objects(c, SCHEMA.identifier):
            if (pv, SCHEMA.propertyID, Literal("NIST")) in g:
                value = URIRef(f"{c}/{year}")
                if (value, RDF.type, MODEL.ConstantValue) in g:
                    out[str(g.value(pv, SCHEMA.value))] = value
    return out


def validate(pairs: dict[tuple[str, str], str], year: str) -> None:
    for (a, b), r in pairs.items():
        x = Decimal(r)
        if a == b and x != 1:
            raise ValueError(f"{year}: diagonal r({a},{a}) = {r}")
        if abs(x) > 1:
            raise ValueError(f"{year}: |r({a},{b})| = {r} > 1")
    try:
        import numpy as np
    except ImportError:
        logger.info("numpy not installed; positive semi-definiteness not checked")
        return
    ids = sorted({i for p in pairs for i in p})
    pos = {i: k for k, i in enumerate(ids)}
    mat = np.eye(len(ids))
    for (a, b), r in pairs.items():
        mat[pos[a], pos[b]] = mat[pos[b], pos[a]] = float(r)
    lam_min = float(np.linalg.eigvalsh(mat).min())
    # The matrix of all recommended values is rank-deficient (they derive from far
    # fewer adjusted constants), so true eigenvalues near 0 can turn negative purely
    # through rounding. Rounding every entry to d decimals is a symmetric
    # perturbation E with |e_ij| <= 0.5e-d, hence ||E||_2 <= n * 0.5e-d (Weyl).
    decimals = max(-Decimal(r).as_tuple().exponent for r in pairs.values())
    bound = len(ids) * 0.5 * 10.0 ** -decimals
    logger.info(f"{year}: {len(ids)}x{len(ids)} matrix, {decimals} decimals, smallest eigenvalue "
                f"{lam_min:.3e} (rounding bound -{bound:.1e})")
    if lam_min < -bound:
        logger.warning(f"{year}: matrix not positive semi-definite beyond rounding of the printed values")


def vocabulary(g: Graph) -> None:
    """Emit the proposed terms so the output file is self-describing."""
    g.add((MODEL.Correlation, RDF.type, OWL.Class))
    g.add((MODEL.Correlation, RDFS.label, Literal("Correlation", lang="en")))
    g.add((MODEL.Correlation, RDFS.comment, Literal(
        "PROPOSED. Correlation coefficient between two recommended values of the same "
        "CODATA adjustment, as published by NIST.", lang="en")))
    g.add((MODEL.correlatedValue, RDF.type, OWL.ObjectProperty))
    g.add((MODEL.correlatedValue, RDFS.domain, MODEL.Correlation))
    g.add((MODEL.correlatedValue, RDFS.range, MODEL.ConstantValue))
    g.add((MODEL.correlatedValue, RDFS.comment, Literal(
        "PROPOSED. One of the two (unordered) values the coefficient relates.", lang="en")))
    g.add((MODEL.correlationCoefficient, RDF.type, OWL.DatatypeProperty))
    g.add((MODEL.correlationCoefficient, RDFS.domain, MODEL.Correlation))
    g.add((MODEL.correlationCoefficient, RDFS.range, XSD.decimal))
    g.add((MODEL.correlationCoefficient, RDFS.comment, Literal(
        "PROPOSED. Correlation coefficient r, in [-1, 1], with the precision printed by NIST.", lang="en")))


def short_id(value: URIRef) -> str:
    return str(value).rstrip("/").split("/")[-2]


def build_graph(year: str, constants: Graph, omit_zero: bool) -> Graph:
    path = ROOT / "nist" / year / f"corrcoef{year}.txt"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found (NIST correlation file for {year})")
    names, pairs = parse_corrcoef(path)
    validate(pairs, year)
    index = drum_value_index(constants, year)

    unmapped = sorted({i for p in pairs for i in p if i not in index})
    if unmapped:
        logger.warning(f"{year}: {len(unmapped)} NIST ids have no DRUM ConstantValue and are skipped: "
                       + ", ".join(f"{i} ({names.get(i, '?')})" for i in unmapped))

    g = Graph()
    for prefix, ns in (("codata", MODEL), ("version", VERSION), ("correlation", CORRELATION),
                       ("constant", Namespace("https://w3id.org/codata/constants/"))):
        g.bind(prefix, ns)
    vocabulary(g)

    emitted = skipped_zero = 0
    for (a, b), r in sorted(pairs.items()):
        if a == b or a not in index or b not in index:
            continue
        if omit_zero and Decimal(r) == 0:
            skipped_zero += 1
            continue
        va, vb = sorted((index[a], index[b]), key=short_id)
        node = CORRELATION[f"{year}/{short_id(va)}--{short_id(vb)}"]
        g.add((node, RDF.type, MODEL.Correlation))
        g.add((node, MODEL.hasVersion, VERSION[year]))
        g.add((node, MODEL.correlatedValue, va))
        g.add((node, MODEL.correlatedValue, vb))
        g.add((node, MODEL.correlationCoefficient, Literal(r, datatype=XSD.decimal)))
        emitted += 1
    logger.info(f"{year}: {emitted} correlations emitted"
                + (f", {skipped_zero} zero coefficients omitted" if omit_zero else ""))
    return g


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish NIST CODATA correlation coefficients as RDF (prototype)")
    ap.add_argument("years", nargs="+", help="adjustment years, e.g. 2018")
    ap.add_argument("--omit-zero", action="store_true", help="do not emit coefficients printed as zero")
    ap.add_argument("-o", "--output-dir", type=Path, default=ROOT / "dist" / "rdf")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    constants = Graph().parse(ROOT / "dist" / "rdf" / "codata_constants.ttl", format="turtle")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for year in args.years:
        g = build_graph(year, constants, args.omit_zero)
        out = args.output_dir / f"codata_correlations_{year}.ttl"
        g.serialize(destination=out, format="turtle")
        logger.info(f"{year}: wrote {out} ({len(g)} triples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

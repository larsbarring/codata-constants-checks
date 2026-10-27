"""
Tests for codata_constants_checks.checks against a small synthetic drum-constants layout,
so they run without a clone of the real repository.

The fixture contains the four 2019-SI defining constants needed for the exact
relations, the molar gas constant R = k N_A (published truncated by NIST), and
one constant whose English label can carry a symbol-case error.
"""

import json
from pathlib import Path

import pytest

from codata_constants_checks import checks

PREFIXES = """\
@prefix codata: <https://w3id.org/codata/model/> .
@prefix constant: <https://w3id.org/codata/constants/> .
@prefix schema: <https://schema.org/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def constant_ttl(cid, nist_id, value, exact, truncated=None, pref=None, alt=None, year="2022"):
    flags = f"    codata:isExact {'true' if exact else 'false'} ;\n"
    if truncated is not None:
        flags += f"    codata:isTruncated {'true' if truncated else 'false'} ;\n"
    labels = ""
    if pref:
        labels += f'    skos:prefLabel "{pref}"@en ;\n'
    if alt:
        labels += f'    skos:altLabel "{alt}"@en ;\n'
    return f"""
constant:{cid} a codata:Constant ;
{labels}    schema:identifier <https://w3id.org/codata/constants/{cid}#NIST> ;
    codata:hasValue <https://w3id.org/codata/constants/{cid}/{year}> .
<https://w3id.org/codata/constants/{cid}#NIST> a schema:PropertyValue ;
    schema:propertyID "NIST" ; schema:value "{nist_id}" .
<https://w3id.org/codata/constants/{cid}/{year}> a codata:ConstantValue ;
{flags}    codata:value "{value}"^^xsd:string ;
    codata:valueDecimal {value_as_decimal(value)} ;
    codata:versionId "{year}" .
"""


def value_as_decimal(s):
    from decimal import Decimal
    return format(Decimal(s), "f")


def make_repo(tmp_path: Path, *, r_truncated=None, r_exact=True,
              label="Rydberg Constant Times c In Hz") -> Path:
    ttl = PREFIXES
    ttl += constant_ttl("BoltzmannConstant", "k", "1.380649e-23", True)
    ttl += constant_ttl("AvogadroConstant", "na", "6.02214076e23", True)
    ttl += constant_ttl("ElementaryCharge", "e", "1.602176634e-19", True)
    ttl += constant_ttl("PlanckConstant", "h", "6.62607015e-34", True)
    ttl += constant_ttl("MolarGasConstant", "r", "8.314462618", r_exact, r_truncated)
    ttl += constant_ttl("RydbergConstantTimesCInHz", "rydchz", "3.2898419602508e15", False,
                        pref=label, alt="Rydberg constant times c in Hz")
    rdf = tmp_path / "dist" / "rdf"
    rdf.mkdir(parents=True)
    (rdf / "codata_constants.ttl").write_text(ttl, encoding="utf-8")

    nist = [
        {"nist_id": "k", "is_exact": True, "is_truncated": False},
        {"nist_id": "na", "is_exact": True, "is_truncated": False},
        {"nist_id": "e", "is_exact": True, "is_truncated": False},
        {"nist_id": "h", "is_exact": True, "is_truncated": False},
        {"nist_id": "r", "is_exact": True, "is_truncated": True},
        {"nist_id": "rydchz", "is_exact": False, "is_truncated": False},
    ]
    nd = tmp_path / "nist" / "2022"
    nd.mkdir(parents=True)
    (nd / "allascii_2022.json").write_text(json.dumps(nist), encoding="utf-8")
    return tmp_path


def run(repo: Path) -> dict[str, checks.Result]:
    checks.main(["--repo", str(repo)])
    return {r.check_id: r for r in checks.RESULTS}


def test_missing_truncation_flag_fails(tmp_path):
    res = run(make_repo(tmp_path, r_truncated=None))
    assert res["D1"].status == "FAIL"
    assert any("MolarGasConstant" in d for d in res["D1"].details)
    assert res["D3"].status == "FAIL"


def test_truncation_flag_present_passes(tmp_path):
    res = run(make_repo(tmp_path, r_truncated=True))
    assert res["D1"].status == "PASS"
    assert res["D3"].status == "PASS"


def test_exact_flag_mismatch_fails(tmp_path):
    res = run(make_repo(tmp_path, r_truncated=True, r_exact=False))
    assert res["D9"].status == "FAIL"
    assert "MolarGasConstant" in res["D9"].details[-1]


@pytest.mark.parametrize("label,status", [
    ("Rydberg Constant Times c In Hz", "PASS"),
    ("Rydberg Constant Times C In Hz", "FAIL"),
])
def test_label_symbol_case(tmp_path, label, status):
    res = run(make_repo(tmp_path, r_truncated=True, label=label))
    assert res["D5"].status == status


def test_decimal_literals_agree(tmp_path):
    res = run(make_repo(tmp_path, r_truncated=True))
    assert res["D8"].status == "PASS"


def test_exit_code(tmp_path):
    assert checks.main(["--repo", str(make_repo(tmp_path / "bad", r_truncated=None))]) == 1

# codata-constants-checks

Reproducible review checks and proposed fixes for the CODATA DRUM Constants
repositories:

- [codata/drum-constants](https://github.com/codata/drum-constants): data, ETL, RDF
- [codata/drum-constants-api](https://github.com/codata/drum-constants-api): FastAPI service

Reviewed versions: drum-constants 0.2.0 (`9f13b1b`) and drum-constants-api 0.4.0
(`00d0892`), both dated 2026-10-20.

## Contents

| Path | What |
|---|---|
| `src/codata_constants_checks/checks.py` | `codata-constants-checks`: one check per review finding, run against a drum-constants clone and optionally a running API |
| `patches/drum-constants/0001-*.patch`, `0002-*.patch` | Fix for the exact/truncated flags and symbol case in labels (0001); symbol case taken from NIST's names and a test of exactness against the SI history (0002) |
| `tests/` | Tests for the checks, using a small synthetic repository |
| `wip/correlations/` | Stage 3 prototype (correlation coefficients as RDF). Not packaged, not finished |

## Install and run

```bash
git clone https://github.com/codata/drum-constants
pip install .                       # in this directory; needs Python >= 3.10, rdflib >= 7
codata-constants-checks --repo drum-constants
codata-constants-checks --repo drum-constants --api http://127.0.0.1:8000           # also check an API
codata-constants-checks --repo drum-constants --api <base> --probe-service --json results.json
```

The exit status is 1 if any check fails. `--api` is the base URL under which
the service answers `/constants` and `/sparql`. To run the API locally:

```bash
git clone https://github.com/codata/drum-constants-api && cd drum-constants-api
pip install fastapi uvicorn gunicorn pydantic pydantic-settings rdflib jinja2
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

`--probe-service` sends one SPARQL query containing
`SERVICE <http://127.0.0.1:1/...>`. Port 1 on the server's own loopback is
closed, so nothing outside the server is contacted. Only use it against a
server you are entitled to test.

## Checks

The Before and After columns are the results for drum-constants `9f13b1b`
without and with the patch (outputs regenerated). The API column is for the
unpatched API 0.4.0 run locally.

| Id | Check | Before | After |
|---|---|---|---|
| D1 | `isTruncated` agrees with NIST's `...` marker | FAIL: 62 values (all 2022) | PASS |
| D9 | `isExact` agrees with NIST | FAIL: 48 values (16 per release, 1998–2006, e.g. c, μ0, ε0) | PASS |
| D2 | NIST CSV export and Sheet snapshot keep the truncation marker | FAIL: repackaged CSVs drop it; `v2022` sheet has 0 of 62 | FAIL: `v2022` sheet still wrong (fix in the Sheet) |
| D3 | 2022 exact derived constants (R, F, K_J, R_K) are flagged truncated where truncated | FAIL: 4 | PASS |
| D4 | Deprecated constants were last published in 2018 | FAIL: 2 last in 2006, 2 last in 2014 | FAIL (modelling/wording issue, not patched) |
| D5 | Symbols keep their case in English `prefLabel`, relative to NIST's quantity names | FAIL: 41 of 357 labels (`In U`, `Times C`, `G Factor`, `In Hd`, `100 KPa`, …) | PASS |
| D6 | Correlation coefficients exposed in RDF | FAIL: 0 triples, 6 NIST files unused | FAIL (stage 3) |
| D7 | Sheet snapshot matches published labels | WARN: 1 | WARN: 41 (labels now corrected in code; fix the Sheet) |
| D8 | `valueDecimal` equals `value` numerically | PASS | PASS |

| Id | API check | API 0.4.0 |
|---|---|---|
| A1 | `/constants` returns only current values | PASS |
| A2 | Unknown `?version=` rejected | FAIL: `1999` and `abc` return 200 with `[]` |
| A3 | `*Decimal` JSON fields use one lexical form | FAIL: 354 scientific, 283 fixed |
| A4 | Truncated exact values flagged | FAIL: follows D1 (data) |
| A5 | `unit.name` is a label | WARN: 261 embed the id |
| A6 | SPARQL `SERVICE` refused | **FAIL: the server makes outbound HTTP requests to caller-supplied URLs** |

A6 is a server-side request forgery issue on a public endpoint. Report it to
the maintainer privately rather than in a public issue.

## Applying the drum-constants patch

The patch changes only the generator code and tests. The checks and the new
tests read the generated files, so nothing changes until those are rebuilt:

```bash
cd drum-constants
git am ../codata-constants-checks/patches/drum-constants/000[12]-*.patch
pip install rdflib openpyxl requests pytest      # generator and test dependencies
python nist/repackage.py                         # NIST CSVs, now with IsExact/IsTruncated
python utils/codata_constants.py --norefresh     # uses the committed workbook, not the live Sheet
python utils/package.py                          # dist/rdf/*
rm -f nist_ids.csv nist_ids.json                 # stray copies repackage.py writes to the current directory
python -m pytest tests                           # 34 tests
codata-constants-checks --repo .
```

Without `--norefresh`, `codata_constants.py` downloads the live Google
Sheet, so the result reflects the Sheet's current state rather than the
committed workbook.

`codata_constants.py` now logs a warning for every worksheet flag it
overrides from the NIST source (110 warnings at present). They list exactly
what to correct in the Google Sheet. Once the Sheet is fixed, the warnings
stop and D2/D7 pass.

Regenerating rewrites `codata_constants.jsonld` in a different triple order
each time, even when the graph is identical. That produces about 140,000
diff lines per regeneration.

## Stages

1. Reproducible checks (this package). **Done.**
2. Fix of flags and labels in drum-constants (patch 0001). **Done.**
3. Correlation coefficients as RDF (`wip/correlations/`). **Deferred.** The
   vocabulary needs agreement in the DRUM group first.
4. drum-constants-api fixes: block SPARQL `SERVICE` (A6), validate `?version`
   (A2), consistent `valueDecimal` (A3). **Next.**

## Publishing this repository

This directory is already a git repository. Create an empty repository on
GitHub (no README or licence), then:

```bash
git remote add origin git@github.com:<user>/codata-constants-checks.git
git push -u origin main
```

Licence: not yet chosen.

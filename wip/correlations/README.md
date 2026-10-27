# Stage 3 (deferred): correlation coefficients as RDF

`corrcoef_to_rdf.py` is meant to live at `utils/corrcoef_to_rdf.py` in a
drum-constants checkout. Its docstring describes the proposed vocabulary
(`codata:Correlation`, `codata:correlatedValue`, `codata:correlationCoefficient`)
and how it is used.

## State at deferral (2026-10-27)

What works:
- Parses `nist/<year>/corrcoef<year>.txt` and checks that both triangles
  agree (they do for all six files), that the diagonal is 1, and that |r| ≤ 1.
- Maps NIST ids to DRUM `ConstantValue` IRIs. 1998–2014 each have about 21
  unmapped ids, mostly the `ev90*` conventional relationships, which have no
  entry in NIST's value tables. These are skipped with a warning. 2018 maps
  completely (262 ids).
- Output for 2018: 34,191 correlations, 171k triples, 13 MB of Turtle.
  `--omit-zero` reduces this to 23,044 correlations, 115k triples, 8.8 MB.

Not yet verified:
- Spot checks of emitted coefficients against the NIST files.
- The covariance SPARQL example in the docstring.
- A run over all six years with the rounding-aware eigenvalue bound. The
  earlier fixed threshold flagged 1998 (min eigenvalue −0.019, 3-decimal
  coefficients). The new bound is n·½·10⁻ᵈ ≈ 0.16 for 1998, so that value is
  within rounding.

Open questions for DRUM:
- The vocabulary and the `https://w3id.org/codata/correlations/` namespace.
- Whether to emit zero coefficients.
- Whether to publish this as separate files or merge it into the main graph
  (it would roughly quadruple the triple count).
- Where to get a CODATA 2022 correlation file; none is in the repository.

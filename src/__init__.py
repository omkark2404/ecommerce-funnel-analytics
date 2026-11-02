"""Empty package marker so `src` is an explicit package.

`src/funnel.py` is imported as `src.funnel` from the repository root
(CI sets PYTHONPATH to the repo root), and as `funnel` from
scripts/validate_results.py, which adds `src/` to sys.path.
"""

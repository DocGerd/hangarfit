"""Solve→tow profiling/benchmark harness (#381).

A committed, repeatable measurement substrate for the hangarfit ``solve`` → tow
pipeline. Not shipped in the wheel (lives outside ``src/``); it is a dev/CI tool
that future perf work measures against, turning anecdotal "601 s → 136 s on my
machine" numbers into regression-guarded ones.

Entry point: ``python -m bench.profile_pipeline`` (see that module's ``--help``).
"""

# Metals recovery branch — 10 Oct 2026

This branch is pinned to the last known-good production commit after a malformed reporting-only edit landed on `main`.

Production trading logic on this branch is the known-good state at commit `993a49c21b5f1452e8498696a9716deb762f1a00` plus this documentation-only commit. No strategy parameters or execution authority are changed here.

Until `main` is cleanly restored, production should follow this recovery branch rather than `main`.

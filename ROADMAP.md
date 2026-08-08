# Roadmap

This roadmap is directional, not a promise of dates. Contributions are welcome through design Issues and pull requests.

## Near term

- package and test a Linux/systemd-compatible watchdog backend;
- formal JSON Schemas for registries, gates, leases, ledgers, jobs, and certificates;
- safer gate states where HOLD is represented without hand-editing runtime JSON;
- automatic manifest generation/check tooling;
- improved coordinator rotation cooldown and fallback repatriation tests;
- portable label merge/validation helper;
- structured migration reports and owner decision views;
- additional adversarial process/cwd/preservation fixtures.

## Medium term

- pluggable process observers for macOS/Linux/Windows;
- repository/provider adapters that remain independent of core safety logic;
- signed completion certificates and evidence bundles;
- schema migration tooling;
- deterministic simulation harness for multi-project contention;
- documented API boundaries for session metadata without live JSONL mutation;
- metrics/export format for dashboards without exposing private runtime content.

## Long term

- cross-machine coordinator ownership with compare-and-swap storage;
- remote worker attestation and artifact provenance;
- policy language for standing authority and irreversible actions;
- formally modeled lifecycle/state-machine validation;
- reference integrations for additional agent runtimes.

## Out of scope

- embedding credentials or customer data;
- autonomous owner decisions;
- bypassing provider/platform safety controls;
- killing arbitrary processes;
- replacing Git, CI, issue trackers, or human review.

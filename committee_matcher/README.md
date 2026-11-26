# Committee Matcher V2 Quick Start

This package is the **api_mapper**. It maps committee jurisdictions to industries through OpenRouter, which charges per request. The **submapper** (`submapper.py`) provides a separate workflow using CLI subscriptions. See section 15 of `organisation/CLI_Bedienungsanleitung.md` for the distinction.

The CLI covers input validation, roster coverage, taxonomy snapshots, provider checks, mapping runs, and manual review in XLSX.

## Validate inputs

```bash
venv/bin/python -m committee_matcher --help
venv/bin/python -m committee_matcher input validate
venv/bin/python -m committee_matcher roster validate
```

Exit code `2` means the command completed with warnings. See the terminal report and `data/committee_matcher_v2/reports/<command>/errors.json` for details.

## Check the provider

```bash
venv/bin/python -m committee_matcher provider check
venv/bin/python -m committee_matcher provider smoke
```

`provider check` checks the configured model's endpoint catalog for structured output support, privacy policy, quantization, price ceiling, and context window. The catalog omits zero data retention (ZDR) status, so this check cannot verify it. If no endpoint qualifies, the command exits with code `4` and lists the available options. You can change the model or set `COMMITTEE_MATCHER_ZDR = False`.

`provider smoke` is the only check that makes a paid request (under USD 0.001). It enforces the output schema and fails if the model advertises structured output support but fails to deliver it.

## Build and approve a taxonomy snapshot

```bash
RUN_ID=run-2026-08-20
venv/bin/python -m committee_matcher snapshot build --run-id "$RUN_ID"
```

The first complete snapshot exits with code `5` (`awaiting_taxonomy_approval`). Review both files before approving it.

```text
data/committee_matcher_v2/runs/<run_id>/taxonomy_snapshot.json
data/committee_matcher_v2/runs/<run_id>/taxonomy_catalog.md
```

```bash
venv/bin/python -m committee_matcher snapshot approve --run-id "$RUN_ID"
```

To resume an interrupted fetch from its checkpoint, run:

```bash
venv/bin/python -m committee_matcher snapshot resume --run-id "$RUN_ID"
```

## Run the mapping

```bash
venv/bin/python -m committee_matcher run plan    --run-id "$RUN_ID" --all-units
venv/bin/python -m committee_matcher run execute --run-id "$RUN_ID"
```

`run plan` estimates the maximum cost in USD without contacting the provider. `run execute` makes paid requests. Choose either `--all-units` or a subset using `--chamber`, `--committee`, and `--unit-id`. The CLI requires a target selection and rejects combining filters with `--all-units`.

If a request has an unresolved outcome, `run reconcile --run-id "$RUN_ID"` looks it up by generation ID.

## Review assignments in Excel

```bash
venv/bin/python -m committee_matcher review export --run-id "$RUN_ID"
venv/bin/python -m committee_matcher review import --run-id "$RUN_ID"
```

Import writes `reviewed_assignments.csv` for `scripts/import_trades.py`. The production mapping master remains separate from this review workflow.

## Production mapping and evaluation

The production mapping of all 225 Units uses CLI subscriptions. See `SUBMAPPER_ANLEITUNG.md` and `organisation/committee_matcher_fullrun_plan.md`.

See `AUFGEGEBENER_PLANTEIL.md` for the decision and alternatives for evaluation (`evaluate run`), holdout qualification (`qualify holdout`), a dedicated `full-run` command family, and reporting (`report`).

## Configuration and references

- Settings with the `COMMITTEE_MATCHER_*` prefix are in `config/settings.py`.
- Section 15 of `organisation/CLI_Bedienungsanleitung.md` documents the full CLI.
- `SUBMAPPER_ANLEITUNG.md` covers the submapper gold set and full run.
- `organisation/committee_matcher_planung_entscheidungen_codex.md` records decisions.
- `organisation/committee_matcher_v2_implementierungsplan.md` records phases and approval gates.

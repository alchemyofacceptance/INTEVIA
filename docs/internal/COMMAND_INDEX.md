# INTEVIA — Command and Orientation Index

**Status:** Internal, static navigation  
**Qualified repository ref:** `1aed0f4da88209d5298e40867b08505661cfd451`  
**Last verified:** 2026-07-31  
**Runtime authority:** None

## Boundary

This index reports repository surfaces. It does not introduce behaviour, routing, parsing, dynamic discovery, telemetry, hidden state, dependency changes, or capability expansion. A listed module may contain an executable entry point; listing it does not execute or authorise it.

## Python module entry points present

| Module | Example invocation | Bounded purpose |
|---|---|---|
| `src.intevia.commands.demo_activity_review` | `python -m src.intevia.commands.demo_activity_review` | Render the bounded contribution-lifecycle demonstration |
| `src.intevia.commands.heartbeat` | `python -m src.intevia.commands.heartbeat` | Render first-breath and governed status text |
| `src.intevia.commands.inspect` | `python -m src.intevia.commands.inspect` | Render a static inspection-surface description |
| `src.intevia.commands.observation` | `python -m src.intevia.commands.observation` | Render an injected or empty observation snapshot |
| `src.intevia.commands.run_observation` | `python -m src.intevia.commands.run_observation` | Render first-breath and governance-status output |
| `src.intevia.commands.status_command` | `python -m src.intevia.commands.status_command` | Render the current static governance-status surface |

`src/intevia/commands/review_snapshot.py` provides formatting functions but has no direct `__main__` entry point at the qualified ref.

## Django management entry point present

| Command | Example invocation | Boundary |
|---|---|---|
| `provision_first_human` | `python manage.py provision_first_human` | Repository command path is present; consult its exact code and required Human authority before use |

Local Django commands require an independently generated `DJANGO_SECRET_KEY` in the process environment. Do not commit that value.

## Documentation-only surfaces

| Surface | Path | Runtime effect |
|---|---|---|
| `status` candidate | [`commands/status.md`](commands/status.md) | None |
| structural map | [`introspection/structural-map.md`](introspection/structural-map.md) | None |
| governance valve | [`instrumentation/governance-valve.md`](instrumentation/governance-valve.md) | None |
| organismal mode | [`primitives/organismal-mode.md`](primitives/organismal-mode.md) | None |
| Sprint 1 candidate command index | [`../evidence/sprints/sprint-1/WORK_BLOCK_9_CANDIDATE_COMMAND_INDEX.md`](../evidence/sprints/sprint-1/WORK_BLOCK_9_CANDIDATE_COMMAND_INDEX.md) | Historical candidate evidence; byte-preserve |

## Interpretation

Documentation remembers surfaces. It does not activate them. Repository presence is not execution, and a command example is not authority to run it.

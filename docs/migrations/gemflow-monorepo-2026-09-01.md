# Gemflow Monorepo Migration — 2026-09-01

## Source

- Repository: `AkkunYo/gemflow`
- Default branch: `main`
- Imported tip: `399d672112c0a6badb5bc48310076b2669f50c44`
- Releases: none
- Tags: none
- Issues: none
- Pull requests: none

The complete Gemflow Git history was imported without squashing under
`services/gemflow`.

## Ownership Mapping

- Gemini gateway and worker code → `services/gemflow`
- Mihomo and provider code → `packages/egress`
- CPA and TokenFlow application code → `apps/tokenflow`

## Compatibility

Runtime files remain flattened under `/app` in both Docker images. Existing
ports, environment variables, mounted data and service commands are unchanged.

## Recovery

The imported Gemflow tip is retained in TokenFlow history and by the annotated
tag `gemflow-archive-2026-09-01`.

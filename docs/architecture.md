# TokenFlow Monorepo Architecture

## Ownership

```text
apps/tokenflow
  ├── depends on services/gemflow
  └── depends on packages/egress

services/gemflow
  └── consumes worker proxy configuration

packages/egress
  └── controls the upstream Mihomo binary and REST API
```

Dependencies must remain one-way:

- `packages/egress` must not import Gemflow or TokenFlow application code.
- `services/gemflow` must not read CPA configuration.
- `apps/tokenflow` owns final process supervision and Docker delivery.

## Runtime Compatibility

The repository layout is modular, but both Docker images copy their required
component files into a flat `/app` runtime directory. Existing imports, config
paths, ports and mounted data directories remain unchanged.

## Published Images

- `registry.cn-hangzhou.aliyuncs.com/zkyml/tokenflow`
- `registry.cn-hangzhou.aliyuncs.com/zkyml/gemflow`

Both images are built from the Monorepo root so shared Egress code has one
canonical source.

## Verification

```bash
bash scripts/test_all.sh
docker build --check .
docker build -t tokenflow:local .
docker build -f services/gemflow/Dockerfile -t gemflow:local .
```

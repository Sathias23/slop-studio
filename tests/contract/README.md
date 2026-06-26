# Cloud API contract snapshot

`openapi-cloud.yaml` is a pinned copy of Comfy Cloud's official OpenAPI spec
([`Comfy-Org/docs`](https://github.com/Comfy-Org/docs/blob/main/openapi-cloud.yaml)),
the source the [Cloud API Reference](https://docs.comfy.org/development/cloud/api-reference)
is generated from.

Two layers use it:

- **`tests/test_cloud_contract.py`** runs `scripts/check_cloud_contract.py`
  against *this pinned copy* — offline, deterministic, part of normal CI. It
  guards that `slop_studio/backends/cloud.py` still matches the last-known-good
  contract.
- **`.github/workflows/cloud-contract.yml`** runs the same checker against the
  **live** spec on a weekly schedule. That's the early-warning: when upstream
  drifts (as `/api/history_v2` → `/api/jobs` already did), the weekly run fails
  before users do.

When the weekly run fails, fix `cloud.py` for the change and refresh this
snapshot:

```sh
curl -sSL https://raw.githubusercontent.com/Comfy-Org/docs/main/openapi-cloud.yaml \
  -o tests/contract/openapi-cloud.yaml
uv run python scripts/check_cloud_contract.py --spec tests/contract/openapi-cloud.yaml
```

# Normalized manifest fixtures

`valid/agent-*.json` are the `manifest` objects of unitysvc-compute-k8s's contract fixtures
(`api/testdata/valid/intent-health.json`, `intent-resources.json`, PR #48): the agent accepts them, so
`normalized_manifest_v1.json` must too. `invalid/` holds manifests the agent's strict parser refuses.
`echo.manifest.json` and `weixin.manifest.json` (with `scratch`) are the golden output of `compile_instance_spec` for the
two example services.

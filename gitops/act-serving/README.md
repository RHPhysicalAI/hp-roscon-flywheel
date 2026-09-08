# act-serving — blue/green serving of the promoted ACT policy (D022, Phase 3 step 5)

Mirrors thor-testing's `vllm-cosmos3` pattern for the in-cluster-GPU target (GB10/GB300):

- `deployment.yaml` (blue) and `deployment-green.yaml` (green) are identical except `color`,
  the modelcar image **digest**, and `MODEL_VERSION`. A modelcar initContainer copies the signed
  ACT checkpoint (`/models/act`) into an emptyDir; the policy container serves it.
- `service.yaml` selects `app: act-policy` + `color: <live>`.
- **A promotion is exactly three edits in ONE commit** (thor-testing 5e3e87a: a two-of-three flip
  left the Service with no endpoints): green digest+`MODEL_VERSION`+`replicas: 1`, blue
  `replicas: 0`, service `color: green`. Only one side is `replicas: 1` at a time.
- `strategy: Recreate`, `revisionHistoryLimit: 0` — one GPU; a RollingUpdate surge pod can never
  schedule.
- Signature enforcement on the node: `policy.json` (`sigstoreSigned` for the registry) **and**
  `registries.d` `use-sigstore-attachments: true` (thor-testing D015 + D018).

**[desktop shim]** There is no in-cluster GPU on the desktop (D013). Both Deployments stay
`replicas: 0` there; the host swap agent (`src/swap-agent/`) reads these same three files, verifies
the promoted image's cosign signature, exports `/models/act` from it, and recreates the host
`act-inference` container with it (Recreate semantics). Deleted by the Fury port.

Bootstrapping: no modelcar exists until the pipeline's first package step runs. Seed blue by
packaging the incumbent (teacher, v1) once; the first promotion PR then moves green to v2.

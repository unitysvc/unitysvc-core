# HTTP Echo

Choose the base or medium resource tier and enroll to request your own disposable
instance. Once the platform starts it, open the generated instance URL with your
enrollment key. The service returns `healthy` on port 5678.

The tier is charged by running time, starting at first readiness. Suspend or
cancel the instance to stop accrual. The rates in this example illustrate the
pricing format; the backend must validate them against its base-cost schedule.

Local files are scratch space and may disappear on restart, replacement,
suspension or cancellation. The application has no persistent volumes and does
not request access to your AI, storage or notification services.

For seller-side verification, run `usvc seller specs validate`, then
`usvc seller specs run-tests hashicorp/echo --category connectivity_test --skip-links`
from the example root. Docker must be available locally. The test pulls the
prepared image, starts the selected tier, probes readiness and removes its test
resources. Local tests do not exercise entry authentication or deployment.

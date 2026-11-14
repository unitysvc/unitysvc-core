# Container service example

This is a complete seller catalog in the existing flat `specs/` layout:

```text
specs/hashicorp/echo/
├── provider.json      # Project behind the software
├── offering.json      # Container channels, technical Document and payout
├── listing.json       # Customer prices, enrollment settings and documents
├── compose.yaml       # Prepared application, uploaded as a Document
├── connectivity.py    # Required connectivity test, uploaded unchanged
└── README.md          # Customer getting-started document
```

`provider.name` is `hashicorp`, `offering.name` is the bare `echo`, and
`listing.name` is `hashicorp/echo`, matching its folder. A seller publishes the
service; the provider credits the project behind the software. `service.json`
is CLI-managed, records the service ID after upload, and is not authored here.

## Extend the existing spec

The offering uses `service_type: container` and two upstream channels, `base`
and `medium`. Each has `access_method: container`, a customer-facing description,
a resource budget, entries and a required `resource:container` / `create` request.
A hosted app may keep another catalog classification, such as `llm`.

The application is an ordinary offering-scoped `DocumentData` record, under the
title `Application`, with the new `category: container_spec`, `mime_type: text`
and `file_path: compose.yaml`. Each channel's `spec` references **that title**.
The existing upload pipeline sends the file bytes through content-addressed
Document storage; no separate app catalog or uploaded per-tier manifest exists.
The source Document belongs on the offering because its contents define what
all listings of that offering execute.

The listing uses existing `list_price: {type: channel, ...}` pricing with hourly
rates for both tiers and the offering's existing revenue-share `payout_price`.
`service_options.enrollment` requires enrollment and caps instances per customer.
Its Documents contain the required `connectivity_test` and a `getting_started`
page. Instance URLs are generated per enrollment and are never authored in
`user_access_interfaces`.

Entries declare `name`, `service`, `port`, `auth` (`enrollment_key` or `none`)
and optional `path` (default `/`). These describe central/edge access policy;
local Docker health checks do not enforce or certify that policy. Entry
hostnames are assigned by central, not the seller.

## Resources belong to the channel

`resources.cpu`, `memory` and `ephemeral_disk` are the total tier budget, using
explicit core/byte quantities. A single service with one replica receives that
budget. Multi-service or replicated applications must declare every service's
**per-replica** allocation within the same channel:

```json
"resources": {
  "cpu": "1", "memory": "1Gi", "ephemeral_disk": "4Gi",
  "services": {
    "web": {"cpu": "250m", "memory": "256Mi", "ephemeral_disk": "1Gi"},
    "worker": {"cpu": "100m", "memory": "128Mi", "ephemeral_disk": "512Mi"}
  }
}
```

The compiler multiplies each allocation by its Compose `deploy.replicas` and
rejects sums above the total budget. There is no automatic division between
services. Allocations are converted internally into equal Kubernetes requests
and limits; `ephemeral_disk` maps to `ephemeral-storage`. The initial seller
format accepts CPU, RAM and scratch disk; GPU/other hardware and persistent
storage are excluded. Placement and base-cost pricing remain backend work.

## Validate and test locally

With the matching core/seller changes installed and Docker available, run from
this directory:

```bash
usvc seller specs validate
usvc seller specs format
usvc seller specs run-tests hashicorp/echo --category connectivity_test --skip-links
```

The local command compiles the selected channel and referenced Compose Document
in memory, then runs the same uploaded connectivity script against every tier.
No `base.manifest.json` or `medium.manifest.json` is required. The script in this
folder is a copy of unitysvc-data's generic `container_connectivity_v1`; it keeps
this example self-contained while the companion package changes are reviewed.
Sellers may instead use the existing preset mechanism in `listing.json`:

```json
"Connectivity test": {"$doc_preset": "container_connectivity_v1"}
```

The test pulls the existing digest-pinned image, starts services and checks
health; it never builds an image. Local execution is optional, while the
connectivity Document remains required. The backend test executor must provide
the same normalized manifest and Docker capability. Backend execution, review
integration, entry authentication, deployment and billing are subsequent work;
this example has not been uploaded or activated.

## Initial Compose subset

Accepted source fields are `services`, prepared digest-pinned `image`, internal
TCP `ports`/`expose`, explicit `environment`, `entrypoint`, `command`,
`deploy.replicas`, and `healthcheck` or native HTTP/TCP/exec `health`.
Compose entrypoint maps to runtime command and Compose command to runtime args.
String forms are split into arguments; shell execution must be explicit.
`healthcheck` CMD/CMD-SHELL and whole-second timings map to the compute health
fields. Missing health falls back to TCP on the first declared port, then running
state when there are no ports. Image-embedded HEALTHCHECK is not used.

Build declarations, volumes, host access, published ports, UDP, dependency
ordering, arbitrary networks and host environment inheritance are rejected.
Platform-injected environment references and secret delivery are not implemented
by this initial compiler; the reference image requires neither. Scratch files
may disappear on restart, replacement, suspension or cancellation. Passing the
local test does not certify Kubernetes/gVisor behavior or disk quotas.

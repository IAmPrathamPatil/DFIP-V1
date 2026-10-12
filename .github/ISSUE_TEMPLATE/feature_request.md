---
name: Feature request
about: Suggest a capability or an improvement
title: ""
labels: enhancement
---

## Problem

<!-- What is hard or impossible today? Describe the situation, not the solution. -->

## Proposal

<!-- What you would like to be possible. -->

## Why this way

<!-- Optional. If you have considered alternatives, say what ruled them out. -->

## Scope and constraints

DFIP is a working production system. These usually matter for a proposal here:

- **Single API process.** In-process thread pools, process-local rate limiting and
  in-memory mode assume one instance. Horizontal scaling needs those externalized.
- **No durable queue.** Background depth is bounded by process memory.
- **Tenant isolation** is enforced by application authorization first, with
  PostgreSQL RLS as defense in depth. A change that weakens either needs careful
  review.
- **Excel refresh is fragile.** A Power Query refresh rebuilds the shared pivot
  cache and drops calculated fields.
- **No down migrations.** Schema changes are additive.

## Alternatives considered

<!-- Optional. -->

## Additional context

<!-- Links, references, or examples. Please do not attach client data. -->
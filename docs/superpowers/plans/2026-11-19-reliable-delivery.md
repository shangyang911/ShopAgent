# ShopAgent Reliable Delivery Implementation Plan

> **For agentic workers:** Use systematic debugging, TDD and bounded independent agent tasks, then verify the integrated release.

**Goal:** Resolve the accepted audit gaps and connect Gitee Go to a tested, reversible release process.

**Architecture:** Preserve existing production storage and HTTPS. Use commit-addressed local images, isolated MySQL for CI, pre-release backup and application-only rollback.

**Tech stack:** FastAPI, Vue, Docker Compose, Python 3.10 host utilities, systemd, Gitee Go.

**Spec:** ../specs/2026-11-19-reliable-delivery-design.md

## Global constraints

- No external logistics credentials exist: keep services explicitly disabled.
- Local backups only. No outbound fault notifications or new paid cloud services.
- Never delete production volumes or restore a backup over the running business database during tests.
- Preserve existing uncommitted production fixes in the release baseline.

## Review focus

- A failed build or test must never change running containers.
- A failed smoke check must revert image tag, release pointer and environment consistently.
- Temporary MySQL and restored databases must be isolated and cleaned on errors.
- Archive traversal, corrupt manifests and credentials must not escape backup boundaries.
- Older queued pipeline jobs must not overwrite a newer master deployment.

## Tasks

- [ ] Fix bounded delivery queries; prove timeout and pagination regressions, preserve single-route behavior.
- [ ] Add backup.py create/verify/status and daily systemd timer; test corruption and isolated recovery.
- [ ] Add /api/ready, smoke.py and local monitor timer; test database failure and stale backup.
- [ ] Add pinned Docker builds, release.py locking/CI/rollback, and .workflow Gitee Go definition; test failure paths before live rollout.
- [ ] Record current validated source, install host tools and run real backup/recovery, CI and public smoke checks.
- [ ] Register Gitee Go host group and execute triggered pipeline; verify rollback safely, then update operator instructions and completion evidence.

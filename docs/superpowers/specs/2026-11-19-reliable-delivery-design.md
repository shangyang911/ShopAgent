# ShopAgent reliable delivery

Implements the audit report accepted for execution on 2026-11-19. User clarified: use Gitee Go; keep backups locally under `/opt/shopagent/backups/`; do not configure fault notifications; no Kuaidi100 account exists, so leave real tracking/label services disabled.

Keep the existing ECS, Nginx HTTPS endpoint, MySQL volume and uploads. Fix the legacy delivery list with bounded SQL pagination and one request-wide geocoding deadline. Add database readiness, scheduled local backup with verified isolated recovery, and local health state without sending notifications.

Gitee Go triggers on master pushes. A dedicated host task supplies the immutable GITEE_COMMIT to a server-side release tool. The tool fetches that exact trusted commit, serializes releases, builds SHA-tagged images, runs frontend checks and backend tests against disposable MySQL, backs up production, then updates only api/web. Failed post-deployment checks revert both image selection and the current release pointer; database data is never automatically rolled back. Preserve previous images and release metadata.

Approaches: Gitee Go orchestration on the existing ECS avoids adding a registry or another paid service and keeps credentials on the server. Cloud-built images plus a registry can separate build resources later. A second application instance could reduce rollout interruption but does not remove the single ECS failure boundary and is outside this requested setup.

Acceptance: regression test for timeout; public readiness and business reads; one scheduled backup and isolated restore check; a known-failure deployment gate proving rollback; a real Gitee Go triggered run with version provenance. Do not claim Gitee Go completion until the platform run exists. User must log into the platform to register its host group/credentials.

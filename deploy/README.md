# ShopAgent production operations

Public URL: https://123.57.2.244/ (voice: `/agent`). Production secrets are separate from Git, under `/opt/shopagent/secrets`; uploaded files and the existing MySQL volume are preserved across releases.

## CI/CD

GitHub Actions deploys pushes to master and supports manual workflow dispatch on master. Production deployments are serialized. The workflow uses the SSH_PRIVATE_KEY repository secret with the restricted shopagent-deploy account and pins the ECS address and SSH host key in `.github/workflows/main.yml`. Its root-owned release entry accepts a full commit SHA, and the server fetches GitHub using a repository-only read key.

The root-owned release tool fetches that exact master revision with a repository-only read key, serializes executions, builds commit-tagged images, runs frontend tests/architecture/type checks/build and backend tests with a disposable MySQL. It does not mount production secrets or volumes into CI test containers. Existing Python dependencies are locked from the validated Linux runtime; Docker base images are pinned by digest.

Before deployment it creates a local backup and fetches master again, rejecting a superseded build. Compose then updates only api/web and runs verified HTTPS readiness, authentication, assets and unauthenticated WSS probes. Failure restores the previous image selection, current symlink and application containers; it does not restore the business database. Additive database compatibility is required for rollback. One ECS still permits brief application update interruption.

`/opt/shopagent/state/last-deployment.json` records deployed/superseded/rolled_back/rollback_failed; build logs include the commit SHA. Docker images and release source are retained for review and rollback. Current tooling installed in `/opt/shopagent/ops` is root-owned; changes to that installed controller require explicit operator installation rather than automatic root bootstrap from a new commit.

## Backup and recovery checks

Daily 02:15 Asia/Shanghai: `shopagent-backup.timer`. Archives under `/opt/shopagent/backups/automatic/` contain a transactional SQL dump, uploads, runtime configuration and hash manifest. Keep the latest 14 automatic archives; unrelated/manual backups are not pruned. This is local backup only; host/disk loss also loses these copies.

```sh
python3 /opt/shopagent/ops/backup.py create
python3 /opt/shopagent/ops/backup.py status
python3 /opt/shopagent/ops/backup.py verify --archive /opt/shopagent/backups/automatic/ARCHIVE.tar.gz
systemctl status shopagent-backup.timer
```

Create and verify restore the dump into a random temporary database through a scoped temporary account, compare schema/row counts and remove the temporary database/account. Verify never restores over the running business database. Database transactions and copied upload files are not one cross-system atomic snapshot; schedule snapshots away from bulk image changes. Full disaster recovery is an operator procedure using a selected archive, new database and restored uploads/config, followed by application smoke checks.

## Local health checks

Every five minutes: `shopagent-monitor.timer`. It checks HTTPS/readiness, Docker health, free disk, certificate expiry/renewal and backup age/hash. Results are stored in `/opt/shopagent/state/health.json` and the system journal. No fault messages, emails or webhooks are sent, as requested.

```sh
python3 /opt/shopagent/ops/monitor.py
python3 /opt/shopagent/ops/smoke.py --base-url https://123.57.2.244
journalctl -u shopagent-monitor.service -n 20
```

## External logistics

Kuaidi100 tracking and electronic labels remain disabled until an account and the corresponding API permissions exist. This setup does not generate real labels or incur logistics provider charges.

## Local development verification

```sh
python -m unittest discover -s deploy/tests -v
cd frontend
npm test
npm run check:architecture
npm run build
```

Backend defaults to isolated offline tests; the release controller sets `RUN_MYSQL_TESTS=1` against a separate disposable MySQL container for full concurrency tests. Do not point CI at the business database.

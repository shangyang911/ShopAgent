#!/usr/bin/env python3
"""Commit-addressed builds, isolated CI, preflight backups and application rollback."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import signal
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid


BASE_URL = 'https://123.57.2.244'
REPOSITORY = 'git@gitee.com:up-above/shop-agent.git'
SOURCE_MARKER = '.shopagent-source-revision'


def validate_revision(value):
    if not re.fullmatch(r'[a-f0-9]{40}', value):
        raise ValueError('A full lowercase Git commit SHA is required')
    return value


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        temporary.write_bytes(data if isinstance(data, bytes) else data.encode())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_command(command, *, env=None, timeout=1800, cwd=None):
    # Commands contain no secret values; captured failure output may contain test
    # data so it belongs in private server-side logs, never a public API response.
    result = subprocess.run(command, env=env, cwd=cwd, text=True, encoding='utf-8',
                            errors='replace', stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        print(result.stdout[-12000:], file=sys.stderr)
        raise RuntimeError('Command failed: ' + ' '.join(map(str, command[:3])))
    return result.stdout


@contextmanager
def release_lock(root):
    import fcntl
    (root / 'state').mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'state' / 'release.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another ShopAgent release is already running') from None
        yield


class ReleaseManager:
    def __init__(self, root, *, run=run_command, base_url=BASE_URL):
        self.root = Path(root).resolve()
        self.run = run
        self.base_url = base_url

    def state(self, **values):
        values['checked_at'] = datetime.now(timezone.utc).isoformat()
        atomic_write(self.root / 'state' / 'last-deployment.json', json.dumps(values, indent=2))

    def compose(self, release):
        return ['docker', 'compose', '-p', 'shopagent', '--env-file',
                str(self.root / 'secrets' / 'compose.env'), '-f', str(release / 'compose.yaml'),
                'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'api', 'web']

    def set_current(self, release):
        temporary = self.root / ('current-next-' + uuid.uuid4().hex)
        try:
            temporary.symlink_to(release, target_is_directory=True)
            os.replace(temporary, self.root / 'current')
        finally:
            temporary.unlink(missing_ok=True)

    def check_previous(self):
        # The first rollback target predates /api/ready, so only require the
        # existing health and DB-backed auth status contracts here.
        for endpoint in ('/api/health', '/api/auth/status'):
            with urllib.request.urlopen(self.base_url + endpoint, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError('Previous release failed its recovery probe')

    def deploy(self, release):
        release = Path(release).resolve()
        if release.parent != (self.root / 'releases').resolve():
            raise ValueError('Release must be directly within the owned releases directory')
        manifest = json.loads((release / 'release.json').read_text())
        revision = validate_revision(manifest['revision'])
        previous = (self.root / 'current').resolve(strict=True)
        environment_file = self.root / 'secrets' / 'compose.env'
        previous_environment = environment_file.read_bytes()
        # Backup/verification failure happens before touching environment or containers.
        backup = json.loads(self.run(['python3', str(self.root / 'ops' / 'backup.py'), 'create',
                                      '--root', str(self.root)]))
        # Builds and backups may take minutes. Check the remote again at the
        # last safe boundary, including explicit --source/manual deploys.
        head = self.fetch_master()
        if head != revision:
            self.state(status='superseded', revision=revision, current_master=head,
                       previous=str(previous), backup=backup['path'])
            return False
        new_environment = re.sub(rb'(?m)^SHOPAGENT_RELEASE=.*$',
                                 b'SHOPAGENT_RELEASE=' + revision.encode(), previous_environment)
        if new_environment == previous_environment and b'SHOPAGENT_RELEASE=' not in previous_environment:
            new_environment += b'\nSHOPAGENT_RELEASE=' + revision.encode() + b'\n'
        self.state(status='deploying', revision=revision, previous=str(previous), backup=backup['path'])
        try:
            atomic_write(environment_file, new_environment)
            self.run(self.compose(release), timeout=300)
            self.run(['python3', str(release / 'deploy' / 'ops' / 'smoke.py'),
                      '--base-url', self.base_url], timeout=120)
            self.set_current(release)
        except BaseException:
            try:
                atomic_write(environment_file, previous_environment)
                self.set_current(previous)
                self.run(self.compose(previous), timeout=300)
                self.check_previous()
            except BaseException:
                self.state(status='rollback_failed', revision=revision, previous=str(previous), backup=backup['path'])
                raise RuntimeError('Deployment and rollback failed; inspect private server logs') from None
            self.state(status='rolled_back', revision=revision, previous=str(previous), backup=backup['path'])
            raise RuntimeError('Deployment failed; previous application version recovered') from None
        self.state(status='deployed', revision=revision, previous=str(previous), backup=backup['path'])
        return True

    def git_environment(self):
        environment = os.environ.copy()
        environment['GIT_SSH_COMMAND'] = shlex.join([
            'ssh', '-i', str(self.root / 'secrets' / 'gitee-readonly'),
            '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes'])
        return environment

    def fetch_master(self):
        repository = self.root / 'repository.git'
        if not repository.exists():
            self.run(['git', 'init', '--bare', str(repository)])
            self.run(['git', '-C', str(repository), 'remote', 'add', 'origin', REPOSITORY])
        self.run(['git', '-C', str(repository), 'fetch', '--prune', 'origin',
                  '+refs/heads/master:refs/remotes/origin/master'], env=self.git_environment())
        return validate_revision(self.run(['git', '-C', str(repository), 'rev-parse',
                                          'refs/remotes/origin/master']).strip())

    def checkout(self, revision):
        repository = self.root / 'repository.git'
        head = self.fetch_master()
        if head != revision:
            self.state(status='superseded', revision=revision, current_master=head)
            return None
        destination = self.root / 'releases' / revision
        marker = destination / SOURCE_MARKER
        if destination.exists():
            if destination == (self.root / 'current').resolve():
                self.state(status='already_deployed', revision=revision)
                return None
            if (destination.is_dir() and not destination.is_symlink()
                    and marker.is_file() and not marker.is_symlink()
                    and marker.read_bytes() == (revision + '\n').encode('ascii')):
                return destination
            raise RuntimeError('Release directory already exists; keep failed build evidence and use a new commit')
        destination.mkdir(parents=True, mode=0o700)
        archive = self.root / 'state' / ('source-' + revision + '.tar')
        self.run(['git', '-C', str(repository), 'archive', '--format=tar', '-o', str(archive), revision])
        try:
            with tarfile.open(archive) as package:
                # Trusted repository still may not place links/devices or traverse paths.
                for member in package.getmembers():
                    target = destination / member.name
                    if (not target.resolve().is_relative_to(destination) or member.issym()
                            or member.islnk() or member.isdev()
                            or target == marker or marker in target.parents):
                        raise ValueError('Unsafe source archive member')
                package.extractall(destination)
            # An interrupted extraction can never qualify for source reuse.
            atomic_write(marker, revision + '\n')
        finally:
            archive.unlink(missing_ok=True)
        return destination

    def build(self, source, revision):
        revision = validate_revision(revision)
        source = Path(source).resolve()
        environment = os.environ.copy()
        for line in (self.root / 'secrets' / 'frontend.env').read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() == 'VITE_AMAP_KEY':
                environment['VITE_AMAP_KEY'] = value.strip().strip('\"\'')
        if not environment.get('VITE_AMAP_KEY'):
            raise ValueError('Public Amap JS key missing from secrets/frontend.env')
        # Frontend Docker build runs npm ci/tests/architecture/type checks/build.
        print('Building and testing frontend for ' + revision, flush=True)
        frontend_log = self.run(['docker', 'build', '--build-arg', 'VITE_AMAP_KEY', '--build-arg', 'SOURCE_REVISION=' + revision,
                  '-f', str(source / 'deploy' / 'Dockerfile.web'), '-t', 'shopagent-web:' + revision,
                  str(source)], env=environment, timeout=1800)
        atomic_write(self.root / 'state' / ('frontend-build-' + revision + '.log'), frontend_log)
        for target, image in (('test', 'shopagent-test:'), ('runtime', 'shopagent-api:')):
            print('Building API target ' + target, flush=True)
            self.run(['docker', 'build', '--target', target, '--build-arg', 'SOURCE_REVISION=' + revision,
                      '-f', str(source / 'deploy' / 'Dockerfile.api'), '-t', image + revision,
                      str(source)], timeout=1800)
        print('Running backend tests against isolated MySQL', flush=True)
        self.test_backend(revision)
        self.run(['python3', '-m', 'unittest', 'discover', '-s', str(source / 'deploy' / 'tests'), '-v'],
                  timeout=180, cwd=str(source))
        shutil.copyfile(source / 'deploy' / 'compose.yaml', source / 'compose.yaml')
        images = {name: self.run(['docker', 'image', 'inspect', 'shopagent-' + name + ':' + revision,
                                  '--format', '{{.Id}}']).strip() for name in ('api', 'web')}
        atomic_write(source / 'release.json', json.dumps({'revision': revision, 'images': images,
                     'built_at': datetime.now(timezone.utc).isoformat()}, indent=2))

    def test_backend(self, revision):
        suffix = uuid.uuid4().hex[:12]
        network = 'shopagent-ci-' + suffix
        database = 'shopagent-ci-db-' + suffix
        test_container = 'shopagent-ci-test-' + suffix
        password = secrets.token_hex(24)
        environment = os.environ.copy()
        environment.update(MYSQL_ROOT_PASSWORD=password, MYSQL_PWD=password,
            DATABASE_URL='mysql+pymysql://root:' + password + '@mysql:3306/shopagent_ci?charset=utf8mb4')
        try:
            # A timeout can occur after Docker creates a resource, so include
            # the first allocation in the cleanup scope as well.
            self.run(['docker', 'network', 'create', network])
            self.run(['docker', 'run', '-d', '--name', database, '--network', network,
                      '--network-alias', 'mysql', '--cpus', '1', '--memory', '768m',
                      '--tmpfs', '/var/lib/mysql:rw,nosuid,size=1073741824',
                      '-e', 'MYSQL_ROOT_PASSWORD', '-e', 'MYSQL_DATABASE=shopagent_ci',
                      'mysql:8.4.13', '--innodb-buffer-pool-size=128M'], env=environment)
            for attempt in range(90):
                try:
                    ready = self.run(['docker', 'exec', '-e', 'MYSQL_PWD', database, 'mysql',
                                      '--protocol=TCP', '--host=127.0.0.1', '--user=root',
                                      '--database=shopagent_ci', '--batch', '--skip-column-names',
                                      '--execute', 'SELECT 1'], env=environment, timeout=5)
                    if ready.strip() != '1':
                        raise RuntimeError('Isolated MySQL query did not return its readiness contract')
                    break
                except (RuntimeError, subprocess.SubprocessError):
                    time.sleep(2)
            else:
                raise RuntimeError('Isolated MySQL did not become ready')
            result = self.run(['docker', 'run', '--rm', '--name', test_container, '--network', network,
                              '--cpus', '1', '--memory', '1g', '-e', 'DATABASE_URL',
                              '-e', 'RUN_MYSQL_TESTS=1', 'shopagent-test:' + revision],
                              env=environment, timeout=600)
            atomic_write(self.root / 'state' / ('backend-tests-' + revision + '.log'), result)
        finally:
            # Only exact random CI resources, never the production network or volume.
            for command in (['docker', 'rm', '-f', test_container],
                            ['docker', 'rm', '-f', database], ['docker', 'network', 'rm', network]):
                try:
                    self.run(command, timeout=60)
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'build', 'deploy'])
    parser.add_argument('--root', type=Path, default=Path('/opt/shopagent'))
    parser.add_argument('--revision', required=True, type=validate_revision)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    root = args.root.resolve()
    with release_lock(root):
        manager = ReleaseManager(root)
        if args.action == 'deploy':
            outcome = 'deployed' if manager.deploy(root / 'releases' / args.revision) else 'superseded'
        else:
            source = args.source or manager.checkout(args.revision)
            if source is None:
                outcome = json.loads((root / 'state' / 'last-deployment.json').read_text())['status']
            else:
                manager.build(source, args.revision)
                outcome = 'built'
                if args.action == 'run':
                    outcome = 'deployed' if manager.deploy(source) else 'superseded'
    print(json.dumps({'ok': True, 'revision': args.revision, 'action': args.action, 'status': outcome}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)

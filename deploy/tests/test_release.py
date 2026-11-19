import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


def load_release():
    spec = importlib.util.spec_from_file_location('shopagent_release', Path(__file__).parents[1] / 'ops' / 'release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_release()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'secrets').mkdir()
        (self.root / 'state').mkdir()
        (self.root / 'current').mkdir()
        self.old_env = b'MYSQL_PASSWORD=do-not-print\nSHOPAGENT_RELEASE=20261118\n'
        (self.root / 'secrets' / 'compose.env').write_bytes(self.old_env)
        self.revision = 'a' * 40
        self.stage = self.root / 'releases' / self.revision
        self.stage.mkdir(parents=True)
        (self.stage / 'compose.yaml').write_text('services: {}')
        (self.stage / 'release.json').write_text(json.dumps({'revision': self.revision}))

    def manager(self, fail_new=False, fail_backup=False):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            if 'backup.py' in str(command) and fail_backup:
                raise RuntimeError('backup failed')
            if 'backup.py' in str(command):
                return '{"path":"safe-backup"}'
            if 'rev-parse' in command:
                return self.revision + '\n'
            if str(self.stage / 'deploy' / 'ops' / 'smoke.py') in command and fail_new:
                raise RuntimeError('new release smoke failed')
            return ''
        manager = self.module.ReleaseManager(self.root, run=run)
        manager.set_current = Mock()
        manager.check_previous = Mock()
        return manager, commands

    def test_failure_restores_exact_environment_and_previous_containers(self):
        manager, commands = self.manager(fail_new=True)
        with self.assertRaises(RuntimeError):
            manager.deploy(self.stage)
        self.assertEqual((self.root / 'secrets' / 'compose.env').read_bytes(), self.old_env)
        compose_calls = [c for c in commands if c[:2] == ['docker', 'compose']]
        self.assertEqual(len(compose_calls), 2)
        self.assertIn(str(self.root / 'current' / 'compose.yaml'), compose_calls[-1])
        manager.check_previous.assert_called_once()
        state = json.loads((self.root / 'state' / 'last-deployment.json').read_text())
        self.assertEqual(state['status'], 'rolled_back')

    def test_backup_failure_never_changes_containers_or_environment(self):
        manager, commands = self.manager(fail_backup=True)
        with self.assertRaises(RuntimeError):
            manager.deploy(self.stage)
        self.assertFalse(any(c[:2] == ['docker', 'compose'] for c in commands))
        self.assertEqual((self.root / 'secrets' / 'compose.env').read_bytes(), self.old_env)
        manager.set_current.assert_not_called()

    def test_success_switches_pointer_only_after_smoke_and_records_revision(self):
        manager, commands = self.manager()
        manager.deploy(self.stage)
        manager.set_current.assert_called_once_with(self.stage)
        state = json.loads((self.root / 'state' / 'last-deployment.json').read_text())
        self.assertEqual(state['status'], 'deployed')
        self.assertEqual(state['revision'], self.revision)
        content = (self.root / 'secrets' / 'compose.env').read_text()
        self.assertIn('MYSQL_PASSWORD=do-not-print', content)
        self.assertIn('SHOPAGENT_RELEASE=' + self.revision, content)
        self.assertNotIn('do-not-print', json.dumps(state))

    def test_rejects_release_outside_owned_directory(self):
        manager, commands = self.manager()
        with self.assertRaises(ValueError):
            manager.deploy(self.root / 'current')
        self.assertFalse(commands)

    def test_revision_cannot_be_shell_or_path_expression(self):
        for revision in ('../current', 'master', 'a'*40+';id', 'a'*39):
            with self.assertRaises(ValueError):
                self.module.validate_revision(revision)

    def test_master_advancing_during_backup_skips_all_production_changes(self):
        manager, commands = self.manager()
        original_run = manager.run
        advanced = False
        def run(command, **kwargs):
            nonlocal advanced
            result = original_run(command, **kwargs)
            if 'backup.py' in str(command):
                advanced = True
            if 'rev-parse' in command:
                self.assertTrue(advanced, 'Final master check must happen after backup')
                return 'b' * 40 + '\n'
            return result
        manager.run = run
        self.assertFalse(manager.deploy(self.stage))
        self.assertEqual((self.root / 'secrets' / 'compose.env').read_bytes(), self.old_env)
        self.assertFalse(any(command[:2] == ['docker', 'compose'] for command in commands))
        manager.set_current.assert_not_called()
        state = json.loads((self.root / 'state' / 'last-deployment.json').read_text())
        self.assertEqual(state['status'], 'superseded')
        self.assertEqual(state['current_master'], 'b' * 40)

    def test_final_fetch_failure_never_changes_production(self):
        manager, commands = self.manager()
        original_run = manager.run
        def run(command, **kwargs):
            result = original_run(command, **kwargs)
            if 'fetch' in command:
                self.assertIn('gitee-readonly', kwargs['env']['GIT_SSH_COMMAND'])
                self.assertIn('StrictHostKeyChecking=yes', kwargs['env']['GIT_SSH_COMMAND'])
                raise OSError('fetch failed')
            return result
        manager.run = run
        with self.assertRaises(OSError):
            manager.deploy(self.stage)
        self.assertEqual((self.root / 'secrets' / 'compose.env').read_bytes(), self.old_env)
        self.assertFalse(any(command[:2] == ['docker', 'compose'] for command in commands))
        manager.set_current.assert_not_called()

    def test_checkout_reuses_owned_source_with_matching_completed_marker(self):
        manager, commands = self.manager()
        (self.stage / '.shopagent-source-revision').write_bytes((self.revision + '\n').encode('ascii'))
        artifact = self.stage / 'failed-build.log'
        artifact.write_text('preserve build evidence')
        self.assertEqual(manager.checkout(self.revision), self.stage)
        self.assertEqual(artifact.read_text(), 'preserve build evidence')
        self.assertFalse(any('archive' in command for command in commands))

    def test_checkout_refuses_missing_or_mismatching_source_marker(self):
        for marker in (None, 'b' * 40 + '\n', self.revision):
            with self.subTest(marker=marker):
                path = self.stage / '.shopagent-source-revision'
                if marker is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(marker, encoding='ascii')
                manager, commands = self.manager()
                with self.assertRaisesRegex(RuntimeError, 'Release directory already exists'):
                    manager.checkout(self.revision)
                self.assertTrue((self.stage / 'compose.yaml').exists())
                self.assertFalse(any('archive' in command for command in commands))

    def test_checkout_skips_current_release_even_without_source_marker(self):
        manager, commands = self.manager()
        # Model the current symlink target without requiring Windows symlink privileges.
        with patch.object(self.module.Path, 'resolve', return_value=self.stage):
            self.assertIsNone(manager.checkout(self.revision))
        state = json.loads((self.root / 'state' / 'last-deployment.json').read_text())
        self.assertEqual(state['status'], 'already_deployed')
        self.assertFalse(any('archive' in command for command in commands))

    def test_checkout_marks_source_only_after_complete_extraction(self):
        import io
        import tarfile
        self.stage.joinpath('release.json').unlink()
        self.stage.joinpath('compose.yaml').unlink()
        self.stage.rmdir()
        manager, commands = self.manager()
        original_run = manager.run
        def run(command, **kwargs):
            result = original_run(command, **kwargs)
            if 'archive' in command:
                archive = Path(command[command.index('-o') + 1])
                with tarfile.open(archive, 'w') as package:
                    info = tarfile.TarInfo('source.txt')
                    info.size = 7
                    package.addfile(info, io.BytesIO(b'trusted'))
            return result
        manager.run = run
        manager.checkout(self.revision)
        self.assertEqual((self.stage / 'source.txt').read_bytes(), b'trusted')
        self.assertEqual((self.stage / '.shopagent-source-revision').read_bytes(), (self.revision + '\n').encode())

    def test_extraction_failure_never_publishes_source_marker(self):
        import io
        import tarfile
        self.stage.joinpath('release.json').unlink()
        self.stage.joinpath('compose.yaml').unlink()
        self.stage.rmdir()
        manager, commands = self.manager()
        original_run = manager.run
        def run(command, **kwargs):
            result = original_run(command, **kwargs)
            if 'archive' in command:
                archive = Path(command[command.index('-o') + 1])
                with tarfile.open(archive, 'w') as package:
                    info = tarfile.TarInfo('.shopagent-source-revision')
                    data = (self.revision + '\n').encode()
                    info.size = len(data)
                    package.addfile(info, io.BytesIO(data))
            return result
        manager.run = run
        with self.assertRaisesRegex(ValueError, 'source archive member'):
            manager.checkout(self.revision)
        self.assertFalse((self.stage / '.shopagent-source-revision').exists())

    def test_rollback_environment_restore_failure_records_rollback_failed(self):
        manager, commands = self.manager(fail_new=True)
        real_write = self.module.atomic_write
        def write(path, data):
            if path == self.root / 'secrets' / 'compose.env' and data == self.old_env:
                raise OSError('restore write failed')
            return real_write(path, data)
        with patch.object(self.module, 'atomic_write', side_effect=write):
            with self.assertRaisesRegex(RuntimeError, 'rollback failed'):
                manager.deploy(self.stage)
        state = json.loads((self.root / 'state' / 'last-deployment.json').read_text())
        self.assertEqual(state['status'], 'rollback_failed')

    def test_backend_readiness_requires_authenticated_tcp_query(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            if command[:2] == ['docker', 'exec']:
                required = {'mysql', '--protocol=TCP', '--host=127.0.0.1',
                            '--user=root', '--database=shopagent_ci', 'SELECT 1'}
                if not required.issubset(command):
                    raise RuntimeError('TCP database is not ready')
                self.assertIn('MYSQL_PWD', kwargs['env'])
                self.assertNotIn(kwargs['env']['MYSQL_PWD'], ' '.join(command))
                return '1\n'
            return 'passed\n'
        manager = self.module.ReleaseManager(self.root, run=run)
        with patch.object(self.module.time, 'sleep'):
            manager.test_backend(self.revision)
        self.assertEqual(sum(command[:2] == ['docker', 'exec'] for command in commands), 1)
        self.assertTrue((self.root / 'state' / ('backend-tests-' + self.revision + '.log')).exists())

    def test_partial_network_create_failure_still_cleans_exact_ci_resources(self):
        for error in (OSError('create failed'), subprocess.TimeoutExpired(['docker'], 5)):
            with self.subTest(error=type(error).__name__):
                commands = []
                def run(command, **kwargs):
                    commands.append(command)
                    if command[:3] == ['docker', 'network', 'create']:
                        raise error
                    return ''
                manager = self.module.ReleaseManager(self.root, run=run)
                with self.assertRaises(type(error)):
                    manager.test_backend(self.revision)
                self.assertTrue(any(command[:3] == ['docker', 'network', 'rm'] for command in commands))
                removals = [command for command in commands if command[:3] == ['docker', 'rm', '-f']]
                self.assertEqual(len(removals), 2)
                self.assertTrue(all(len(command) == 4 and command[3].startswith('shopagent-ci-') for command in removals))

    def test_cleanup_failure_never_skips_other_resources(self):
        for error in (OSError('cleanup failed'), subprocess.TimeoutExpired(['docker'], 5)):
            with self.subTest(error=type(error).__name__):
                commands = []
                def run(command, **kwargs):
                    commands.append(command)
                    if command[:2] == ['docker', 'exec']:
                        return '1\n'
                    if command[:3] == ['docker', 'rm', '-f'] and 'shopagent-ci-test-' in command[3]:
                        raise error
                    return 'passed\n'
                manager = self.module.ReleaseManager(self.root, run=run)
                manager.test_backend(self.revision)
                self.assertTrue(any(command[:3] == ['docker', 'rm', '-f'] and command[3].startswith('shopagent-ci-db-') for command in commands))
                self.assertTrue(any(command[:3] == ['docker', 'network', 'rm'] for command in commands))


if __name__ == '__main__':
    unittest.main()

"""Destructive cleanup acceptance uses only a disposable MySQL database."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from pymysql.constants.CLIENT import MULTI_STATEMENTS
from sqlalchemy import create_engine, inspect, text

from app.db.session import engine as application_engine
from scripts import cleanup_retired_tables as cleanup_script


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Set RUN_MYSQL_TESTS=1 for isolated MySQL tests')
class RetiredTableCleanupTests(unittest.TestCase):
    def setUp(self):
        self.database = 'bugfix_test_cleanup_' + uuid4().hex
        self.server = create_engine(application_engine.url.set(database=None))
        with self.server.connect() as connection:
            connection.execute(text(f'CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4'))
        self.engine = create_engine(application_engine.url.set(database=self.database),
                                    connect_args={'client_flag': MULTI_STATEMENTS})
        self.addCleanup(self.drop_database)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.backups = Path(directory.name)
        self.patcher = patch.object(cleanup_script, 'engine', self.engine)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        with self.engine.begin() as connection:
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, note TEXT)'))
            connection.execute(text('CREATE TABLE task_records (id INTEGER PRIMARY KEY, user_id INTEGER, FOREIGN KEY (user_id) REFERENCES users(id))'))
            connection.execute(text('CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id INTEGER, details JSON, FOREIGN KEY (task_id) REFERENCES task_records(id))'))
            connection.execute(text("INSERT INTO users VALUES (1, 'preserved')"))
            connection.execute(text('INSERT INTO task_records VALUES (10, 1)'))
            connection.execute(text('INSERT INTO task_events VALUES (20, 10, :details)'),
                               {'details': json.dumps({'text': "中文; 'quote' \\path\nsecond line", 'value': None})})

    def drop_database(self):
        self.engine.dispose()
        with self.server.connect() as connection:
            connection.execute(text(f'DROP DATABASE `{self.database}`'))
        self.server.dispose()

    def test_dry_run_reports_candidates_without_changes_or_backups(self):
        report = cleanup_script.cleanup(self.database, backup_directory=self.backups)
        self.assertEqual(report.get('candidates'), {'task_events': 1, 'task_records': 1})
        self.assertEqual(set(inspect(self.engine).get_table_names()), {'users', 'task_records', 'task_events'})
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_apply_preserves_active_data_and_backup_restores_parent_child_rows(self):
        report = cleanup_script.cleanup(self.database, apply=True, backup_directory=self.backups)
        self.assertEqual(set(inspect(self.engine).get_table_names()), {'users'})
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT note FROM users')).scalar_one(), 'preserved')
            cursor = connection.connection.cursor()
            try:
                cursor.execute(Path(report['backup']).read_text(encoding='utf-8'))
                while cursor.nextset():
                    pass
            finally:
                cursor.close()
            connection.commit()
            row = connection.execute(text('SELECT task_id, details FROM task_events')).one()
            self.assertEqual(row.task_id, 10)
            self.assertEqual(json.loads(row.details), {'text': "中文; 'quote' \\path\nsecond line", 'value': None})
        self.assertTrue(report['retained_row_counts_unchanged'])

    def test_retained_foreign_key_blocks_cleanup(self):
        with self.engine.begin() as connection:
            connection.execute(text('CREATE TABLE active_jobs (id INTEGER PRIMARY KEY, task_id INTEGER, FOREIGN KEY (task_id) REFERENCES task_records(id))'))
        with self.assertRaisesRegex(RuntimeError, 'retained table references'):
            cleanup_script.cleanup(self.database, apply=True, backup_directory=self.backups)
        self.assertIn('task_records', inspect(self.engine).get_table_names())

    def test_view_reference_blocks_cleanup(self):
        with self.engine.begin() as connection:
            connection.execute(text('CREATE VIEW active_tasks AS SELECT id FROM task_records'))
        with self.assertRaisesRegex(RuntimeError, 'stored database object'):
            cleanup_script.cleanup(self.database, apply=True, backup_directory=self.backups)
        self.assertIn('task_records', inspect(self.engine).get_table_names())

    def test_backup_write_failure_leaves_tables_intact(self):
        with patch.object(Path, 'open', side_effect=OSError('Backup disk unavailable')):
            with self.assertRaisesRegex(OSError, 'Backup disk unavailable'):
                cleanup_script.cleanup(self.database, apply=True, backup_directory=self.backups)
        self.assertIn('task_events', inspect(self.engine).get_table_names())

    def test_wrong_database_and_registered_tables_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            cleanup_script.cleanup('different_database', apply=True)
        with patch.object(cleanup_script, 'RETIRED', ('users',)):
            with self.assertRaisesRegex(RuntimeError, 'still registered'):
                cleanup_script.cleanup(self.database, apply=True)


if __name__ == '__main__':
    unittest.main()

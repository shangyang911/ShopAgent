"""Back up and remove only the three retired logistics-issue tables.

Run from backend:
  .venv/Scripts/python.exe scripts/cleanup_retired_tables.py --database dianshang --apply
Without --apply, the script only reports the proposed cleanup.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from app.db.base import Base
from app.db.session import engine


RETIRED = ('logistics_issue_actions', 'logistics_issue_parcels', 'logistics_issues')


def cleanup(database, apply=False, backup_directory=None):
    if engine.dialect.name != 'mysql' or engine.url.database != database:
        raise RuntimeError('Configured connection does not match the requested MySQL database')
    if set(RETIRED) & set(Base.metadata.tables):
        raise RuntimeError('Retired tables are still registered in application metadata')
    with engine.connect() as connection:
        inspector = inspect(connection)
        before = set(inspector.get_table_names())
        targets = [name for name in RETIRED if name in before]
        if not targets:
            return {'database': database, 'removed': [], 'already_clean': True, 'tables': len(before)}
        incoming = connection.execute(text('''
            SELECT TABLE_SCHEMA, TABLE_NAME, REFERENCED_TABLE_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE REFERENCED_TABLE_SCHEMA=:db
              AND REFERENCED_TABLE_NAME IN (:a,:b,:c)
        '''), dict(db=database, a=RETIRED[0], b=RETIRED[1], c=RETIRED[2])).mappings()
        if any(row['TABLE_SCHEMA'] != database or row['TABLE_NAME'] not in RETIRED for row in incoming):
            raise RuntimeError('A retained table references a retired table; cleanup aborted')
        for table, schema_column, definition in (
            ('VIEWS', 'TABLE_SCHEMA', 'VIEW_DEFINITION'),
            ('TRIGGERS', 'TRIGGER_SCHEMA', 'ACTION_STATEMENT'),
            ('ROUTINES', 'ROUTINE_SCHEMA', 'ROUTINE_DEFINITION'),
            ('EVENTS', 'EVENT_SCHEMA', 'EVENT_DEFINITION'),
        ):
            definitions = connection.execute(text(
                f'SELECT `{definition}` FROM information_schema.{table} WHERE `{schema_column}`=:db'
            ), {'db': database}).scalars()
            if any(value is None or any(name in value.lower() for name in RETIRED) for value in definitions):
                raise RuntimeError('A stored database object may reference a retired table')
        counts = {name: connection.execute(text(f'SELECT COUNT(*) FROM `{name}`')).scalar_one()
                  for name in targets}
        report = {'database': database, 'candidates': counts, 'tables_before': len(before)}
        if not apply:
            return {**report, 'dry_run': True}

        # Snapshot all retained row counts, then preserve exact CREATE statements and data.
        kept = sorted(before - set(targets))
        kept_counts = {name: connection.execute(text(f'SELECT COUNT(*) FROM `{name}`')).scalar_one()
                       for name in kept}
        statements = [f'-- Backup of retired tables from {database}; restore into the same database.']
        cursor = connection.connection.cursor()
        try:
            for name in reversed(targets):  # Parent before children in the restore script.
                ddl = connection.execute(text(f'SHOW CREATE TABLE `{name}`')).one()[1]
                statements.append(ddl + ';')
                result = connection.execute(text(f'SELECT * FROM `{name}`'))
                columns = ', '.join(f'`{column}`' for column in result.keys())
                placeholders = ', '.join(['%s'] * len(result.keys()))
                for row in result:
                    statements.append(cursor.mogrify(
                        f'INSERT INTO `{name}` ({columns}) VALUES ({placeholders});', tuple(row)
                    ))
        finally:
            cursor.close()
        content = '\n\n'.join(statements) + '\n'
        folder = Path(backup_directory) if backup_directory else Path(__file__).resolve().parents[2] / '.local-test' / 'database-backups'
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / ('retired-logistics-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sql')
        with backup.open('x', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if backup.read_text(encoding='utf-8') != content:
            raise RuntimeError('Backup verification failed; no tables were dropped')
        # One multi-table DROP; children first, with foreign-key checks left enabled.
        connection.execute(text('DROP TABLE ' + ', '.join(f'`{name}`' for name in targets)))
        connection.commit()
        after = set(inspect(connection).get_table_names())
        if after != before - set(targets):
            raise RuntimeError('Unexpected table membership after cleanup; inspect backup')
        after_counts = {name: connection.execute(text(f'SELECT COUNT(*) FROM `{name}`')).scalar_one()
                        for name in kept}
        if kept_counts != after_counts:
            raise RuntimeError('Retained row counts changed during cleanup; inspect concurrent activity')
        return {**report, 'removed': targets, 'tables_after': len(after), 'backup': str(backup),
                'backup_sha256': sha256(backup.read_bytes()).hexdigest(), 'retained_row_counts_unchanged': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(cleanup(args.database, args.apply), ensure_ascii=True))

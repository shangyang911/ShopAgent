"""Add nullable SKU snapshots to existing tables; preserve all historical facts."""
from sqlalchemy import inspect, text
from app.db.base import Base


ADDITIONS = {
    'product_images': {'role': "VARCHAR(16) NOT NULL DEFAULT 'gallery'"},
    'order_items': {'sku_id': 'INTEGER NULL', 'sku_code': 'VARCHAR(80) NULL', 'sku_label': 'VARCHAR(100) NULL'},
    'inventory_movements': {'sku_id': 'INTEGER NULL', 'sku_code_snapshot': 'VARCHAR(80) NULL'},
    'inbound_receipt_lines': {'sku_id': 'INTEGER NULL'},
    'stock_adjustment_lines': {'sku_id': 'INTEGER NULL'},
}


def initialize_product_schema(engine):
    with engine.connect() as connection:
        mysql = engine.dialect.name == 'mysql'
        lock_name = f'{engine.url.database}:product-skus-v1'
        if mysql and connection.execute(text('SELECT GET_LOCK(:key, 30)'), {'key': lock_name}).scalar_one() != 1:
            raise RuntimeError('商品 SKU 数据库升级锁获取失败')
        try:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            for table, definitions in ADDITIONS.items():
                if table not in tables:
                    continue
                columns = {column['name'] for column in inspector.get_columns(table)}
                for name, definition in definitions.items():
                    if name not in columns:
                        connection.execute(text(f'ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}'))
            Base.metadata.create_all(connection)
            connection.commit()
        finally:
            if mysql:
                connection.execute(text('SELECT RELEASE_LOCK(:key)'), {'key': lock_name})
                connection.commit()

"""Optional browser-smoke server. DATABASE_URL must point to an isolated SQLite file."""
import os
import uvicorn

if not os.environ.get('DATABASE_URL', '').startswith('sqlite:'):
    raise RuntimeError('Browser smoke tests require an explicit isolated SQLite DATABASE_URL')

from app.main import app

for middleware in app.user_middleware:
    if 'allow_origins' in middleware.kwargs:
        middleware.kwargs['allow_origins'].append('http://127.0.0.1:5175')

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8002)

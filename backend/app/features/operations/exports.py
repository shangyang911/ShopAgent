"""CSV exports preserve snapshots and prevent text from becoming spreadsheet formulas."""
import csv
from io import StringIO

from fastapi.responses import StreamingResponse

from .common import wire


def csv_response(filename, headers, rows):
    def content():
        buffer = StringIO(newline="")
        writer = csv.writer(buffer)
        yield "\ufeff"  # Excel recognises Chinese column labels as UTF-8.
        writer.writerow(headers)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for row in rows:
            cells = []
            for value in row:
                value = wire(value)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                cells.append(value)
            writer.writerow(cells)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

    return StreamingResponse(content(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})

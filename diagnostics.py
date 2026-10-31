"""Small, local error log. No polling or persistent file handle."""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

_LOCK = threading.Lock()


def report(db_path, component, root, message, *, scan=False):
    try:
        with _LOCK:
            handler = RotatingFileHandler(
                os.path.join(os.path.dirname(os.path.abspath(db_path)),
                             'fileburglar-scans.log' if scan else 'fileburglar-errors.log'),
                maxBytes=512 * 1024, backupCount=1, encoding='utf-8', delay=True)
            try:
                handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                info = sys.exc_info()
                record = logging.LogRecord('fileburglar', logging.INFO if scan else logging.ERROR, '', 0,
                                           '%s %s: %s', (component, root, message),
                                           info if info[0] else None)
                handler.handle(record)
            finally:
                handler.close()
    except OSError:
        pass  # A log write failure must not replace the original drive error.

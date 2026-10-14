import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

LOG_FILENAME = "dosoft.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 2

logger = logging.getLogger("dosoft")

_throttle_lock = threading.Lock()
_throttle_last = {}


def setup_logging():
    """Ecrit dosoft.log dans le dossier de travail (1 Mo max, 2 archives)."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        handler = RotatingFileHandler(
            os.path.abspath(LOG_FILENAME),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
    except Exception:
        logger.addHandler(logging.NullHandler())
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] (pid %(process)d, %(threadName)s) %(message)s"
    ))
    logger.addHandler(handler)

    def log_uncaught(exc_type, exc_value, exc_tb):
        logger.critical("Exception non interceptée", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    default_thread_hook = threading.excepthook

    def log_uncaught_thread(args):
        logger.error(
            "Exception non interceptée dans le thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        default_thread_hook(args)

    sys.excepthook = log_uncaught
    threading.excepthook = log_uncaught_thread


def log_throttled(key, level, message, *args, exc_info=False, interval=60.0):
    """Logge au plus une fois par intervalle pour cette clé (boucles de polling)."""
    now = time.monotonic()
    with _throttle_lock:
        last = _throttle_last.get(key)
        if last is not None and now - last < interval:
            return
        _throttle_last[key] = now
    logger.log(level, message, *args, exc_info=exc_info)


def log_exception_throttled(key, message, *args, interval=60.0):
    """Logge l'exception en cours, au plus une fois par intervalle pour cette clé."""
    log_throttled(key, logging.ERROR, message, *args, exc_info=True, interval=interval)

"""
Logging + tqdm-suppression bootstrap. Import this module FIRST in the entry
process so TQDM_DISABLE is set before mlx_whisper or whisper are loaded.
"""
import os

# Must precede any tqdm-using import to actually suppress the progress bars
# that otherwise flood dictation_error.log with thousands of lines.
os.environ.setdefault("TQDM_DISABLE", "1")

import logging
from logging.handlers import RotatingFileHandler

from dictation_config import LOG_FILE

_handler = RotatingFileHandler(str(LOG_FILE), maxBytes=1_000_000, backupCount=3)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

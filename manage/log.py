import logging
import sys
from logging.handlers import RotatingFileHandler

logger = logging.getLogger("main.py")
py_logger = logging.getLogger("pyrogram")


logger.setLevel(logging.INFO)
py_logger.setLevel(logging.WARNING) 

formatter = logging.Formatter(
    '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

file_handler = RotatingFileHandler(
    "downloader.log", 
    maxBytes=10 * 1024 * 1024,
    backupCount=3, 
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    py_logger.addHandler(file_handler)
    
    logger.propagate = False
    py_logger.propagate = False
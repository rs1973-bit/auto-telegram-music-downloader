import os
from manage.setup import cfg

TEMP_PATH = cfg.temp_path
cover = os.path.join(TEMP_PATH, "cover")
lyrice = os.path.join(TEMP_PATH, "lyr")
if not os.path.exists(cover): os.mkdir(cover)
if not os.path.exists(lyrice): os.mkdir(lyrice)
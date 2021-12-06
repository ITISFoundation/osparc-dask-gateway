import re
import sys

import osparc_dask_gateway.backend.osparc
from dask_gateway_server.app import main


def start():
    sys.argv[0] = re.sub(r"(-script\.pyw|\.exe)?$", "", sys.argv[0])
    sys.exit(main())

"""Run Nova's noVNC proxy with the Python 3.14-compatible fork method.

Remove this wrapper when the deployed Nova includes upstream bug 2156932's fix.
"""

import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.set_start_method("fork", force=True)
    from nova.cmd.novncproxy import main

    sys.exit(main())

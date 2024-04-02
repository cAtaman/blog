import sys
from collections.abc import Sequence

from sphinx.cmd import make_mode
from sphinx_autobuild.__main__ import main as autobuild_main

CUSTOM_BUILDERS = [
    ("",      "livehtml",       "to start a webserver with the htmls always reloaded on changes"),
]
make_mode.BUILDERS = CUSTOM_BUILDERS + make_mode.BUILDERS


class CustomMake(make_mode.Make):
    pass


def run_make_mode(args: Sequence[str]) -> int:
    if len(args) < 3:
        print('Error: at least 3 arguments (builder, source '
              'dir, build dir) are required.', file=sys.stderr)
        return 1
    make = CustomMake(args[1], args[2], args[3:])
    run_method = 'build_' + args[0]
    if hasattr(make, run_method):
        return getattr(make, run_method)()
    return autobuild_main()

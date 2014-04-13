# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from distutils.version import StrictVersion
import os
import platform
import sys


sys_strict_version = StrictVersion('{}.{}.{}'.format(
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro))
if sys_strict_version < StrictVersion('2.6'):
    raise Exception("Please upgrade your Python to 2.6 or 2.7")
if sys_strict_version >= StrictVersion('3.0'):
    raise Exception("Anki does not work with Python 3")
if sys.getfilesystemencoding().lower() in ("ascii", "ansi_x3.4-1968"):
    raise Exception("Anki requires a UTF-8 locale.")

try:
    import simplejson as json
except:
    import json as json
if json.__version__ < "1.7.3":
    raise Exception("SimpleJSON must be 1.7.3 or later.")

# add path to bundled third party libs
ext = os.path.realpath(os.path.join(
    os.path.dirname(__file__), "../thirdparty"))
sys.path.insert(0, ext)
arch = platform.architecture()
if arch[1] == "ELF":
    # add arch-dependent libs
    sys.path.insert(0, os.path.join(ext, "py2.%d-%s" % (
        sys.version_info[1], arch[0][0:2])))

version="2.0.24" # build scripts grep this line, so preserve formatting

__version__ = version
# We’ve been told to not touch the “version” above, so add the
# standard (i.e. PEP 396 http://www.python.org/dev/peps/pep-0396)
# variable, instead of replacing it.

from anki.storage import Collection
__all__ = ["Collection"]

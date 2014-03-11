# Copyright: Damien Elmes <anki@ichi2.net>
#
# License: GNU AGPL, version 3 or later;
# http://www.gnu.org/licenses/agpl.html
#
# imports are all in this file to make moving to pyside easier in the future
# fixme: make sure not to optimize imports on this file


import os
import sys
import traceback

from distutils.version import StrictVersion

from PyQt4.QtCore import *
from PyQt4.QtGui import *
from PyQt4.QtWebKit import QWebPage, QWebView, QWebSettings
from PyQt4.QtNetwork import QLocalServer, QLocalSocket

from anki.utils import isWin, isMac


def debug():
    from PyQt4.QtCore import pyqtRemoveInputHook
    from pdb import set_trace
    pyqtRemoveInputHook()
    set_trace()

if os.environ.get("DEBUG"):
    def info(type, value, tb):
        from PyQt4.QtCore import pyqtRemoveInputHook
        for line in traceback.format_exception(type, value, tb):
            sys.stdout.write(line)
        pyqtRemoveInputHook()
        from pdb import pm
        pm()
    sys.excepthook = info

# Don't muck around with bit shifts. RAS 2013-03-31
qtmajor, qtminor, qtpatch = QT_VERSION_STR.split('.')
qt_version = StrictVersion(QT_VERSION_STR)

# This is my private version. I don't have ancient Qt versions lying
# around. Take out the patch for those.

if isWin or isMac:
    # we no longer use this, but want it included in the mac+win builds
    # so we don't break add-ons that use it. any new add-ons should use
    # the above variables instead
    from PyQt4 import pyqtconfig

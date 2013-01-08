# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import sip
sip.setapi('QString', 2)
sip.setapi('QVariant', 2)
sip.setapi('QUrl', 2)


from distutils.version import StrictVersion
import __builtin__
import atexit
import gettext
import locale
import optparse
import os
import sys
import tempfile
from PyQt4.QtCore import QCoreApplication, QEvent, QIODevice, \
    QSharedMemory, QTranslator, Qt, SIGNAL
from PyQt4.QtGui import QApplication, QMessageBox
from PyQt4.QtNetwork import QLocalServer, QLocalSocket

from aqt.profiles import default_base
from aqt.qt import qt_version
from anki.consts import HELP_SITE
from anki.lang import langDir
from anki.utils import isMac, isWin
import anki.lang

appVersion = "2.0.4"
appWebsite = "http://ankisrs.net/"
appChanges = "http://ankisrs.net/docs/changes.html"
appDonate = "http://ankisrs.net/support/"
appShared = "https://ankiweb.net/shared/"
appUpdate = "https://ankiweb.net/update/desktop"
appHelpSite = HELP_SITE
mw = None  # set on init

moduleDir = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]

try:
    import aqt.forms
except ImportError as e:
    if "forms" in str(e):
        print "If you're running from git, did you run build_ui.sh?"
        print
    raise

# Dialog manager - manages modeless windows
##########################################################################


class DialogManager(object):

    def __init__(self):
        from aqt import addcards, browser, editcurrent
        self._dialogs = {
            "AddCards": [addcards.AddCards, None],
            "Browser": [browser.Browser, None],
            "EditCurrent": [editcurrent.EditCurrent, None],
        }

    def open(self, name, *args):
        (creator, instance) = self._dialogs[name]
        if instance:
            instance.activateWindow()
            instance.raise_()
            return instance
        else:
            instance = creator(*args)
            self._dialogs[name][1] = instance
            return instance

    def close(self, name):
        self._dialogs[name] = [self._dialogs[name][0], None]

    def closeAll(self):
        for (n, (creator, instance)) in self._dialogs.items():
            if instance:
                instance.forceClose = True
                instance.close()
                self.close(n)

dialogs = DialogManager()

# Language handling
##########################################################################
# Qt requires its translator to be installed before any GUI widgets are
# loaded, and we need the Qt language to match the gettext language or
# translated shortcuts will not work.

_gtrans = None
_qtrans = None


def setupLang(pm, app, force=None):
    global _gtrans, _qtrans
    try:
        locale.setlocale(locale.LC_ALL, '')
    except:
        pass
    lang = force or pm.meta["defaultLang"]
    dir = langDir()
    # gettext
    _gtrans = gettext.translation(
        'anki', dir, languages=[lang], fallback=True)
    __builtin__.__dict__['_'] = _gtrans.ugettext
    __builtin__.__dict__['ngettext'] = _gtrans.ungettext
    anki.lang.setLang(lang, local=False)
    if lang in ("he", "ar", "fa"):
        app.setLayoutDirection(Qt.RightToLeft)
    else:
        app.setLayoutDirection(Qt.LeftToRight)
    # qt
    _qtrans = QTranslator()
    if _qtrans.load("qt_" + lang, dir):
        app.installTranslator(_qtrans)

# App initialisation
##########################################################################


class AnkiApp(QApplication):

    # Single instance support on Win32/Linux
    ##################################################
    # Now single instance per base dir support. RAS 2012-08-24, 2013-01-05

    TMOUT = 5000

    def __init__(self, argv, key_path):
        QApplication.__init__(self, argv)
        self._argv = argv
        self.key = os.path.join(key_path, 'ipc')
        if isWin:
            self.key = '\\\\pipe\\.\\' + self.key
        self._shmem = QSharedMemory(self.key)
        self.alreadyRunning = self._shmem.attach()

    def secondInstance(self, decks_to_load):
        if not self.alreadyRunning:
            # use a 1 byte shared memory instance to signal we exist
            if not self._shmem.create(1):
                raise Exception("shared memory not supported")
            QLocalServer.removeServer(self.key)
            atexit.register(self._cleanup)
            # and a named pipe/unix domain socket for ipc
            self._srv = QLocalServer(self)
            self.connect(self._srv, SIGNAL("newConnection()"), self.onRecv)
            self._srv.listen(self.key)
            print 'key:', self.key
            print 'server listens on:', self._srv.serverName()
        else:
            # Treat all remaining args as decks to load. If there are
            # none, send a blank screen to just raise the existing
            # window
            if decks_to_load:
                for deck_to_load in decks_to_load:
                    self.sendMsg(os.path.abspath(deck_to_load))
            else:
                self.sendMsg('raise')
        # Always return a Bolean
        return self.alreadyRunning

    def sendMsg(self, txt):
        sock = QLocalSocket(self)
        sock.connectToServer(self.key, QIODevice.WriteOnly)
        if not sock.waitForConnected(self.TMOUT):
            raise Exception("existing instance not responding")
        sock.write(txt)
        if not sock.waitForBytesWritten(self.TMOUT):
            raise Exception("existing instance not emptying")
        sock.disconnectFromServer()

    def onRecv(self):
        sock = self._srv.nextPendingConnection()
        if not sock.waitForReadyRead(self.TMOUT):
            sys.stderr.write(sock.errorString())
            return
        buf = sock.readAll()
        self.emit(SIGNAL("appMsg"), buf)
        sock.disconnectFromServer()

    # OS X file/url handler
    ##################################################

    def event(self, evt):
        if evt.type() == QEvent.FileOpen:
            self.emit(SIGNAL("appMsg"), evt.file() or "raise")
            return True
        return QApplication.event(self, evt)

    def _cleanup(self):
        self._shmem.detach()
        self._srv.close()


def parseArgs(argv):
    "Returns (opts, args)."
    parser = optparse.OptionParser()
    parser.usage = "%prog [OPTIONS] [file to import]"
    parser.add_option("-b", "--base", help="path to base folder")
    parser.add_option("-p", "--profile", help="profile name to load")
    parser.add_option("-l", "--lang", help="interface language (en, de, etc)")
    return parser.parse_args(argv[1:])


def run():
    global mw

    # on osx we'll need to add the qt plugins to the search path
    if isMac and getattr(sys, 'frozen', None):
        rd = os.path.abspath(moduleDir + "/../../..")
        QCoreApplication.setLibraryPaths([rd])

    # parse args
    # Move this before the creation of the app. Like that we can use
    # the base dir as the key to the shared memory. Intended as a fix
    # to issue #12 (originally issue #3210).
    opts, args = parseArgs(sys.argv)
    # Use abspath to avoid any disambiguation when we use this as the
    # key for signaling/remote import. Also, set the default here
    # already to make the key consistent.
    # Unroll. Looks like it is already unicode on Windows but not on Linux.
    if not opts.base:
        opts.base = default_base()
    try:
        opts.base = unicode(opts.base or default_base(),
                            sys.getfilesystemencoding())
    except TypeError:
        # Already unicode.
        pass
    opts.base = os.path.abspath(opts.base)
    opts.profile = unicode(opts.profile or "", sys.getfilesystemencoding())

    # create the app
    # The opts.base is only used to attach to the shared memory at
    # this time.
    app = AnkiApp(sys.argv, opts.base)
    QCoreApplication.setApplicationName("Anki")
    # Pass along the remaining args.
    if app.secondInstance(args):
        # we've signaled the primary instance, so we should close
        return

    # we must have a usable temp dir
    try:
        tempfile.gettempdir()
    except:
        QMessageBox.critical(
            None, "Error", """\
No usable temporary folder found. Make sure C:\\temp exists or TEMP in your \
environment points to a valid, writable folder.""")
        return

    # Qt version must not be too old. Use StrictVersion to avoid
    # surprises.
    if qt_version < StrictVersion('4.7'):
        QMessageBox.critical(
            None, "Error", "Anki requires Qt4.7 or later.")
        return

    # This is done above now. Do not re-activate a second parse
    # here. (Especially when merging patches.)
    # # parse args
    # opts, args = parseArgs(sys.argv)
    # opts.base = unicode(opts.base or "", sys.getfilesystemencoding())
    # opts.profile = unicode(opts.profile or "", sys.getfilesystemencoding())

    # profile manager
    from aqt.profiles import ProfileManager
    pm = ProfileManager(opts.base, opts.profile)

    # i18n
    setupLang(pm, app, opts.lang)

    # remaining pm init
    pm.ensureProfile()

    # load the main window
    import aqt.main
    mw = aqt.main.AnkiQt(app, pm, args)
    app.exec_()

if __name__ == "__main__":
    run()

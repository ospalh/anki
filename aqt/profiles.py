# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

# Profile handling
##########################################################################
# - Saves in pickles rather than json to easily store Qt window state.
# - Saves in sqlite rather than a flat file so the config can't be corrupted

from distutils.version import StrictVersion
import cPickle
import locale
import os
import random
import re


from PyQt4.QtCore import QT_VERSION_STR, SIGNAL
from PyQt4.QtGui import QDialog, QMessageBox

from anki.db import DB
from anki.lang import _, langs
from anki.utils import checksum, intTime, isMac, isWin
import aqt
from aqt.utils import showWarning
import aqt.forms
from send2trash import send2trash

if StrictVersion(QT_VERSION_STR) >= StrictVersion("5.0"):
    from PyQt4.QtCore import QStandardPaths
else:
    from PyQt4.QtGui import QDesktopServices

metaConf = dict(
    ver=0,
    updates=True,
    created=intTime(),
    id=random.randrange(0, 2**63),
    lastMsg=-1,
    suppressUpdate=False,
    firstRun=True,
    defaultLang=None,
    disabledAddons=[])

profileConf = dict(
    # profile
    key=None,
    mainWindowGeom=None,
    mainWindowState=None,
    numBackups=30,
    lastOptimize=intTime(),
    # editing
    fullSearch=False,
    searchHistory=[],
    lastColour="#00f",
    stripHTML=True,
    pastePNG=False,
    # not exposed in gui
    deleteMedia=False,
    preserveKeyboard=True,
    # syncing
    syncKey=None,
    syncMedia=True,
    autoSync=True,
    # importing
    allowHTML=False,
    importMode=0)


class ProfileManager(object):

    def __init__(self, base, profile=None):
        # Base has no default any more. We use the base dir when we
        # set up the QApplication now, so it has to be set before
        # this.
        self.name = None
        self.base = base
        self.new_base_asked = False
        self.ensureBaseExists()
        # load metadata
        self.firstRun = self._loadMeta()
        # did the user request a profile to start up with?
        if profile:
            try:
                self.load(profile)
            except TypeError:
                raise Exception("Provided profile does not exist.")

    # Base creation
    ######################################################################

    def ensureBaseExists(self):
        if self.base != default_base() and not os.path.exists(self.base):
            new_base_button = QMessageBox.question(
                None, "New base?",
                "Create new base folder at {0}?".format(self.base),
                QMessageBox.Yes | QMessageBox.No)
            if QMessageBox.Yes == new_base_button:
                self.new_base_asked = True
            else:
                raise Exception("User canceled profile creation.")
        try:
            self._ensureExists(self.base)
        except:
            # can't translate, as lang not initialized
            QMessageBox.critical(
                None, "Error", """\
Anki can't write to the harddisk. Please see the \
documentation for information on using a flash drive.""")
            raise

    # Profile load/save
    ######################################################################

    def profiles(self):
        return sorted(
            unicode(x, "utf8") for x in
            self.db.list("select name from profiles")
            if x != "_global")

    def load(self, name, passwd=None):
        prof = cPickle.loads(
            self.db.scalar("select data from profiles where name = ?",
                           name.encode("utf8")))
        if prof['key'] and prof['key'] != self._pwhash(passwd):
            self.name = None
            return False
        if name != "_global":
            self.name = name
            self.profile = prof
        return True

    def save(self):
        sql = "update profiles set data = ? where name = ?"
        self.db.execute(sql, cPickle.dumps(self.profile),
                        self.name.encode("utf8"))
        self.db.execute(sql, cPickle.dumps(self.meta), "_global")
        self.db.commit()

    def create(self, name):
        prof = profileConf.copy()
        self.db.execute("insert into profiles values (?, ?)",
                        name.encode("utf8"), cPickle.dumps(prof))
        self.db.commit()

    def remove(self, name):
        p = self.profileFolder()
        if os.path.exists(p):
            send2trash(p)
        self.db.execute("delete from profiles where name = ?",
                        name.encode("utf8"))
        self.db.commit()

    def rename(self, name):
        oldName = self.name
        oldFolder = self.profileFolder()
        self.name = name
        newFolder = self.profileFolder(create=False)
        if os.path.exists(newFolder):
            showWarning(_("Folder already exists."))
            self.name = oldName
            return
        # update name
        self.db.execute("update profiles set name = ? where name = ?",
                        name.encode("utf8"), oldName.encode("utf-8"))
        # rename folder
        os.rename(oldFolder, newFolder)
        self.db.commit()

    # Folder handling
    ######################################################################

    def profileFolder(self, create=True):
        path = os.path.join(self.base, self.name)
        if create:
            self._ensureExists(path)
        return path

    def addonFolder(self):
        return self._ensureExists(os.path.join(self.base, "addons"))

    def backupFolder(self):
        return self._ensureExists(
            os.path.join(self.profileFolder(), "backups"))

    def collectionPath(self):
        return os.path.join(self.profileFolder(), "collection.anki2")

    # Helpers
    ######################################################################

    def _ensureExists(self, path):
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    def _loadMeta(self):
        path = os.path.join(self.base, "prefs.db")
        new = not os.path.exists(path)
        if new and self.base != default_base() and not self.new_base_asked:
            new_db_button = QMessageBox.question(
                None, "New db?",
                "Create new Anki2 database {0}?".format(path),
                QMessageBox.Yes | QMessageBox.No)
            if not QMessageBox.Yes == new_db_button:
                raise Exception("User canceled database creation.")
        self.db = DB(path, text=str)
        self.db.execute("""
create table if not exists profiles
(name text primary key, data text not null);""")
        if not new:
            # load previously created
            try:
                self.meta = cPickle.loads(
                    self.db.scalar(
                        "select data from profiles where name = '_global'"))
                return
            except:
                # if we can't load profile, start with a new one
                os.rename(path, path + ".broken")
                return self._loadMeta()
        # create a default global profile
        self.meta = metaConf.copy()
        self.db.execute(
            "insert or replace into profiles values ('_global', ?)",
            cPickle.dumps(metaConf))
        self._setDefaultLang()
        return True

    def ensureProfile(self):
        "Create a new profile if none exists."
        if self.firstRun:
            try:
                import getpass
            except ImportError:
                self.create(_("User 1"))
            else:
                self.create(getpass.getuser())
            p = os.path.join(self.base, "README.txt")
            open(p, "w").write((_("""\
This folder stores all of your Anki data in a single location,
to make backups easy. To tell Anki to use a different location,
please see:

%s
""") % (aqt.appHelpSite + "#startupopts")).encode("utf8"))

    def _pwhash(self, passwd):
        return checksum(unicode(self.meta['id']) + unicode(passwd))

    # Default language
    ######################################################################
    # On first run, allow the user to choose the default language

    def _setDefaultLang(self):
        # the dialog expects _ to be defined, but we're running before
        # setupLang() has been called. so we create a dummy op for now
        import __builtin__
        __builtin__.__dict__['_'] = lambda x: x
        # create dialog

        class NoCloseDiag(QDialog):
            def reject(self):
                pass
        d = self.langDiag = NoCloseDiag()
        f = self.langForm = aqt.forms.setlang.Ui_Dialog()
        f.setupUi(d)
        d.connect(d, SIGNAL("accepted()"), self._onLangSelected)
        d.connect(d, SIGNAL("rejected()"), lambda: True)
        # default to the system language
        try:
            (lang, enc) = locale.getdefaultlocale()
        except:
            # fails on osx
            lang = "en"
        if lang and lang not in ("pt_BR", "zh_CN", "zh_TW"):
            lang = re.sub("(.*)_.*", "\\1", lang)
        # find index
        idx = None
        en = None
        for c, (name, code) in enumerate(langs):
            if code == "en":
                en = c
            if code == lang:
                idx = c
        # if the system language isn't available, revert to english
        if idx is None:
            idx = en
        # update list
        f.lang.addItems([x[0] for x in langs])
        f.lang.setCurrentRow(idx)
        d.exec_()

    def _onLangSelected(self):
        f = self.langForm
        code = langs[f.lang.currentRow()][1]
        self.meta['defaultLang'] = code
        sql = "update profiles set data = ? where name = ?"
        self.db.execute(sql, cPickle.dumps(self.meta), "_global")
        self.db.commit()


def default_base():
    """
    Return the default directory.

    Returns the path to the default directory to store all the data
    when the user didn't provide one.
    """
    if isWin:
        if StrictVersion(QT_VERSION_STR) >= StrictVersion("5.0"):
            loc = QStandardPaths.writeableLocation(
                QStandardPaths.DocumentsLocation)
        else:
            loc = QDesktopServices.storageLocation(
                QDesktopServices.DocumentsLocation)
        return os.path.join(loc, "Anki")
    elif isMac:
        return os.path.expanduser("~/Documents/Anki")
    else:
        return os.path.expanduser("~/anki")

# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import os
import re
import shutil
import sys
import unicodedata
import urllib
import zipfile
from cStringIO import StringIO

from anki.consts import MEDIA_ADD, MEDIA_REM, MODEL_CLOZE, SYNC_ZIP_COUNT, \
    SYNC_ZIP_SIZE
from anki.db import DB
from anki.latex import mungeQA
from anki.utils import checksum, isWin, isMac, json


class MediaManager(object):

    # other code depends on this order, so don't reorder
    regexps = (u"(?i)(\[sound:([^]]+)\])",
               u"(?i)(<(?:img|embed)[^>]+src=[\"']?([^\"'>]+)[\"']?[^>]*>)",
               u"(?i)(<object[^>]+data=[\"']?([^\"'>]+)[\"']?[^>]*>)")

    def __init__(self, col, server):
        self.col = col
        if server:
            self._dir = None
            return
        # media directory
        self._dir = re.sub(
            u"(?i)\.(anki2)$", ".media", self.col.path, flags=re.UNICODE)
        # convert dir to unicode if it's not already
        if isinstance(self._dir, str):
            self._dir = unicode(self._dir, sys.getfilesystemencoding())
        if not os.path.exists(self._dir):
            os.makedirs(self._dir)
        try:
            self._oldcwd = os.getcwd()
        except OSError:
            # cwd doesn't exist
            self._oldcwd = None
        os.chdir(self._dir)
        # Two variables that are used during syncs on Macs.
        self._problem_files = None
        self._all_media_files = None
        # change database
        self.connect()

    def connect(self):
        if self.col.server:
            return
        path = self.dir() + ".db"
        create = not os.path.exists(path)
        os.chdir(self._dir)
        self.db = DB(path)
        if create:
            self._initDB()

    def close(self):
        if self.col.server:
            return
        self.db.close()
        self.db = None
        # change cwd back to old location
        if self._oldcwd:
            try:
                os.chdir(self._oldcwd)
            except:
                # may have been deleted
                pass

    def dir(self):
        return self._dir

    # Adding media
    ##########################################################################

    def addFile(self, opath):
        """
        Return name of a file in the media dir with the content of opath.

        Copy opath to the media dir, and return the new filename.
        If the same name exists, compare checksums and just return the
        name when they match.
        This function avoids new names that differ only in
        capitalization or are Unicode equivalents of existing media
        files.
        """
        mdir = self.dir()
        # remove any dangerous characters
        base = re.sub(r"[][<>:/\\&?\"\|]", "", os.path.basename(opath))
        # Check against normalized, lowercase versions to avoid
        # problems with name clasches.
        normalized_base = unicodedata.normalize('NFKD', base).lower()
        dst = os.path.join(mdir, base)
        normalized_media_files = [unicodedata.normalize('NFKD', base).lower()
                                  for fn in os.listdir(mdir)]
        #  if it doesn't exist, copy it directly
        if not normalized_base in normalized_media_files:
            shutil.copyfile(opath, dst)
            return base
        # if it's identical, reuse
        if self.filesIdentical(opath, dst):
            return base
        # otherwise, find a unique name
        (root, ext) = os.path.splitext(base)

        def repl(match):
            n = int(match.group(1))
            return " (%d)" % (n + 1)
        while True:
            path = os.path.join(mdir, root + ext)
            normalized_base = unicodedata.normalize(
                'NFKD', root + ext).lower()
            if not normalized_base in normalized_media_files:
                break
            reg = " \((\d+)\)$"
            if not re.search(reg, root, flags=re.UNICODE):
                root = root + " (1)"
            else:
                root = re.sub(reg, repl, root, flags=re.UNICODE)
        # copy and return
        shutil.copyfile(opath, path)
        return os.path.basename(os.path.basename(path))

    def filesIdentical(self, path1, path2):
        "True if files are the same."
        try:
            # The try is needed now as the real file name may be an
            # uppercase version of path2.
            return (checksum(open(path1, "rb").read()) ==
                    checksum(open(path2, "rb").read()))
        except IOError:
            return False

    # String manipulation
    ##########################################################################

    def filesInStr(self, mid, string, includeRemote=False):
        l = []
        model = self.col.models.get(mid)
        strings = []
        if model['type'] == MODEL_CLOZE and "{{c" in string:
            # if the field has clozes in it, we'll need to expand the
            # possibilities so we can render latex
            strings = self._expandClozes(string)
        else:
            strings = [string]
        for string in strings:
            # handle latex
            string = mungeQA(string, None, None, model, None, self.col)
            # extract filenames
            for reg in self.regexps:
                for (full, fname) in re.findall(reg, string, flags=re.UNICODE):
                    isLocal = not re.match(
                        "(https?|ftp)://", fname.lower(), flags=re.UNICODE)
                    if isLocal or includeRemote:
                        l.append(fname)
        return l

    def _expandClozes(self, string):
        ords = set(re.findall("{{c(\d+)::.+?}}", string))
        strings = []
        from anki.template.template import clozeReg

        def qrepl(m):
            if m.group(3):
                return "[%s]" % m.group(3)
            else:
                return "[...]"

        def arepl(m):
            return m.group(1)
        for ord in ords:
            s = re.sub(clozeReg % ord, qrepl, string)
            s = re.sub(clozeReg % ".+?", "\\1", s)
            strings.append(s)
        strings.append(re.sub(clozeReg % ".+?", arepl, string))
        return strings

    def transformNames(self, txt, func):
        for reg in self.regexps:
            txt = re.sub(reg, func, txt, flags=re.UNICODE)
        return txt

    def strip(self, txt):
        for reg in self.regexps:
            txt = re.sub(reg, "", txt, flags=re.UNICODE)
        return txt

    def escapeImages(self, string):
        # Feeding webkit unicode can result in it not finding images, so on
        # linux/osx we percent escape the image paths as utf8. On Windows the
        # problem is more complicated - if we percent-escape as utf8 it fixes
        # some images but breaks others. When filenames are normalized by
        # dropbox they become unreadable if we escape them.
        if isWin:
            return string

        def repl(match):
            tag = match.group(1)
            fname = match.group(2)
            if re.match("(https?|ftp)://", fname, flags=re.UNICODE):
                return tag
            return tag.replace(
                fname, urllib.quote(fname.encode("utf-8")))
        return re.sub(self.regexps[1], repl, string, flags=re.UNICODE)

    # Rebuilding DB
    ##########################################################################

    def check(self, local=None):
        "Return (missingFiles, unusedFiles)."
        mdir = self.dir()
        # generate card q/a and look through all references
        normrefs = {}

        def norm(s):
            if isinstance(s, unicode) and isMac:
                return unicodedata.normalize('NFD', s)
            return s
        for f in self.allMedia():
            normrefs[norm(f)] = True
        # loop through directory and find unused & missing media
        unused = []
        if local is None:
            files = os.listdir(mdir)
        else:
            files = local
        for file in files:
            if not local:
                path = os.path.join(mdir, file)
                if not os.path.isfile(path):
                    # ignore directories
                    continue
                if file.startswith("_"):
                    # leading _ says to ignore file
                    continue
            nfile = norm(file)
            if nfile not in normrefs:
                unused.append(file)
            else:
                del normrefs[nfile]
        nohave = normrefs.keys()
        return (nohave, unused)

    def allMedia(self):
        "Return a set of all referenced filenames."
        files = set()
        for mid, flds in self.col.db.execute("select mid, flds from notes"):
            for f in self.filesInStr(mid, flds):
                files.add(f)
        return files

    # Copying on import
    ##########################################################################

    def have(self, fname):
        return os.path.exists(os.path.join(self.dir(), fname))

    # Media syncing - changes and removal
    ##########################################################################

    def hasChanged(self):
        return self.db.scalar("select 1 from log limit 1")

    def removed(self):
        return self.db.list("select * from log where type = ?", MEDIA_REM)

    def syncRemove(self, fnames):
        # remove provided deletions
        for f in fnames:
            if os.path.exists(f):
                os.unlink(f)
            self.db.execute("delete from log where fname = ?", f)
            self.db.execute("delete from media where fname = ?", f)
        # and all locally-logged deletions, as server has acked them
        self.db.execute("delete from log where type = ?", MEDIA_REM)
        self.db.commit()

    # Media syncing - unbundling zip files from server
    ##########################################################################

    def syncAdd(self, zipData):
        "Extract zip data; true if finished."
        f = StringIO(zipData)
        z = zipfile.ZipFile(f, "r")
        finished = False
        meta = None
        media = []
        sizecnt = 0
        # get meta info first
        assert z.getinfo("_meta").file_size < 100000
        meta = json.loads(z.read("_meta"))
        nextUsn = int(z.read("_usn"))
        # then loop through all files
        for i in z.infolist():
            # check for zip bombs
            sizecnt += i.file_size
            assert sizecnt < 100 * 1024 * 1024
            if i.filename == "_meta" or i.filename == "_usn":
                # ignore previously-retrieved meta
                continue
            elif i.filename == "_finished":
                # last zip in set
                finished = True
            else:
                data = z.read(i)
                csum = checksum(data)
                name = meta[i.filename]
                # can we store the file on this system?
                if self.illegal(name):
                    continue
                # save file
                open(name, "wb").write(data)
                # update db
                media.append((name, csum, self._mtime(name)))
                # remove entries from local log
                self.db.execute("delete from log where fname = ?", name)
        # update media db and note new starting usn
        if media:
            self.db.executemany(
                "insert or replace into media values (?,?,?)", media)
        self.setUsn(nextUsn)  # commits
        # if we have finished adding, we need to record the new folder mtime
        # so that we don't trigger a needless scan
        if finished:
            self.syncMod()
        return finished

    def illegal(self, f):
        if isWin:
            for c in f:
                if c in "<>:\"/\\|?*^":
                    return True
        elif isMac:
            for c in f:
                if c in ":\\/":
                    return True

    # Media syncing - bundling zip files to send to server
    ##########################################################################
    # Because there's no standard filename encoding for zips, and because not
    # all zip clients support retrieving mtime, we store the files as ascii
    # and place a json file in the zip with the necessary information.

    def zipAdded(self):
        "Add files to a zip until over SYNC_ZIP_SIZE/COUNT. Return zip data."
        f = StringIO()
        z = zipfile.ZipFile(f, "w", compression=zipfile.ZIP_DEFLATED)
        sz = 0
        cnt = 0
        files = {}
        cur = self.db.execute(
            "select fname from log where type = ?", MEDIA_ADD)
        fnames = []
        # Clear the list from a possible last sync.
        self._problem_files = None
        self._all_media_files = None
        while 1:
            fname = cur.fetchone()
            if not fname:
                # add a flag so the server knows it can clean up
                z.writestr("_finished", "")
                break
            fname = fname[0]
            ufname = self._unnormalize(fname)
            fnames.append([fname])
            z.write(fname, str(cnt))
            files[str(cnt)] = ufname
            sz += os.path.getsize(fname)
            if sz > SYNC_ZIP_SIZE or cnt > SYNC_ZIP_COUNT:
                break
            cnt += 1
        z.writestr("_meta", json.dumps(files))
        z.close()
        return f.getvalue(), fnames

    def forgetAdded(self, fnames):
        if not fnames:
            return
        self.db.executemany("delete from log where fname = ?", fnames)
        self.db.commit()

    def _build_problem_file_dict(self):
        """
        Build a dict of all problem files in the collection.

        Go through the media in the collection, and for each file
        where the file name is different on a Mac, add the file name
        in the collection to a dict with the Mac file name as a key,
        so we can look it up later.

        (I see no way around going through the collection in one
        way or another, as there is basically no other way to see
        if any given normalized Unicode string has been changed
        from an unnormalized form. Think of re-arranged combining
        marks. See http://unicode.org/reports/tr15/,  for a cabinet
        of normalization horrors. (Version Unicode 6.2.0))
        """
        # Create an empty dict.
        print('debug, start building nfd dict')
        import time
        st = time.clock()
        self._problem_files = dict()
        print ('checking {} files'.format(len(self._all_media_files)))
        for fic in self._all_media_files:
            fic_n = unicodedata.normalize('NFD', fic)
            if fic_n != fic:
                print(u'{0} is not {1}'.format(fic_n, fic).encode('utf-8'))
                self._problem_files[fic_n] = fic
        print('debug, finished building nfd dict. Took {0}'.format(
              time.clock() - st))

    def _unnormalize(self, fn):
        """Return the file name we should send during sync."""
        # On Macs we have, or may have, a problem. The file names
        # we get in this function have been Unicode-normalized
        # (into NFD form). While we stay on a Mac, there is no
        # problem, but when we sync, have to fix this. The name of
        # the file on disk is not necessarily the same as what is
        # used in the collection, so try to find what is used in
        # the collection.
        # Two quick checks:
        if not isMac:
        # if False:  # Testing. Obiously.
            # No problem, we stored file name is the file name to
            # use.
            return fn
        if isinstance(fn, str):
            # No problem, the file name is the normalized file
            # name for sure.
            return fn
        # Still here, we have to look in the collection.
        if self._all_media_files is None:
            self._all_media_files = self.allMedia()
        # There are two rather quck ways:
        if fn in self._all_media_files:
            # The file is used decomposed in the
            # collection. Typical case if the user added it
            # through a file dialog (i think).
            return fn
        fn_nfc = unicodedata.normalize('NFC', fn)
        if fn_nfc in self._all_media_files:
            # The file is in the collection normalized. This may
            # happen quite often when the user typed in the file
            # name. It may also happen when some other bit of software
            # did't care about Unicode equivalence. (See my (ospalh's)
            # audio downloader add-on for an example of that..) (NFC
            # normalized strings are nicer. The text may also look
            # nicer. Never mind the dictionary meaning of equivalence,
            # it is often rendered differently.)
            return fn_nfc
        # Yikes! Looks like we really have to normalize the whole
        # collection.
        if self._problem_files is None:
            # N.B.: We check for None instead of "if not
            # problem_files:" so we don't rebuild an empty dict
            # over and over again.
            self._build_problem_file_dict()
        try:
            print(u'fn {0}'.format(fn).encode('utf-8'))
            print(
                u'pf[fn] {0}'.format(self._problem_files[fn]).encode('utf-8'))
            return self._problem_files[fn]
        except KeyError:
            return fn

    # Tracking changes (private)
    ##########################################################################

    def _initDB(self):
        self.db.executescript("""
create table media (fname text primary key, csum text, mod int);
create table meta (dirMod int, usn int); insert into meta values (0, 0);
create table log (fname text primary key, type int);
""")

    def _mtime(self, path):
        return int(os.stat(path).st_mtime)

    def _checksum(self, path):
        return checksum(open(path, "rb").read())

    def usn(self):
        return self.db.scalar("select usn from meta")

    def setUsn(self, usn):
        self.db.execute("update meta set usn = ?", usn)
        self.db.commit()

    def syncMod(self):
        self.db.execute("update meta set dirMod = ?", self._mtime(self.dir()))
        self.db.commit()

    def _changed(self):
        "Return dir mtime if it has changed since the last findChanges()"
        # doesn't track edits, but user can add or remove a file to update
        mod = self.db.scalar("select dirMod from meta")
        mtime = self._mtime(self.dir())
        if mod and mod == mtime:
            return False
        return mtime

    def findChanges(self):
        "Scan the media folder if it's changed, and note any changes."
        if self._changed():
            self._logChanges()

    def _logChanges(self):
        (added, removed) = self._changes()
        log = []
        media = []
        mediaRem = []
        for f in added:
            mt = self._mtime(f)
            media.append((f, self._checksum(f), mt))
            log.append((f, MEDIA_ADD))
        for f in removed:
            mediaRem.append((f,))
            log.append((f, MEDIA_REM))
        # update media db
        self.db.executemany("insert or replace into media values (?,?,?)",
                            media)
        if mediaRem:
            self.db.executemany("delete from media where fname = ?",
                                mediaRem)
        self.db.execute("update meta set dirMod = ?", self._mtime(self.dir()))
        # and logs
        self.db.executemany("insert or replace into log values (?,?)", log)
        self.db.commit()

    def _changes(self):
        self.cache = {}
        for (name, csum, mod) in self.db.execute(
                "select * from media"):
            self.cache[name] = [csum, mod, False]
        added = []
        removed = []
        # loop through on-disk files
        for f in os.listdir(self.dir()):
            # ignore folders and thumbs.db
            if os.path.isdir(f):
                continue
            if f.lower() == "thumbs.db":
                continue
            # and files with invalid chars
            bad = False
            for c in "\0", "/", "\\", ":":
                if c in f:
                    bad = True
                    break
            if bad:
                continue
            # empty files are invalid; clean them up and continue
            if not os.path.getsize(f):
                os.unlink(f)
                continue
            # newly added?
            if f not in self.cache:
                added.append(f)
            else:
                # modified since last time?
                if self._mtime(f) != self.cache[f][1]:
                    # and has different checksum?
                    if self._checksum(f) != self.cache[f][0]:
                        added.append(f)
                # mark as used
                self.cache[f][2] = True
        # look for any entries in the cache that no longer exist on disk
        for (k, v) in self.cache.items():
            if not v[2]:
                removed.append(k)
        return added, removed

    def sanityCheck(self):
        assert not self.db.scalar("select count() from log")
        cnt = self.db.scalar("select count() from media")
        return cnt

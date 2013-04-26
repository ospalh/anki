# Copyright: Damien Elmes <anki@ichi2.net>
# -*- coding: utf-8 -*-
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from PyQt4.QtCore import Qt, SIGNAL
from PyQt4.QtGui import QDialog

from anki.consts import DYN_DUE, DYN_OLDEST, DYN_RANDOM
from anki.lang import _
from aqt.utils import showInfo, showWarning
import aqt

RADIO_NEW = 1
RADIO_REV = 2
RADIO_FORGOT = 3
RADIO_AHEAD = 4
RADIO_RANDOM = 5
RADIO_PREVIEW = 6
RADIO_TAGS = 7


class CustomStudy(QDialog):
    def __init__(self, mw):
        QDialog.__init__(self, mw)
        self.mw = mw
        self.deck = self.mw.col.decks.current()
        self.form = f = aqt.forms.customstudy.Ui_Dialog()
        f.setupUi(self)
        self.setWindowModality(Qt.WindowModal)
        self.setupSignals()
        f.radio1.click()
        self.exec_()

    def setupSignals(self):
        f = self.form
        c = self.connect
        s = SIGNAL("clicked()")
        c(f.radio1, s, lambda: self.onRadioChange(1))
        c(f.radio2, s, lambda: self.onRadioChange(2))
        c(f.radio3, s, lambda: self.onRadioChange(3))
        c(f.radio4, s, lambda: self.onRadioChange(4))
        c(f.radio5, s, lambda: self.onRadioChange(5))
        c(f.radio6, s, lambda: self.onRadioChange(6))
        c(f.radio7, s, lambda: self.onRadioChange(7))

    def onRadioChange(self, idx):
        f = self.form
        sp = f.spin
        smin = 1
        smax = 9999
        sval = 1
        post = _("cards")
        tit = ""
        spShow = True

        def plus(num):
            if num == 1000:
                num = "1000+"
            return "<b>" + str(num) + "</b>"
        if idx == RADIO_NEW:
            new = self.mw.col.sched.totalNewForCurrentDeck()
            self.deck['newToday']
            tit = _("New cards in deck: %s") % plus(new)
            pre = _("Increase today's new card limit by")
            sval = min(new, self.deck.get('extendNew', 10))
            smax = new
        elif idx == RADIO_REV:
            rev = self.mw.col.sched.totalRevForCurrentDeck()
            tit = _("Reviews due in deck: %s") % plus(rev)
            pre = _("Increase today's review limit by")
            sval = min(rev, self.deck.get('extendRev', 10))
        elif idx == RADIO_FORGOT:
            pre = _("Review cards forgotten in last")
            post = _("days")
            smax = 30
        elif idx == RADIO_AHEAD:
            pre = _("Review ahead by")
            post = _("days")
        elif idx == RADIO_RANDOM:
            pre = _("Select")
            post = _("cards randomly from the deck")
            sval = 100
        elif idx == RADIO_PREVIEW:
            pre = _("Preview new cards added in the last")
            post = _("days")
            sval = 1
        elif idx == RADIO_TAGS:
            tit = _("Press OK to choose tags.")
            sval = 100
            spShow = False
            pre = post = ""
        sp.setVisible(spShow)
        f.title.setText(tit)
        f.title.setVisible(not not tit)
        f.spin.setMinimum(smin)
        f.spin.setMaximum(smax)
        f.spin.setValue(sval)
        f.preSpin.setText(pre)
        f.postSpin.setText(post)
        self.radioIdx = idx

    def accept(self):
        f = self.form
        i = self.radioIdx
        spin = f.spin.value()
        if i == RADIO_NEW:
            self.deck['extendNew'] = spin
            self.mw.col.decks.save(self.deck)
            self.mw.col.sched.extendLimits(spin, 0)
            self.mw.reset()
            return QDialog.accept(self)
        elif i == RADIO_REV:
            self.deck['extendRev'] = spin
            self.mw.col.decks.save(self.deck)
            self.mw.col.sched.extendLimits(0, spin)
            self.mw.reset()
            return QDialog.accept(self)
        elif i == RADIO_TAGS:
            tags = self._getTags()
            if not tags:
                return
        # the rest create a filtered deck
        cur = self.mw.col.decks.byName(_("Custom Study Session"))
        if cur:
            if not cur['dyn']:
                showInfo("Please rename the existing Custom Study deck first.")
                return QDialog.accept(self)
            else:
                # safe to empty
                self.mw.col.sched.emptyDyn(cur['id'])
                # reuse; don't delete as it may have children
                dyn = cur
                self.mw.col.decks.select(cur['id'])
        else:
            did = self.mw.col.decks.newDyn(_("Custom Study Session"))
            dyn = self.mw.col.decks.get(did)
        # and then set various options
        if i == RADIO_FORGOT:
            dyn['delays'] = [1]
            dyn['terms'][0] = ['rated:%d:1' % spin, 9999, DYN_RANDOM]
            dyn['resched'] = False
        elif i == RADIO_AHEAD:
            dyn['delays'] = None
            dyn['terms'][0] = ['prop:due<=%d' % spin, 9999, DYN_DUE]
            dyn['resched'] = True
        elif i == RADIO_RANDOM:
            dyn['delays'] = None
            dyn['terms'][0] = ['', spin, DYN_RANDOM]
            dyn['resched'] = True
        elif i == RADIO_PREVIEW:
            dyn['delays'] = None
            dyn['terms'][0] = ['is:new added:%s' % spin, 9999, DYN_OLDEST]
            dyn['resched'] = False
        elif i == RADIO_TAGS:
            dyn['delays'] = None
            dyn['terms'][0] = ["(is:new or is:due) " + tags, 9999, DYN_RANDOM]
            dyn['resched'] = True
        # add deck limit
        dyn['terms'][0][0] = "deck:\"%s\" %s " % (
            self.deck['name'], dyn['terms'][0][0])
        # generate cards
        if not self.mw.col.sched.rebuildDyn():
            return showWarning(
                _("No cards matched the criteria you provided."))
        self.mw.moveToState("overview")
        QDialog.accept(self)

    def _getTags(self):
        from aqt.taglimit import TagLimit
        t = TagLimit(self.mw, self)
        return t.tags

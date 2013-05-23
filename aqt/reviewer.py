# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import division
import HTMLParser
import difflib
import re

from PyQt4.QtCore import Qt, SIGNAL
from PyQt4.QtGui import QCursor, QKeySequence, QMenu, QShortcut

from anki.hooks import addHook, runFilter, runHook
from anki.lang import _, ngettext
from anki.sound import playFromText, clearAudioQueue, play
from anki.utils import stripHTML, isMac, json
from aqt.sound import getAudio
from aqt.utils import askUser, mungeQA, getBase, openLink, tooltip
import aqt


class Reviewer(object):
    "Manage reviews.  Maintains a separate state."

    def __init__(self, mw):
        self.mw = mw
        self.web = mw.web
        self.card = None
        self.cardQueue = []
        self.hadCardQueue = False
        self._answeredIds = []
        self._recordedAudio = None
        self.typeCorrect = None  # web init happens before this is set
        self.state = None
        self.bottom = aqt.toolbar.BottomBar(mw, mw.bottomWeb)
        # qshortcut so we don't autorepeat
        self.delShortcut = QShortcut(QKeySequence("Delete"), self.mw)
        self.delShortcut.setAutoRepeat(False)
        self.mw.connect(self.delShortcut, SIGNAL("activated()"), self.onDelete)
        addHook("leech", self.onLeech)
        addHook(
            "filterTypedAnswer",
            lambda r, g, co, ca: self.correct(
                ret=r, given=g, correct=co, card=ca, showBad=False))

    def show(self):
        self.mw.col.reset()
        self.mw.keyHandler = self._keyHandler
        self.web.setLinkHandler(self._linkHandler)
        self.web.setKeyHandler(self._catchEsc)
        if isMac:
            self.bottom.web.setFixedHeight(46)
        else:
            self.bottom.web.setFixedHeight(52 + self.mw.fontHeightDelta * 4)
        self.bottom.web.setLinkHandler(self._linkHandler)
        self._reps = None
        self.nextCard()

    def lastCard(self):
        if self._answeredIds:
            if not self.card or self._answeredIds[-1] != self.card.id:
                try:
                    return self.mw.col.getCard(self._answeredIds[-1])
                except TypeError:
                    # id was deleted
                    return

    def cleanup(self):
        runHook("reviewCleanup")

    # Fetching a card
    ##########################################################################

    def nextCard(self):
        elapsed = self.mw.col.timeboxReached()
        if elapsed:
            part1 = ngettext("%d card studied in", "%d cards studied in",
                             elapsed[1]) % elapsed[1]
            part2 = ngettext("%s minute.", "%s minutes.",
                             elapsed[0] / 60) % (elapsed[0] / 60)
            tooltip("%s %s" % (part1, part2), period=5000)
            self.mw.col.startTimebox()
        if self.cardQueue:
            # undone/edited cards to show
            c = self.cardQueue.pop()
            c.startTimer()
            self.hadCardQueue = True
        else:
            if self.hadCardQueue:
                # the undone/edited cards may be sitting in the regular queue;
                # need to reset
                self.mw.col.reset()
                self.hadCardQueue = False
            c = self.mw.col.sched.getCard()
        self.card = c
        clearAudioQueue()
        if not c:
            # self.mw.moveToState("overview")
            # Do what going to overview and then to deck browser did
            # by hand, skipping the display of the overview.
            self.mw.col.reset()
            self.mw.moveToState("deckBrowser")
            return
        if self._reps is None or self._reps % 100 == 0:
            # we recycle the webview periodically so webkit can free memory
            self._initWeb()
        else:
            self._showQuestion()

    # Audio
    ##########################################################################

    def replayAudio(self):
        clearAudioQueue()
        c = self.card
        if self.state == "question":
            playFromText(c.q())
        elif self.state == "answer":
            txt = ""
            if self._replayq(c):
                txt = c.q()
            txt += c.a()
            playFromText(txt)

    # Initializing the webview
    ##########################################################################

    _revHtml = """
<img src="qrc:/icons/rating.png" class=marked>
<div id=qa></div>
<script>
var ankiPlatform = "desktop";
var typeans;
function _updateQA (q, answerMode, klass) {
    $("#qa").html(q);
    typeans = document.getElementById("typeans");
    if (typeans) {
        typeans.focus();
    }
    if (answerMode) {
        window.location = "#answer";
    } else {
        window.scrollTo(0, 0);
    }
    if (klass) {
        document.body.className = klass;
    }
    // don't allow drags of images, which cause them to be deleted
    $("img").attr("draggable", false);
};

function _toggleStar (show) {
    if (show) {
        $(".marked").show();
    } else {
        $(".marked").hide();
    }
}

function _getTypedText () {
    if (typeans) {
        py.link("typeans:"+typeans.value);
    }
};
function _typeAnsPress() {
    if (window.event.keyCode === 13) {
        py.link("ansHack");
    }
}
</script>
"""

    def _initWeb(self):
        self._reps = 0
        self._bottomReady = False
        base = getBase(self.mw.col)
        # main window
        self.web.stdHtml(self._revHtml, self._styles(),
                         loadCB=lambda x: self._showQuestion(), head=base)
        # show answer / ease buttons
        self.bottom.web.show()
        self.bottom.web.stdHtml(
            self._bottomHTML(),
            self.bottom._css + self._bottomCSS,
            loadCB=lambda x: self._showAnswerButton())

    # Showing the question
    ##########################################################################

    def _mungeQA(self, buf):
        return self.mw.col.media.escapeImages(
            self.typeAnsFilter(mungeQA(buf)))

    def _showQuestion(self):
        self._reps += 1
        self.state = "question"
        self.typedAnswer = None
        c = self.card
        # grab the question and play audio
        if c.isEmpty():
            q = _("""\
The front of this card is empty. Please run Tools>Maintenance>Empty Cards.""")
        else:
            q = c.q()
        if self.autoplay(c):
            playFromText(q)
        # render & update bottom
        q = self._mungeQA(q)
        klass = "card card%d" % (c.ord + 1)
        self.web.eval("_updateQA(%s, false, '%s');" % (json.dumps(q), klass))
        self._toggleStar()
        if self._bottomReady:
            self._showAnswerButton()
        # if we have a type answer field, focus main web
        if self.typeCorrect:
            self.mw.web.setFocus()
        # user hook
        runHook('showQuestion')

    def autoplay(self, card):
        return self.mw.col.decks.confForDid(
            card.odid or card.did)['autoplay']

    def _replayq(self, card):
        return self.mw.col.decks.confForDid(
            self.card.odid or self.card.did).get('replayq', True)

    def _toggleStar(self):
        self.web.eval("_toggleStar(%s);" % json.dumps(
            self.card.note().hasTag("marked")))

    # Showing the answer
    ##########################################################################

    def _showAnswer(self):
        if self.mw.state != "review":
            # showing resetRequired screen; ignore space
            return
        self.state = "answer"
        c = self.card
        a = c.a()
        # play audio?
        if self.autoplay(c):
            playFromText(a)
        # render and update bottom
        a = self._mungeQA(a)
        self.web.eval("_updateQA(%s, true);" % json.dumps(a))
        self._showEaseButtons()
        # user hook
        runHook('showAnswer')

    # Answering a card
    ############################################################

    def _answerCard(self, ease):
        "Reschedule card and show next."
        if self.mw.state != "review":
            # showing resetRequired screen; ignore key
            return
        if self.state != "answer":
            return
        if self.mw.col.sched.answerButtons(self.card) < ease:
            return
        self.mw.col.sched.answerCard(self.card, ease)
        self._answeredIds.append(self.card.id)
        self.mw.autosave()
        self.nextCard()

    # Handlers
    ############################################################

    def _catchEsc(self, evt):
        if evt.key() == Qt.Key_Escape:
            self.web.eval("$('#typeans').blur();")
            return True

    def _showAnswerHack(self):
        # on <qt4.8, calling _showAnswer() directly fails to show images on
        # the answer side. But if we trigger it via the bottom web's python
        # link, it inexplicably works.
        self.bottom.web.eval("py.link('ans');")

    def _keyHandler(self, evt):
        key = unicode(evt.text())
        if key == "e":
            self.mw.onEditCurrent()
        elif (key == " " or evt.key() in (Qt.Key_Return, Qt.Key_Enter)):
            if self.state == "question":
                self._showAnswerHack()
            elif self.state == "answer":
                self._answerCard(self._defaultEase())
        elif key == "r" or evt.key() == Qt.Key_F5:
            self.replayAudio()
        elif key == "*":
            self.onMark()
        elif key == "-":
            self.onBuryNote()
        elif key == "!":
            self.onSuspend()
        elif key == "@":
            self.onSuspendCard()
        elif key == "V":
            self.onRecordVoice()
        elif key == "o":
            self.onOptions()
        elif key in ("1", "2", "3", "4"):
            self._answerCard(int(key))
        elif key == "v":
            self.onReplayRecorded()

    def _linkHandler(self, url):
        if url == "ans":
            self._showAnswer()
        elif url == "ansHack":
            self.mw.progress.timer(100, self._showAnswerHack, False)
        elif url.startswith("ease"):
            self._answerCard(int(url[4:]))
        elif url == "edit":
            self.mw.onEditCurrent()
        elif url == "more":
            self.showContextMenu()
        elif url.startswith("typeans:"):
            (cmd, arg) = url.split(":", 1)
            self.typedAnswer = arg
        else:
            openLink(url)

    # CSS
    ##########################################################################

    _css = """
#answer {
  background-color:#ccc;
  margin: 1em;
}
body {
  margin: 0.2em;
  margin-top: 0.5em;
}
img {
  max-width: 100%;
}
.marked {
  position:absolute;
  right: 7px;
  top: 7px;
  display: none;
}

.typeGood { background: #0f0; }
.typeBad { background: #f00; }
.typeMissed { background: #ccc; }
"""

    def _styles(self):
        return self._css

    # Type in the answer
    ##########################################################################

    typeAnsPat = "\[\[type:(.+?)\]\]"

    def typeAnsFilter(self, buf):
        if self.state == "question":
            return self.typeAnsQuestionFilter(buf)
        else:
            return self.typeAnsAnswerFilter(buf)

    def typeAnsQuestionFilter(self, buf):
        self.typeCorrect = None
        clozeIdx = None
        m = re.search(self.typeAnsPat, buf)
        if not m:
            return buf
        fld = m.group(1)
        # if it's a cloze, extract data
        if fld.startswith("cloze:"):
            # get field and cloze position
            clozeIdx = self.card.ord + 1
            fld = fld.split(":")[1]
        # loop through fields for a match
        for f in self.card.model()['flds']:
            if f['name'] == fld:
                self.typeCorrect = self.card.note()[f['name']]
                if clozeIdx:
                    # narrow to cloze
                    self.typeCorrect = self._contentForCloze(
                        self.typeCorrect, clozeIdx)
                self.typeFont = f['font']
                self.typeSize = f['size']
                break
        if not self.typeCorrect:
            if self.typeCorrect is None:
                if clozeIdx:
                    warn = _("""\
Please run Tools>Empty Cards""")
                else:
                    warn = _("Type answer: unknown field %s") % fld
                return re.sub(self.typeAnsPat, warn, buf)
            else:
                # empty field, remove type answer pattern
                return re.sub(self.typeAnsPat, "", buf)
        return re.sub(self.typeAnsPat, """<input type=text id=typeans \
onkeypress="_typeAnsPress();">""", buf)

    def typeAnsAnswerFilter(self, buf):
        # tell webview to call us back with the input content
        self.web.eval("_getTypedText();")
        if not self.typeCorrect or not self.typedAnswer:
            return re.sub(self.typeAnsPat, "", buf)
        # munge correct value
        parser = HTMLParser.HTMLParser()
        cor = stripHTML(self.mw.col.media.strip(self.typeCorrect))
        cor = parser.unescape(cor)
        given = self.typedAnswer
        # compare with typed answer
        res = runFilter("filterTypedAnswer", u'', given, cor, self.card)
        # and update the type answer area

        def repl(match):
            # can't pass a string in directly, and can't use re.escape as it
            # escapes too much
            return u""" <span  id="corrected">{0}</span>""".format(res)
        return re.sub(self.typeAnsPat, repl, buf)

    def _contentForCloze(self, txt, idx):
        matches = re.findall("\{\{c%s::(.+?)\}\}" % idx, txt)
        if not matches:
            return None

        def noHint(txt):
            if "::" in txt:
                return txt.split("::")[0]
            return txt
        matches = [noHint(m_txt) for m_txt in matches]
        if len(matches) > 1:
            txt = ", ".join(matches)
        else:
            txt = matches[0]
        return txt

    def tokenizeComparison(self, given, correct):
        s = difflib.SequenceMatcher(None, given, correct, autojunk=False)
        givenElems = []
        correctElems = []
        givenPoint = 0
        correctPoint = 0
        offby = 0

        def logBad(old, new, str, array):
            if old != new:
                array.append((False, str[old:new]))

        def logGood(start, cnt, str, array):
            if cnt:
                array.append((True, str[start:start+cnt]))
        for x, y, cnt in s.get_matching_blocks():
            # if anything was missed in correct, pad given
            if cnt and y-offby > x:
                givenElems.append((False, "-"*(y-x-offby)))
                offby = y-x
            # log any proceeding bad elems
            logBad(givenPoint, x, given, givenElems)
            logBad(correctPoint, y, correct, correctElems)
            givenPoint = x+cnt
            correctPoint = y+cnt
            # log the match
            logGood(x, cnt, given, givenElems)
            logGood(y, cnt, correct, correctElems)
        return givenElems, correctElems

    def correct(self, ret, given, correct, card=None, showBad=True):
        "Diff-corrects the typed-in answer."
        if ret:
            # Someone else has already done some correcting.
            return ret
        givenElems, correctElems = self.tokenizeComparison(given, correct)

        def good(s):
            return "<span class=typeGood>"+s+"</span>"

        def bad(s):
            return "<span class=typeBad>"+s+"</span>"

        def missed(s):
            return "<span class=typeMissed>"+s+"</span>"
        if given == correct:
            res = "<span class=allgood>" + good(given) + "</span>"
        else:
            ge = u""
            for ok, txt in givenElems:
                if ok:
                    ge += good(txt)
                else:
                    ge += bad(txt)
            ce = u''
            for ok, txt in correctElems:
                if ok:
                    ce += good(txt)
                else:
                    ce += missed(txt)
            res = u"""
<span class=given>{ge}</span><span class=arrow>→</span>\
<span class=correct>{ce}</span>
""".format(ge=ge, ce=ce)
        res = "<span id=typeans>" + res + "</span>"
        return res

    # Bottom bar
    ##########################################################################

    _bottomCSS = """
body {
background: -webkit-gradient(linear, left top, left bottom,
from(#fff), to(#ddd));
border-bottom: 0;
border-top: 1px solid #aaa;
margin: 0;
padding: 0px;
padding-left: 5px; padding-right: 5px;
}
button {
min-width: 60px; white-space: nowrap;
}
.hitem { margin-top: 2px; }
.stat { padding-top: 5px; }
.stat2 { padding-top: 3px; font-weight: normal; }
.stattxt { padding-left: 5px; padding-right: 5px; white-space: nowrap; }
.nobold { font-weight: normal; display: inline-block; padding-top: 4px; }
.spacer { height: 18px; }
.spacer2 { height: 16px; }

button.ease_again {color: #c35617;}
button.ease_easy {color: #070;}
button#defease {font-weight: bold;}
#ansbut {min-width: 260px;}
"""

    def _bottomHTML(self):
        return """
<table width=100%% cellspacing=0 cellpadding=0>
<tr>
<td align=left width=50 valign=top class=stat>
<br>
<button title="%(editkey)s" onclick="py.link('edit');">%(edit)s</button></td>
<td align=center valign=top id=middle>
</td>
<td width=50 align=right valign=top class=stat><span id=time class=stattxt>
</span><br>
<button onclick="py.link('more');">%(more)s &#9662;</button>
</td>
</tr>
</table>
<script>
var time = %(time)d;
var maxTime = 0;
$(function () {
$("#ansbut").focus();
updateTime();
setInterval(function () { time += 1; updateTime() }, 1000);
});

var updateTime = function () {
    if (!maxTime) {
        $("#time").text("");
        return;
    }
    time = Math.min(maxTime, time);
    var m = Math.floor(time / 60);
    var s = time %% 60;
    if (s < 10) {
        s = "0" + s;
    }
    var e = $("#time");
    if (maxTime == time) {
        e.html("<font color=red>" + m + ":" + s + "</font>");
    } else {
        e.text(m + ":" + s);
    }
}

function showQuestion(txt, maxTime_) {
  // much faster than jquery's .html()
  $("#middle")[0].innerHTML = txt;
  $("#ansbut").focus();
  time = 0;
  maxTime = maxTime_;
}

function showAnswer(txt) {
  $("#middle")[0].innerHTML = txt;
  $("#defease").focus();
}

</script>
""" % dict(rem=self._remaining(), edit=_("Edit"),
           editkey=_("Shortcut key: %s") % "E",
           more=_("More"), time=self.card.timeTaken() // 1000)

    def _showAnswerButton(self):
        self._bottomReady = True
        if not self.typeCorrect:
            self.bottom.web.setFocus()
        middle = '''
<span class=stattxt>%s</span><br>
<button title="%s" id=ansbut onclick='py.link(\"ans\");'>%s</button>''' % (
            self._remaining(), _(
                "Shortcut key: %s") % _("Space"), _("Show Answer"))
        # wrap it in a table so it has the same top margin as the ease buttons
        middle = """\
<table cellpadding=0><tr><td class=stat2 align=center>%s</td></tr></table>""" \
            % middle
        if self.card.shouldShowTimer():
            maxTime = self.card.timeLimit() / 1000
        else:
            maxTime = 0
        self.bottom.web.eval("showQuestion(%s,%d);" % (
            json.dumps(middle), maxTime))

    def _showEaseButtons(self):
        self.bottom.web.setFocus()
        middle = self._answerButtons()
        self.bottom.web.eval("showAnswer(%s);" % json.dumps(middle))

    def _remaining(self):
        if not self.mw.col.conf['dueCounts']:
            return ""
        if self.hadCardQueue:
            # if it's come from the undo queue, don't count it separately
            counts = list(self.mw.col.sched.counts())
        else:
            counts = list(self.mw.col.sched.counts(self.card))
        idx = self.mw.col.sched.countIdx(self.card)
        counts[idx] = "<u>%s</u>" % (counts[idx])
        space = " + "
        ctxt = '<font color="#000099">%s</font>' % counts[0]
        ctxt += space + '<font color="#C35617">%s</font>' % counts[1]
        ctxt += space + '<font color="#007700">%s</font>' % counts[2]
        return ctxt

    def _defaultEase(self):
        if self.mw.col.sched.answerButtons(self.card) == 4:
            return 3
        else:
            return 2

    def _answerButtonList(self):
        l = ((1, _("Again"), 'again'),)
        cnt = self.mw.col.sched.answerButtons(self.card)
        if cnt == 2:
            return l + ((2, _("Good"), 'good'),)
        elif cnt == 3:
            return l + ((2, _("Good"), 'good'), (3, _("Easy"), 'easy'))
        else:
            return l + ((2, _("Hard"), 'hard'), (3, _("Good"), 'good'),
                        (4, _("Easy"), 'easy'))

    def _answerButtons(self):
        # times = []
        default = self._defaultEase()

        def but(i, label, e_label):
            if i == default:
                extra = "id=defease"
            else:
                extra = ""
            due = self._buttonTime(i)
            return u'''
<td align=center>{d}<button {x} class="ease_{et}" title="{l}" \
onclick='py.link("ease{e}");'>{t}</button></td>'''.format(
                d=due, x=extra, l=_("Shortcut key: %s") % i, e=i, t=label,
                et=e_label)
        buf = "<center><table cellpading=0 cellspacing=0><tr>"
        for ease, label, e_label in self._answerButtonList():
            buf += but(ease, label, e_label)
        buf += "</tr></table>"
        script = """
<script>$(function () { $("#defease").focus(); });</script>"""
        return buf + script

    def _buttonTime(self, i):
        if not self.mw.col.conf['estTimes']:
            return "<div class=spacer></div>"
        txt = self.mw.col.sched.nextIvlStr(self.card, i, True) or "&nbsp;"
        return '<span class=nobold>%s</span><br>' % txt

    # Leeches
    ##########################################################################

    def onLeech(self, card):
        # for now
        s = _("Card was a leech.")
        if card.queue < 0:
            s += " " + _("It has been suspended.")
        tooltip(s)

    # Context menu
    ##########################################################################

    # note the shortcuts listed here also need to be defined above
    def showContextMenu(self):
        opts = [
            [_("Mark Note"), "*", self.onMark],
            [_("Bury Note"), "-", self.onBuryNote],
            [_("Suspend Card"), "@", self.onSuspendCard],
            [_("Suspend Note"), "!", self.onSuspend],
            [_("Delete Note"), "Delete", self.onDelete],
            [_("Options"), "O", self.onOptions],
            None,
            [_("Replay Audio"), "R", self.replayAudio],
            [_("Record Own Voice"), "Shift+V", self.onRecordVoice],
            [_("Replay Own Voice"), "V", self.onReplayRecorded],
        ]
        m = QMenu(self.mw)
        for row in opts:
            if not row:
                m.addSeparator()
                continue
            label, scut, func = row
            a = m.addAction(label)
            a.setShortcut(QKeySequence(scut))
            a.connect(a, SIGNAL("triggered()"), func)
        m.exec_(QCursor.pos())

    def onOptions(self):
        self.mw.onDeckConf(self.mw.col.decks.get(
            self.card.odid or self.card.did))

    def onMark(self):
        f = self.card.note()
        if f.hasTag("marked"):
            f.delTag("marked")
        else:
            f.addTag("marked")
        f.flush()
        self._toggleStar()

    def onSuspend(self):
        self.mw.checkpoint(_("Suspend"))
        self.mw.col.sched.suspendCards(
            [c.id for c in self.card.note().cards()])
        tooltip(_("Note suspended."))
        self.mw.reset()

    def onSuspendCard(self):
        self.mw.checkpoint(_("Suspend"))
        self.mw.col.sched.suspendCards([self.card.id])
        tooltip(_("Card suspended."))
        self.mw.reset()

    def onDelete(self):
        # need to check state because the shortcut is global to the main
        # window
        if self.mw.state != "review" or not self.card:
            return
        if not askUser('Delete note?', defaultno=True):
            # Always asks before deleting notes here.
            return
        self.mw.checkpoint(_("Delete"))
        cnt = len(self.card.note().cards())
        self.mw.col.remNotes([self.card.note().id])
        self.mw.reset()
        tooltip(ngettext(
            "Note and its %d card deleted.",
            "Note and its %d cards deleted.",
            cnt) % cnt)

    def onBuryNote(self):
        self.mw.checkpoint(_("Bury"))
        self.mw.col.sched.buryNote(self.card.nid)
        self.mw.reset()
        tooltip(_("Note buried."))

    def onRecordVoice(self):
        self._recordedAudio = getAudio(self.mw, encode=False)
        self.onReplayRecorded()

    def onReplayRecorded(self):
        if not self._recordedAudio:
            return tooltip(_("You haven't recorded your voice yet."))
        clearAudioQueue()
        play(self._recordedAudio)

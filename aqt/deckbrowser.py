# Copyright: Damien Elmes <anki@ichi2.net>
# -*- coding: utf-8 -*-
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from PyQt4.QtCore import QPoint, Qt, SIGNAL
from PyQt4.QtGui import QCursor, QMenu

from anki.errors import DeckRenameError
from anki.lang import _, ngettext
from anki.sound import clearAudioQueue
from anki.utils import ids2str, isMac, fmtTimeSpan
from aqt.utils import askUser, getOnlyText, openLink, shortcut, showWarning
import anki.js
import aqt


class DeckBrowser(object):

    def __init__(self, mw):
        self.mw = mw
        self.web = mw.web
        self.bottom = aqt.toolbar.BottomBar(mw, mw.bottomWeb)

    def show(self):
        clearAudioQueue()
        self.web.setLinkHandler(self._linkHandler)
        self.web.setKeyHandler(None)
        self.mw.keyHandler = self._keyHandler
        self._renderPage()

    def refresh(self):
        self._renderPage()

    # Event handlers
    ##########################################################################

    def _linkHandler(self, url):
        if ":" in url:
            (cmd, arg) = url.split(":")
        else:
            cmd = url
        if cmd == "open":
            self._selDeck(arg)
        elif cmd == "opts":
            self._showOptions(arg)
        elif cmd == "shared":
            self._onShared()
        elif cmd == "import":
            self.mw.onImport()
        elif cmd == "create":
            deck = getOnlyText(_("Name for deck:"))
            if deck:
                self.mw.col.decks.id(deck)
                self.refresh()
        elif cmd == "drag":
            draggedDeckDid, ontoDeckDid = arg.split(',')
            self._dragDeckOnto(draggedDeckDid, ontoDeckDid)
        elif cmd == "collapse":
            self._collapse(arg)

    def _keyHandler(self, evt):
        if evt.key() == Qt.Key_Up:
            self._previousDeck()
        if evt.key() == Qt.Key_Down:
            self._nextDeck()
        if evt.key() == Qt.Key_Return or evt.key() == Qt.Key_Enter:
            self._selDeck()
        key_text = unicode(evt.text())
        if key_text == "f":
            self.mw.onCram()

    def _selDeck(self, did=None):
        if not did:
            did = self.mw.col.conf['curDeck']
        self.mw.col.decks.select(did)
        # New, no overview
        self.mw.col.reset()
        self.mw.col.startTimebox()
        self.mw.moveToState("review")
        ## old, overview
        # self.mw.onOverview()

    def _previousDeck(self):
        try:
            previous_did = int(self.web.eval("previousDid()"))
        except (ValueError, TypeError):
            # Most likely reason: first deck. Then prevousDid() returns
            # u''.
            return
        current_did = self.mw.col.conf['curDeck']
        self.mw.col.decks.select(previous_did)
        self.web.eval(
            'moveCurrentClass({0}, {1})'.format(current_did, previous_did))

    def _nextDeck(self):
        try:
            next_did = int(self.web.eval("nextDid()"))
        except (ValueError, TypeError):
            # last deck
            return
        current_did = self.mw.col.conf['curDeck']
        self.mw.col.decks.select(next_did)
        self.web.eval(
            "moveCurrentClass({0}, {1})".format(current_did, next_did))

    # HTML generation
    ##########################################################################

    _dragIndicatorBorderWidth = "1px"

    _css = """
a.deck { color: #000; text-decoration: none; min-width: 5em;
         display:inline-block; }
a.deck:hover { text-decoration: underline; }
tr.deck td { border-bottom: %(width)s solid #e7e7e7; }
tr.top-level-drag-row td { border-bottom: %(width)s solid transparent; }
td { white-space: nowrap; }
tr.drag-hover td { border-bottom: %(width)s solid #aaa; }
body { margin: 1em; -webkit-user-select: none; }
.current { background-color: #e7e7e7; }
.decktd { min-width: 15em; }
.count { width: 6em; text-align: right; }
.collapse { color: #000; text-decoration:none; display:inline-block;
    width: 1em; }
.filtered { color: #00a !important; }
%(qtip)s
 """ % dict(width=_dragIndicatorBorderWidth, qtip=anki.js.qtip_css)

    _body = """
<center>
<table cellspacing=0 cellpading=3>
%(tree)s
</table>

<br>
%(stats)s
</center>
<script>
    $( init );

    function init() {

        $("tr.deck").draggable({
            scroll: false,

            // can't use "helper: 'clone'" because of a bug in jQuery 1.5
            helper: function (event) {
                return $(this).clone(false);
            },
            delay: 200,
            opacity: 0.7
        });
        $("tr.deck").droppable({
            drop: handleDropEvent,
            hoverClass: 'drag-hover',
        });
        $("tr.top-level-drag-row").droppable({
            drop: handleDropEvent,
            hoverClass: 'drag-hover',
        });
    }

    function moveCurrentClass(from_id, to_id) {
        $(document.getElementById(to_id)).addClass("current");
        $(document.getElementById(from_id)).removeClass( "current");
    }

    function previousDid() {
          return document.getElementsByClassName(
              "current")[0].previousSibling.id;
    }

    function nextDid() {
         return document.getElementsByClassName("current")[0].nextSibling.id;
    }

    function handleDropEvent(event, ui) {
        var draggedDeckId = ui.draggable.attr('id');
        var ontoDeckId = $(this).attr('id');

        py.link("drag:" + draggedDeckId + "," + ontoDeckId);
    }

    function add_qtips(){
        $('td.duelrn').qtip({
            tip:true,
            position:{
                target: 'mouse',
                my: 'right center',
                at: 'top left',
                adjust: {x: -10, y: -10}
            },
            content: {
                text: function() {
                    var dls = $(this).attr('title').split(" ");
                    return   '<font color=#007700>' + dls[0]
                             + '</font> + <font color=#990000> '
                             + dls[1] + '</font>';
                }
            },
            show: 'mouseover',
            hide: 'mouseout'

        })
    }
</script>
"""

    def _renderPage(self, reuse=False):
        css = self.mw.sharedCSS + self._css
        if not reuse:
            self._dueTree = self.mw.col.sched.deckDueTree()
        tree = self._renderDeckTree(self._dueTree)
        stats = self._renderStats()
        op = self._oldPos()
        self.web.stdHtml(self._body % dict(tree=tree, stats=stats), css=css,
                         js=anki.js.jquery + anki.js.ui + anki.js.qtip_js,
                         loadCB=lambda ok:
                             self.web.page().mainFrame().setScrollPosition(op))
        self.web.key = "deckBrowser"
        self.web.eval("add_qtips()")
        self._drawButtons()

    def _oldPos(self):
        if self.web.key == "deckBrowser":
            return self.web.page().mainFrame().scrollPosition()
        else:
            return QPoint(0, 0)

    def _renderStats(self):
        cards, thetime = self.mw.col.db.first("""
select count(), sum(time)/1000 from revlog
where id > ?""", (self.mw.col.sched.dayCutoff - 86400) * 1000)
        cards = cards or 0
        thetime = thetime or 0
        msgp1 = ngettext(
            "<!--studied-->%d card", "<!--studied-->%d cards", cards) % cards
        buf = _("Studied %(a)s in %(b)s today.") % \
            dict(a=msgp1, b=fmtTimeSpan(thetime, unit=1))
        return buf

    def _renderDeckTree(self, nodes, depth=0):
        if not nodes:
            return ""
        if depth == 0:
            buf = """
<tr><th colspan=5 align=left>%s</th><th class=count>%s</th>
<th class=count>%s</th><th class=count></th></tr>""" % (
                _("Deck"), _("Due"), _("New"))
            buf += self._topLevelDragRow()
        else:
            buf = ""
        for node in nodes:
            buf += self._deckRow(node, depth, len(nodes))
        if depth == 0:
            buf += self._topLevelDragRow()
        return buf

    def _deckRow(self, node, depth, cnt):
        name, did, due, lrn, new, children = node
        deck = self.mw.col.decks.get(did)
        if did == 1 and cnt > 1 and not children:
            # if the default deck is empty, hide it
            if not self.mw.col.db.scalar("select 1 from cards where did = 1"):
                return ""
        # parent toggled for collapsing
        for parent in self.mw.col.decks.parents(did):
            if parent['collapsed']:
                buff = ""
                return buff
        prefix = "-"
        if self.mw.col.decks.get(did)['collapsed']:
            prefix = "+"
        # Don't add those. We show both in the qtip
        # due += lrn

        def indent():
            return "&nbsp;" * 6 * depth
        if did == self.mw.col.conf['curDeck']:
            klass = 'deck current'
        else:
            klass = 'deck'
        buf = "<tr class='%s' id='%d'>" % (klass, did)
        # deck link
        if children:
            collapse = \
                "<a class=collapse href='collapse:%d'>%s</a>" % (did, prefix)
        else:
            collapse = "<span class=collapse></span>"
        if deck['dyn']:
            extraclass = "filtered"
        else:
            extraclass = ""
        buf += """

        <td class=decktd colspan=5>%s%s<a class="deck %s"\
 href='open:%d'>%s</a></td>""" % (
            indent(), collapse, extraclass, did, name)
        # due counts

        def nonzeroColour(cnt, colour):
            if not cnt:
                colour = "#e0e0e0"
            # Show even large numbers
            # if cnt >= 1000:
            #     cnt = "1000+"
            # Also, use span with a sytle, not font.
            return '<span style="color: %s;">%s</span>' % (colour, cnt)
        # New style, put due and lrn in qtip, sum in left column as
        # before.
        buf += """\
<td align=right class="duelrn" title="%d %d">%s</td>\
<td align=right>%s</td>""" % (
            due, lrn,
            nonzeroColour(due + lrn, "#007700"),
            nonzeroColour(new, "#000099"))
        # options
        buf += "<td align=right class=opts>%s</td></tr>" % self.mw.button(
            link="opts:%d" % did,
            name="<img valign=bottom src='qrc:/icons/gears.png'>&#9662;")
        # children
        buf += self._renderDeckTree(children, depth + 1)
        return buf

    def _topLevelDragRow(self):
        return \
            "<tr class='top-level-drag-row'><td colspan='6'>&nbsp;</td></tr>"

    def _dueImg(self, due, new):
        if due:
            i = "clock-icon"
        elif new:
            i = "plus-circle"
        else:
            i = "none"
        return '<img valign=bottom src="qrc:/icons/%s.png">' % i

    # Options
    ##########################################################################

    def _showOptions(self, did):
        m = QMenu(self.mw)
        a = m.addAction(_("Rename"))
        a.connect(a, SIGNAL("triggered()"), lambda did=did: self._rename(did))
        a = m.addAction(_("Options"))
        a.connect(a, SIGNAL("triggered()"), lambda did=did: self._options(did))
        a = m.addAction(_("Delete"))
        a.connect(a, SIGNAL("triggered()"), lambda did=did: self._delete(did))
        m.exec_(QCursor.pos())

    def _rename(self, did):
        self.mw.checkpoint(_("Rename Deck"))
        deck = self.mw.col.decks.get(did)
        oldName = deck['name']
        newName = getOnlyText(_("New deck name:"), default=oldName)
        newName = newName.replace("'", "").replace('"', "")
        if not newName or newName == oldName:
            return
        try:
            self.mw.col.decks.rename(deck, newName)
        except DeckRenameError as e:
            return showWarning(e.description)
        self.show()

    def _options(self, did):
        # select the deck first, because the dyn deck conf assumes the deck
        # we're editing is the current one
        self.mw.col.decks.select(did)
        self.mw.onDeckConf()

    def _collapse(self, did):
        self.mw.col.decks.collapse(did)
        self._renderPage(reuse=True)

    def _dragDeckOnto(self, draggedDeckDid, ontoDeckDid):
        try:
            self.mw.col.decks.renameForDragAndDrop(draggedDeckDid, ontoDeckDid)
        except DeckRenameError as e:
            return showWarning(e.description)

        self.show()

    def _delete(self, did):
        if str(did) == '1':
            return showWarning(_("The default deck can't be deleted."))
        self.mw.checkpoint(_("Delete Deck"))
        deck = self.mw.col.decks.get(did)
        if not deck['dyn']:
            dids = [did] + [r[1] for r in self.mw.col.decks.children(did)]
            cnt = self.mw.col.db.scalar(
                "select count() from cards where did in {0} or "
                "odid in {0}".format(ids2str(dids)))
            if cnt:
                extra = ngettext(
                    " It has %d card.", " It has %d cards.", cnt) % cnt
            else:
                extra = None
        if deck['dyn'] or not extra or askUser(
                (_("Are you sure you wish to delete %s?") % deck['name']) +
                extra):
            self.mw.progress.start(immediate=True)
            try:
                parent_did = self.mw.col.decks.parents(did)[-1]['id']
            except:
                parent_did = 1
            self.mw.col.decks.rem(did, True)
            self.mw.col.decks.select(parent_did)
            self.mw.progress.finish()
            self.show()

    # Top buttons
    ######################################################################

    def _drawButtons(self):
        links = [
            ["", "shared", _("Get Shared")],
            ["", "create", _("Create Deck")],
            ["Ctrl+I", "import", _("Import File")],
        ]
        buf = ""
        for b in links:
            if b[0]:
                b[0] = _("Shortcut key: %s") % shortcut(b[0])
            buf += """
<button title='%s' onclick='py.link(\"%s\");'>%s</button>""" % tuple(b)
        self.bottom.draw(buf)
        if isMac:
            size = 28
        else:
            size = 36 + self.mw.fontHeightDelta * 3
        self.bottom.web.setFixedHeight(size)
        self.bottom.web.setLinkHandler(self._linkHandler)

    def _onShared(self):
        openLink(aqt.appShared + "decks/")

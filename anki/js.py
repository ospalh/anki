# -*- mode: Python ; coding: utf-8 -*-
# Copyright © 2013 Roland Sieker <ospalh@gmail.com>
# Copyright © 2013 Ken Micklas <kmicklas@gmail.com>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/copyleft/agpl.html

from pkgutil import get_data

jquery = get_data(__package__, "js/jquery.js")
plot = get_data(__package__, "js/plot.js")
ui = get_data(__package__, "js/ui.js")
browserSel = get_data(__package__, "js/browserSel.js")
qtip_css = get_data(__package__, "js/jquery.qtip.min.css")
qtip_js = get_data(__package__, "js/jquery.qtip.min.js")
images_loaded = get_data(__package__, "js/imagesloaded.min.js")

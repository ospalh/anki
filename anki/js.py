from pkgutil import get_data

jquery     = get_data(__package__, "js/jquery.js")
plot       = get_data(__package__, "js/plot.js")
ui         = get_data(__package__, "js/ui.js")
browserSel = get_data(__package__, "js/browserSel.js")

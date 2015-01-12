import BaseHTTPServer
import httplib
import re
import SocketServer
import threading
import time
import urllib
import urlparse

from core.common import *
from core.database import *
from core.settings import *

reference_urls = {}

def _insert_filter(report_html):
    """
    Inserts filtering form inside the HTML report
    """

    return report_html.replace("<!--filter-->", FILTER_FORM)

def _html_output(title, headers, rows):
    retval = "<tr>\n"
    for header in REPORT_HEADERS:
        retval += "<th>%s</th>" % header
    retval += "\n</tr>\n"
    for row in rows:
        retval += "<tr>"
        for entry in row:
            retval += "<td>%s</td>" % entry
        retval += "</tr>"
    return HTML_OUTPUT_TEMPLATE % (NAME, retval)

def _get_time_range():
    min_, max_ = None, None
    query = "SELECT MIN(time), MAX(time) FROM history"
    get_cursor().execute(query)
    _ = get_cursor().fetchone()
    if _:
        min_, max_ = _
    return min_, max_

def create_report(order=None, limit=None, offset=None, mintime=None, maxtime=None, search=None):
    """
    Creates HTML report from database
    """

    query = "SELECT * FROM history"
    if mintime:
        query += " WHERE time >= %s" % re.sub(r"[^0-9.]", "", str(mintime))
    if maxtime:
        query += " %s time <= %s" % ("AND" if mintime else "WHERE", re.sub(r"[^0-9.]", "", str(maxtime)))
    if search:
        search = search.replace("'", "").strip()
        query += " %s (src LIKE '%%%s%%' OR dst LIKE '%%%s%%' OR type LIKE '%%%s%%' OR trigger LIKE '%%%s%%' OR info LIKE '%%%s%%' OR reference LIKE '%%%s%%')" % ("AND" if mintime or maxtime else "WHERE", search, search, search, search, search, search)
    if order:
        query += " ORDER BY time %s" % re.sub(r"[^A-Za-z]", "", order)
    if limit:
        query += " LIMIT %s" % re.sub(r"[^0-9]", "", str(limit))
    if offset:
        query += " OFFSET %s" % re.sub(r"[^0-9]", "", str(offset))
    get_cursor().execute(query)
    rows = get_cursor().fetchall()
    for i in xrange(len(rows)):
        rows[i] = (time.strftime(TIME_FORMAT, time.localtime(rows[i][0])),) + rows[i][1:-1] + ("<a href='%s'>%s</a>" % (reference_urls[rows[i][-1]], rows[i][-1]) if rows[i][-1] in reference_urls else rows[i][-1],)
    return _html_output(NAME, REPORT_HEADERS, rows)

def start_httpd():
    """
    Starts reporting HTTP server
    """

    class ThreadingServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
        pass

    class ReqHandler(BaseHTTPServer.BaseHTTPRequestHandler):
        def do_GET(self):
            path, query = self.path.split('?', 1) if '?' in self.path else (self.path, "")
            if path in HTTP_RAW_FILES:
                content = HTTP_RAW_FILES[path]
                length = len(content)
                self.send_response(httplib.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                self.wfile.write(content)
            elif path == '/':
                mintime, maxtime = None, None
                params = {}
                if hasattr(self, "data"):
                    params.update(urlparse.parse_qs(self.data))
                if query:
                    params.update(urlparse.parse_qs(query))
                for key in params:
                    if params[key]:
                        params[key] = params[key][-1]
                min_, max_ = _get_time_range()
                if min_:
                    _ = time.localtime(min_)
                    params.setdefault("yearfrom", _.tm_year)
                    params.setdefault("monthfrom", _.tm_mon)
                    params.setdefault("dayfrom", _.tm_mday)
                if max_:
                    _ = time.localtime(max_)
                    params.setdefault("yearto", _.tm_year)
                    params.setdefault("monthto", _.tm_mon)
                    params.setdefault("dayto", _.tm_mday)
                if params.get("yearfrom"):
                    params.setdefault("monthfrom", 1)
                    params.setdefault("dayfrom", 1)
                    while True:
                        try:
                            mintime = time.mktime(time.strptime("%d/%02d/%02d" % (int(params["dayfrom"]), int(params["monthfrom"]), int(params["yearfrom"])), "%d/%m/%Y"))
                            break
                        except ValueError:
                            params["dayfrom"] = int(params["dayfrom"]) - 1
                if params.get("yearto"):
                    params.setdefault("monthto", 12)
                    params.setdefault("dayto", 31)
                    while True:
                        try:
                            maxtime = time.mktime(time.strptime("%d/%02d/%02d 23:59:59" % (int(params["dayto"]), int(params["monthto"]), int(params["yearto"])), "%d/%m/%Y %H:%M:%S"))
                            break
                        except ValueError:
                            params["dayto"] = int(params["dayto"]) - 1
                content = create_report(order=params.get("order", "DESC"), limit=params.get("limit"), offset=params.get("offset"), mintime=mintime, maxtime=maxtime, search=params.get("search"))
                content = _insert_filter(content)
                for param, value in params.items():
                    content = re.sub(r"(name=\"%s\".+?<option) (value=\"%s\")" % (re.escape(param), re.escape(str(value))), r"\g<1> selected \g<2>", content)
                if params.get("search"):
                    content = content.replace("input name=\"search\" value=\"\"", "input name=\"search\" value=\"%s\"" % params["search"])
                length = len(content)
                self.send_response(httplib.OK)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(httplib.NOT_FOUND)

        def do_POST(self):
            length = self.headers.getheader("Content-Length")
            data = self.rfile.read(int(length))
            data = urllib.unquote_plus(data)
            self.data = data
            self.do_GET()

        def log_message(self, format, *args):
            return

        def finish(self):
            try:
                BaseHTTPServer.BaseHTTPRequestHandler.finish(self)
            except:
                pass

    server = ThreadingServer(('', HTTP_REPORTING_PORT), ReqHandler)
    print("[i] using address '%s:%d' for HTTP reporting" % ("*" if not server.server_address[0].strip("0.") else server.server_address[0], server.server_address[1]))

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
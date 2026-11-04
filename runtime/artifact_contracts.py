"""Format-aware diagnostics; never remove or rewrite generated content."""
from __future__ import annotations

import re
from html.parser import HTMLParser


def html_envelope_issues(source: str) -> list[str]:
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.at_root = False
            self.issues = []

        def handle_starttag(self, tag, _attrs):
            self.at_root = tag == "html"

        def handle_endtag(self, _tag):
            self.at_root = False

        def handle_data(self, text):
            if self.at_root and re.match(r'\s*"\s*,\s*"complete"\s*:\s*(?:true|false)\s*}', text):
                self.issues.append("Staged JSON envelope metadata appears as text directly after the html tag")

    parser = Parser()
    parser.feed(source)
    return parser.issues

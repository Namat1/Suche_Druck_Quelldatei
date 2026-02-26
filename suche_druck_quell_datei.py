Skip to content
Namat1
Suche_Druck_Quelldatei
Repository navigation
Code
Issues
Pull requests
Actions
Projects
Wiki
Security
Insights
Settings
Files
Go to file
t
requirements.txt
suche_druck_quell_datei.py
Suche_Druck_Quelldatei
/
suche_druck_quell_datei.py
in
main

Edit

Preview
Indent mode

Spaces
Indent size

4
Line wrap mode

No wrap
Editing suche_druck_quell_datei.py file contents
  1
  2
  3
  4
  5
  6
  7
  8
  9
 10
 11
 12
 13
 14
 15
 16
 17
 18
 19
 20
 21
 22
 23
 24
 25
 26
 27
 28
 29
 30
 31
 32
 33
 34
 35
 36
 37
 38
 39
 40
 41
 42
 43
 44
 45
 46
 47
 48
 49
 50
 51
 52
 53
 54
 55
 56
 57
 58
 59
 60
 61
 62
 63
 64
 65
# =============================================================================
# app.py  -  Kombinierter Generator: Suche + Druck  ->  eine app.html
# =============================================================================
# Vollstaendig in sich geschlossen. Keine Suche.py / Druck.py benoetigt.
# Starten:  streamlit run app.py
# =============================================================================

import streamlit as st
import pandas as pd
import json
import base64
import unicodedata
import re
import datetime
from pathlib import Path
from typing import List


# =============================================================================
# EINGEBETTETE HTML-TEMPLATES  (Base64 -> werden beim Start dekodiert)
# =============================================================================

_SUCHE_B64 = "CjwhRE9DVFlQRSBodG1sPgo8aHRtbCBsYW5nPSJkZSI+CjxoZWFkPgo8bWV0YSBjaGFyc2V0PSJVVEYtOCIvPgo8bWV0YSBuYW1lPSJ2aWV3cG9ydCIgY29udGVudD0id2lkdGg9ZGV2aWNlLXdpZHRoLCBpbml0aWFsLXNjYWxlPTEuMCIvPgo8dGl0bGU+S3VuZGVuLVN1Y2hlPC90aXRsZT4KPGxpbmsgaHJlZj0iaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1JbnRlcjp3Z2h0QDUwMDs2MDA7NzAwOzgwMDs5MDAmZmFtaWx5PUludGVyK1RpZ2h0OndnaHRANTAwOzYwMDs3MDA7ODAwOzkwMCZmYW1pbHk9SmV0QnJhaW5zK01vbm86d2dodEA1MDA7NjAwOzcwMCZkaXNwbGF5PXN3YXAiIHJlbD0ic3R5bGVzaGVldCI+CjxzdHlsZT4KOnJvb3R7CiAgLS1iZzojZjRmNmZhOwogIC0tc3VyZmFjZTojZmZmZmZmOwogIC0tYWx0OiNmOGZhZmM7CgogIC0tZ3JpZDojZDVkZGU5OwogIC0tZ3JpZC0yOiNjNmQwZTM7CiAgLS1oZWFkLWdyaWQ6I2I4YzRkYTsKICAtLXJvdy1zZXA6I2VkZjJmYjsKCiAgLS10eHQ6IzBiMTIyMDsKICAtLW11dGVkOiMzMzQxNTU7CiAgLS1tdXRlZC0yOiM2NDc0OGI7CgogIC0tYWNjZW50OiMyNTYzZWI7CiAgLS1hY2NlbnQtMjojMWU0ZmQxOwoKICAtLWNoaXAtbmV1dHJhbC1iZzojZjhmYWZjOwogIC0tY2hpcC1uZXV0cmFsLWJkOiNjYmQ1ZTE7CiAgLS1jaGlwLW5ldXRyYWwtdHg6IzMzNDE1NTsKCiAgLS1jaGlwLXRvdXItYmc6I2ZmZTRlNjsKICAtLWNoaXAtdG91ci1iZDojZmI3MTg1OwogIC0tY2hpcC10b3VyLXR4OiM3ZjFkMWQ7CgogIC0tY2hpcC1rZXktYmc6I2RjZmNlNzsKICAtLWNoaXAta2V5LWJkOiMyMmM1NWU7CiAgLS1jaGlwLWtleS10eDojMTQ1MzJkOwoKICAtLWNoaXAtYWRkci1iZzojZTdmMGZmOwogIC0tY2hpcC1hZGRyLWJkOiM3YWE3ZmY7CiAgLS1jaGlwLWFkZHItdHg6IzBiM2E4YTsKCiAgLS1zaGFkb3ctc29mdDowIDFweCAwIHJnYmEoMTUsMjMsNDIsLjA0KSwgMCA4cHggMjRweCByZ2JhKDE1LDIzLDQyLC4wNik7CgogIC0tcmFkaXVzOjEwcHg7CiAgLS1yYWRpdXMtcGlsbDo5OTlweDsKfQoKKntib3gtc2l6aW5nOmJvcmRlci1ib3h9Cmh0bWwsYm9keXtoZWlnaHQ6MTAwJX0KaHRtbCwgYm9keXsgb3ZlcmZsb3cteDpoaWRkZW47IH0KCmJvZHl7CiAgbWFyZ2luOjA7CiAgYmFja2dyb3VuZDp2YXIoLS1iZyk7CiAgZm9udC1mYW1pbHk6IkludGVyIFRpZ2h0IiwgSW50ZXIsIHN5c3RlbS11aSwgLWFwcGxlLXN5c3RlbSwgIlNlZ29lIFVJIiwgUm9ib3RvLCBBcmlhbCwgc2Fucy1zZXJpZjsKICBjb2xvcjp2YXIoLS10eHQpOwogIGZvbnQtc2l6ZToxMnB4OwogIGxpbmUtaGVpZ2h0OjEuMzU7CiAgZm9udC13ZWlnaHQ6NjUwOwogIGxldHRlci1zcGFjaW5nOi4wNXB4Owp9CgoucGFnZXttaW4taGVpZ2h0OjEwMHZoOyBkaXNwbGF5OmZsZXg7IGp1c3RpZnktY29udGVudDpjZW50ZXI7IHBhZGRpbmc6MH0KLmNvbnRhaW5lcnsKICB3aWR0aDoxNzI4cHg7CiAgbWF4LXdpZHRoOjE3MjhweDsKICBtYXJnaW46MCBhdXRvOwp9Ci5jYXJkewogIGJhY2tncm91bmQ6dmFyKC0tc3VyZmFjZSk7CiAgYm9yZGVyOjFweCBzb2xpZCB2YXIoLS1ncmlkKTsKICBib3JkZXItcmFkaXVzOnZhcigtLXJhZGl1cyk7CiAgb3ZlcmZsb3c6aGlkZGVuOwogIGJveC1zaGFkb3c6dmFyKC0tc2hhZG93LXN
_DRUCK_B64  = "PCFET0NUWVBFIGh0bWw+CjxodG1sIGxhbmc9ImRlIj4KPGhlYWQ+CjxtZXRhIGNoYXJzZXQ9IlVURi04Ii8+CjxtZXRhIG5hbWU9InZpZXdwb3J0IiBjb250ZW50PSJ3aWR0aD1kZXZpY2Utd2lkdGgsIGluaXRpYWwtc2NhbGU9MS4wIi8+Cjx0aXRsZT5TZW5kZXBsYW4gR2VuZXJhdG9yPC90aXRsZT4KPGxpbmsgaHJlZj0iaHR0cHM6Ly9mb250cy5nb29nbGVhcGlzLmNvbS9jc3MyP2ZhbWlseT1JbnRlcjp3Z2h0QDUwMDs2MDA7NzAwOzgwMDs5MDAmZmFtaWx5PUludGVyK1RpZ2h0OndnaHRANTAwOzYwMDs3MDA7ODAwOzkwMCZmYW1pbHk9SmV0QnJhaW5zK01vbm86d2dodEA1MDA7NjAwOzcwMCZkaXNwbGF5PXN3YXAiIHJlbD0ic3R5bGVzaGVldCI+CjxzdHlsZT4KOnJvb3R7CiAgLS1hY2NlbnQ6IzFiNjZiMzsKICAtLWdyaWQ6I2Q1ZGRlOTsKICAtLXR4dDojMGIxMjIwOwogIC0tbXV0ZWQ6IzMzNDE1NTsKICAtLXJhZGl1czo2cHg7Cn0KKntib3gtc2l6aW5nOmJvcmRlci1ib3h9Cmh0bWwsYm9keXtoZWlnaHQ6MTAwJTttYXJnaW46MDtvdmVyZmxvdzpoaWRkZW59CmJvZHl7CiAgYmFja2dyb3VuZDojZjRmNmZhOwogIGZvbnQtZmFtaWx5OlNlZ29lIFVJLEFyaWFsLHNhbnMtc2VyaWY7CiAgY29sb3I6dmFyKC0tdHh0KTsKICBmb250LXNpemU6MTJweDsKICBmb250LXdlaWdodDo2MDA7Cn0KLyog4pSA4pSAIExBWU9VVCDilIDilIAgKi8KLmFwcHtkaXNwbGF5OmdyaWQ7Z3JpZC10ZW1wbGF0ZS1jb2x1bW5zOjMwMHB4IDFmcjtoZWlnaHQ6MTAwdmh9Ci8qIOKUgOKUgCBTSURFQkFSIOKUgOKUgCAqLwouc2lkZWJhcntiYWNrZ3JvdW5kOiNmZmY7Ym9yZGVyLXJpZ2h0OjFweCBzb2xpZCB2YXIoLS1ncmlkKTtkaXNwbGF5OmZsZXg7ZmxleC1kaXJlY3Rpb246Y29sdW1uO292ZXJmbG93OmhpZGRlbn0KLnNpZGViYXItaGVhZGVye3BhZGRpbmc6MTBweCAxMnB4O2JhY2tncm91bmQ6bGluZWFyLWdyYWRpZW50KDE4MGRlZywjZmZmIDAlLCNmNWY3ZmYgMTAwJSk7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tZ3JpZCk7Zm9udC1zaXplOjEzcHg7Zm9udC13ZWlnaHQ6ODAwO2NvbG9yOnZhcigtLWFjY2VudCk7ZGlzcGxheTpmbGV4O2FsaWduLWl0ZW1zOmNlbnRlcjtnYXA6NnB4O2ZsZXgtc2hyaW5rOjB9Ci5hcmVhLWJ1dHRvbnN7ZGlzcGxheTpncmlkO2dyaWQtdGVtcGxhdGUtY29sdW1uczoxZnIgMWZyO2dhcDo2cHg7cGFkZGluZzoxMHB4IDEycHg7Ym9yZGVyLWJvdHRvbToxcHggc29saWQgdmFyKC0tZ3JpZCk7ZmxleC1zaHJpbms6MH0KLmFyZWEtYnRue3BhZGRpbmc6NnB4IDEwcHg7Ym9yZGVyOjFweCBzb2xpZCB2YXIoLS1ncmlkKTtiYWNrZ3JvdW5kOiNmOGZhZmM7Y29sb3I6dmFyKC0tbXV0ZWQpO2N1cnNvcjpwb2ludGVyO2JvcmRlci1yYWRpdXM6OTk5cHg7Zm9udC13ZWlnaHQ6ODAwO2ZvbnQtc2l6ZToxMXB4O3RleHQtYWxpZ246Y2VudGVyO3RyYW5zaXRpb246YWxsIC4xNXN9Ci5hcmVhLWJ0bjpob3Zlcntib3JkZXItY29sb3I6dmFyKC0tYWNjZW50KTtjb2xvcjp2YXIoLS1hY2NlbnQpO2JhY2tncm91bmQ6I2VmZjZmZn0KLmFyZWEtYnRuLmFjdGl2ZXtiYWNrZ3JvdW5kOnZhcigtLWFjY2VudCk7Ym9yZGVyLWNvbG9yOnZhcigtLWFjY2VudCk7Y29sb3I6I2ZmZn0KLnNpZGViYXItY29udHJvbHN7cGFkZGluZzoxMHB4IDEycH

SUCHE_HTML_TEMPLATE: str = base64.b64decode(_SUCHE_B64).decode("utf-8")
DRUCK_HTML_TEMPLATE: str = base64.b64decode(_DRUCK_B64).decode("utf-8")


# =============================================================================
# DRUCK – Konstanten & Hilfsfunktionen
# =============================================================================

PLAN_TYP = ""
BEREICH  = "Alle Sortimente Fleischwerk"
DAYS_DE  = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]
TOUR_COLS = {
    "Montag": "Mo", "Dienstag": "Die", "Mittwoch": "Mitt",
    "Donnerstag": "Don", "Freitag": "Fr", "Samstag": "Sam",
}
DAY_SHORT_TO_DE = {
    "Mo": "Montag",  "Di": "Dienstag", "Die": "Dienstag",
    "Mi": "Mittwoch","Mit": "Mittwoch","Mitt": "Mittwoch",
    "Do": "Donnerstag","Don": "Donnerstag","Donn": "Donnerstag",
    "Fr": "Freitag", "Sa": "Samstag",  "Sam": "Samstag",
}
SORT_PRIO = {"21": 0, "1011": 1, "22": 2, "41": 3, "65": 4, "0": 5, "91": 6}


def norm_val(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and pd.isna(x):
        return ""
    s = str(x).replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def normalize_time(s) -> str:
    if isinstance(s, (datetime.time, pd.Timestamp)):
        return s.strftime("%H:%M") + " Uhr"
    s = norm_val(s)
Use Control + Shift + m to toggle the tab key moving focus. Alternatively, use esc then tab to move to the next interactive element on the page.
 

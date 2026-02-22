"""
app.py – Kombinierter Generator für app.html
============================================================
Liest die HTML-Templates aus Suche.py und Druck.py,
führt die komplette Datenverarbeitung beider Skripte aus
und bündelt das Ergebnis in einer einzigen app.html.

Voraussetzung: Suche.py und Druck.py liegen im selben
               Verzeichnis wie app.py.
============================================================
"""

import ast
import base64
import json
import re
import unicodedata
import datetime
from pathlib import Path
from typing import List

import pandas as pd
import streamlit as st


# ============================================================
# Template-Extraktion per AST (kein Code-Duplikat nötig)
# ============================================================

def extract_html_template(py_filepath: str, varname: str = "HTML_TEMPLATE") -> str:
    """
    Liest eine Python-Quelldatei und gibt den Wert der angegebenen
    String-Variablen zurück – ohne den restlichen Code auszuführen.
    """
    with open(py_filepath, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=py_filepath)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == varname:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        # Fallback: raw string-slice (für sehr große Strings)
                        pass
    raise ValueError(f"Variable '{varname}' in {py_filepath} nicht gefunden.")


# ============================================================
# SUCHE – Hilfsfunktionen (1:1 aus Suche.py)
# ============================================================

def normalize_digits_py(v) -> str:
    if pd.isna(v):
        return ""
    s = str(v).strip().replace(".0", "")
    s = "".join(ch for ch in s if ch.isdigit())
    if not s:
        return ""
    s = s.lstrip("0")
    return s if s else "0"


def norm_de_py(s: str) -> str:
    if not s:
        return ""
    x = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    x = x.replace("\u00A0", " ").replace("–", "-").replace("—", "-").lower()
    x = x.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    x = unicodedata.normalize("NFD", x)
    x = "".join(ch for ch in x if unicodedata.category(ch) != "Mn")
    x = re.sub(r"\(.*?\)", " ", x)
    x = re.sub(r"[./,;:+*_#|]", " ", x)
    x = re.sub(r"-", " ", x)
    x = re.sub(r"[^a-z\s]", " ", x)
    x = " ".join(x.split())
    return x


def build_key_map(df: pd.DataFrame) -> dict:
    if df.shape[1] < 6:
        st.warning("Schlüsseldatei hat < 6 Spalten – nehme letzte vorhandene Spalte als Schlüssel.")
    csb_col = 0
    key_col = 5 if df.shape[1] > 5 else df.shape[1] - 1
    out = {}
    for _, row in df.iterrows():
        csb = normalize_digits_py(row.iloc[csb_col] if df.shape[1] > 0 else "")
        key = normalize_digits_py(row.iloc[key_col] if df.shape[1] > 0 else "")
        if csb:
            out[csb] = key
    return out


def build_berater_map(df: pd.DataFrame) -> dict:
    out = {}
    for _, row in df.iterrows():
        v = ("" if df.shape[1] < 1 or pd.isna(row.iloc[0]) else str(row.iloc[0])).strip()
        n = ("" if df.shape[1] < 2 or pd.isna(row.iloc[1]) else str(row.iloc[1])).strip()
        t = ("" if df.shape[1] < 3 or pd.isna(row.iloc[2]) else str(row.iloc[2])).strip()
        if not t:
            continue
        k1 = norm_de_py(f"{v} {n}")
        k2 = norm_de_py(f"{n} {v}")
        for k in {k1, k2}:
            if k and k not in out:
                out[k] = t
    return out


def build_berater_csb_map(df: pd.DataFrame) -> dict:
    out = {}
    for _, row in df.iterrows():
        fach = str(row.iloc[0]).strip() if df.shape[1] > 0 and not pd.isna(row.iloc[0]) else ""
        csb  = normalize_digits_py(row.iloc[8])  if df.shape[1] > 8  and not pd.isna(row.iloc[8])  else ""
        tel  = str(row.iloc[14]).strip()          if df.shape[1] > 14 and not pd.isna(row.iloc[14]) else ""
        mail = str(row.iloc[23]).strip()          if df.shape[1] > 23 and not pd.isna(row.iloc[23]) else ""
        if csb:
            out[csb] = {"name": fach, "telefon": tel, "email": mail}
    return out


def format_lf(v) -> str:
    if pd.isna(v):
        return ""
    s = str(v).strip().replace(".0", "")
    if not s:
        return ""
    if s.isdigit():
        return f"LF{int(s)}"
    s2 = s.replace(" ", "").upper()
    if s2.startswith("LF"):
        return s2
    return s


def build_winter_map(excel_file_obj) -> dict:
    out = {}
    try:
        dfw = pd.read_excel(excel_file_obj, sheet_name="Mo-Sa Winter")
    except Exception:
        return out
    for _, row in dfw.iterrows():
        kd   = normalize_digits_py(row.iloc[3] if len(row) > 3 else "")
        tour = normalize_digits_py(row.iloc[1] if len(row) > 1 else "")
        lf   = format_lf(row.iloc[2]           if len(row) > 2 else "")
        if not kd or not tour or not lf:
            continue
        out.setdefault(kd, {})[tour] = lf
    return out


def to_data_url_suche(file) -> str:
    mime = file.type or ("image/png" if file.name.lower().endswith(".png") else "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(file.read()).decode("utf-8")


# ============================================================
# DRUCK – Hilfsfunktionen (1:1 aus Druck.py)
# ============================================================

PLAN_TYP  = "Standard"
BEREICH   = "Alle Sortimente Fleischwerk"
DAYS_DE   = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]
TOUR_COLS = {
    "Montag": "Mo", "Dienstag": "Die", "Mittwoch": "Mitt",
    "Donnerstag": "Don", "Freitag": "Fr", "Samstag": "Sam",
}
DAY_SHORT_TO_DE = {
    "Mo": "Montag", "Di": "Dienstag", "Die": "Dienstag",
    "Mi": "Mittwoch", "Mit": "Mittwoch", "Mitt": "Mittwoch",
    "Do": "Donnerstag", "Don": "Donnerstag", "Donn": "Donnerstag",
    "Fr": "Freitag", "Sa": "Samstag", "Sam": "Samstag",
}
SORT_PRIO = {"21": 0, "1011": 1, "22": 2, "41": 3, "65": 4, "0": 5, "91": 6}


def norm(x) -> str:
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
    s = norm(s)
    if not s:
        return ""
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        return s + " Uhr"
    if re.fullmatch(r"\d{1,2}", s):
        return s.zfill(2) + ":00 Uhr"
    return s


def safe_time(val) -> str:
    raw = norm(val)
    if re.fullmatch(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag)", raw):
        return ""
    return normalize_time(val)


def canon_group_id(label: str) -> str:
    s = norm(label).lower()
    m = re.search(r"\b(1011|21|41|65|0|91|22)\b", s)
    if m:
        return m.group(1)
    if "bio" in s and "geflügel" in s:
        return "41"
    if "wiesenhof" in s:
        return "1011"
    if "geflügel" in s:
        return "1011"
    if "frischfleisch" in s or "veredlung" in s or "schwein" in s or "pök" in s:
        return "65"
    if "fleisch" in s or "wurst" in s or "heidemark" in s:
        return "21"
    if "avo" in s or "gewürz" in s:
        return "0"
    if "werbe" in s or "werbemittel" in s:
        return "91"
    if "pfeiffer" in s or "gmyrek" in s or "siebert" in s or "bard" in s or "mago" in s:
        return "22"
    return "?"


def detect_bspalten(columns: List[str]):
    rx_b = re.compile(
        r"^(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+"
        r"(?:(Z|L)\s+)?(.+?)\s+B[_ ]\s*(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)$",
        re.IGNORECASE,
    )
    rx_no_b = re.compile(
        r"^(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+"
        r"(Z|L)\s+(.+?)\s+(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)$",
        re.IGNORECASE,
    )
    mapping: dict = {}
    for c in columns:
        if re.search(r"\sB[_ ]\s*", c, re.IGNORECASE):
            continue
        m = rx_no_b.match(c.strip())
        if m:
            day_de              = DAY_SHORT_TO_DE.get(m.group(1))
            zl                  = m.group(2).upper()
            group_text          = m.group(3).strip()
            bestell_de_from_name = DAY_SHORT_TO_DE.get(m.group(4))
            if day_de and bestell_de_from_name:
                key = (day_de, group_text, bestell_de_from_name)
                mapping.setdefault(key, {})
                if zl == "Z":
                    mapping[key]["zeit"] = c
                elif zl == "L":
                    mapping[key]["l"] = c
    for c in columns:
        m = rx_b.match(c.strip())
        if m:
            day_de              = DAY_SHORT_TO_DE.get(m.group(1))
            zl                  = (m.group(2) or "").upper()
            group_text          = m.group(3).strip()
            bestell_de_from_name = DAY_SHORT_TO_DE.get(m.group(4))
            if day_de and bestell_de_from_name:
                key = (day_de, group_text, bestell_de_from_name)
                mapping.setdefault(key, {})
                if zl == "Z":
                    if "zeit" not in mapping[key]:
                        mapping[key]["zeit"] = c
                elif zl == "L":
                    if "l" not in mapping[key]:
                        mapping[key]["l"] = c
                else:
                    mapping[key]["sort"]       = c
                    mapping[key]["group_text"] = group_text
    return mapping


def detect_triplets(columns: List[str]):
    rx = re.compile(
        r"^(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+(.+?)\s+"
        r"(Zeit|Zeitende|Bestellzeitende|Uhrzeit|Sort|Sortiment|Tag|Bestelltag)$",
        re.IGNORECASE,
    )
    found: dict = {}
    for c in columns:
        m = rx.match(c.strip())
        if not m:
            continue
        day_de = DAY_SHORT_TO_DE.get(m.group(1))
        if not day_de:
            continue
        group_text = m.group(2).strip()
        end_key    = m.group(3).lower()
        if end_key in ("sort", "sortiment"):
            key = "Sort"
        elif end_key in ("tag", "bestelltag"):
            key = "Tag"
        else:
            key = "Zeit"
        found.setdefault(day_de, {}).setdefault(group_text, {})[key] = c
    return found


def detect_ds_triplets(columns: List[str]):
    rx = re.compile(
        r"^DS\s+(.+?)\s+zu\s+(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+(Zeit|Sort|Tag)$",
        re.IGNORECASE,
    )
    tmp: dict = {}
    for c in columns:
        m = rx.match(c.strip())
        if not m:
            continue
        day_de = DAY_SHORT_TO_DE.get(m.group(2))
        if day_de:
            key = f"DS {m.group(1)} zu {m.group(2)}"
            tmp.setdefault(day_de, {}).setdefault(key, {})[m.group(3).capitalize()] = c
    return tmp


def load_logo_data_uri() -> str:
    candidates = []
    try:
        here = Path(__file__).resolve().parent
        candidates.append(here / "Logo_NORDfrische Center (NFC).png")
    except Exception:
        pass
    candidates.append(Path.cwd() / "Logo_NORDfrische Center (NFC).png")
    candidates.append(Path("/mnt/data/Logo_NORDfrische Center (NFC).png"))
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                b = p.read_bytes()
                return "data:image/png;base64," + base64.b64encode(b).decode("ascii")
        except Exception:
            continue
    return ""


def logo_file_to_data_uri(uploaded_file) -> str:
    if not uploaded_file:
        return ""
    mime = uploaded_file.type or "image/png"
    b    = uploaded_file.getvalue()
    return f"data:{mime};base64," + base64.b64encode(b).decode("ascii")


# ============================================================
# HTML-Generierung: Suche
# ============================================================

BLATTNAMEN_SUCHE = [
    "Direkt 1 - 99",
    "Hupa MK 882",
    "Hupa 2221-4444",
    "Hupa 7773-7779",
]
SPALTEN_MAPPING_SUCHE = {
    "csb_nummer":  "Nr",
    "sap_nummer":  "SAP-Nr.",
    "name":        "Name",
    "strasse":     "Strasse",
    "postleitzahl":"Plz",
    "ort":         "Ort",
    "fachberater": "Fachberater",
}
LIEFERTAGE_MAPPING_SUCHE = {
    "Montag": "Mo", "Dienstag": "Die", "Mittwoch": "Mitt",
    "Donnerstag": "Don", "Freitag": "Fr", "Samstag": "Sam",
}


def generate_suche_html(
    excel_file,
    key_file,
    logo_file,
    berater_file,
    berater_csb_file,
    suche_template: str,
) -> str:
    """Repliziert die HTML-Erzeugungslogik aus Suche.py."""

    if logo_file is None:
        raise ValueError("Bitte Logo (PNG/JPG) hochladen.")

    logo_data_url = to_data_url_suche(logo_file)

    with st.spinner("Lese Schlüsseldatei …"):
        key_file.seek(0)
        key_df = pd.read_excel(key_file, sheet_name=0, header=0)
        if key_df.shape[1] < 2:
            key_file.seek(0)
            key_df = pd.read_excel(key_file, sheet_name=0, header=None)
        key_map = build_key_map(key_df)

    berater_map: dict = {}
    if berater_file is not None:
        with st.spinner("Lese Fachberater-Telefonliste …"):
            berater_file.seek(0)
            bf = pd.read_excel(berater_file, sheet_name=0, header=None)
            bf = bf.rename(columns={0: "Vorname", 1: "Nachname", 2: "Nummer"}).dropna(how="all")
            berater_map = build_berater_map(bf)

    berater_csb_map: dict = {}
    if berater_csb_file is not None:
        with st.spinner("Lese Fachberater–CSB-Zuordnung …"):
            berater_csb_file.seek(0)
            try:
                bcf = pd.read_excel(berater_csb_file, sheet_name=0, header=0)
            except Exception:
                berater_csb_file.seek(0)
                bcf = pd.read_excel(berater_csb_file, sheet_name=0, header=None)
            berater_csb_map = build_berater_csb_map(bcf)

    with st.spinner("Lese Ladefolgen (Mo-Sa Winter) …"):
        excel_file.seek(0)
        winter_map = build_winter_map(excel_file)

    tour_dict: dict = {}

    def kunden_sammeln(df: pd.DataFrame):
        for _, row in df.iterrows():
            for tag, spaltenname in LIEFERTAGE_MAPPING_SUCHE.items():
                if spaltenname not in df.columns:
                    continue
                tournr_raw = str(row[spaltenname]).strip()
                if not tournr_raw or not tournr_raw.replace(".", "", 1).isdigit():
                    continue
                tournr = normalize_digits_py(tournr_raw)
                entry  = {k: str(row.get(v, "")).strip() for k, v in SPALTEN_MAPPING_SUCHE.items()}
                csb_clean = normalize_digits_py(row.get(SPALTEN_MAPPING_SUCHE["csb_nummer"], ""))
                entry["csb_nummer"]   = csb_clean
                entry["sap_nummer"]   = normalize_digits_py(entry.get("sap_nummer", ""))
                entry["postleitzahl"] = normalize_digits_py(entry.get("postleitzahl", ""))
                entry["schluessel"]   = key_map.get(csb_clean, "")
                entry["liefertag"]    = tag
                if csb_clean and csb_clean in berater_csb_map and berater_csb_map[csb_clean].get("name"):
                    entry["fachberater"] = berater_csb_map[csb_clean]["name"]
                tour_dict.setdefault(tournr, []).append(entry)

    with st.spinner("Verarbeite Kundendatei …"):
        for blatt in BLATTNAMEN_SUCHE:
            try:
                excel_file.seek(0)
                df = pd.read_excel(excel_file, sheet_name=blatt)
                kunden_sammeln(df)
            except ValueError:
                pass

    if not tour_dict:
        raise ValueError("Keine gültigen Kundendaten gefunden.")

    sorted_tours = dict(
        sorted(
            tour_dict.items(),
            key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0,
        )
    )

    last_updated = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

    return (
        suche_template
        .replace(
            "const tourkundenData   = {  }",
            f"const tourkundenData   = {json.dumps(sorted_tours, ensure_ascii=False)}",
        )
        .replace(
            "const keyIndex         = {  }",
            f"const keyIndex         = {json.dumps(key_map, ensure_ascii=False)}",
        )
        .replace(
            "const beraterIndex     = {  }",
            f"const beraterIndex     = {json.dumps(berater_map, ensure_ascii=False)}",
        )
        .replace(
            "const beraterCSBIndex  = {  }",
            f"const beraterCSBIndex  = {json.dumps(berater_csb_map, ensure_ascii=False)}",
        )
        .replace(
            "const winterIndex      = {  }",
            f"const winterIndex      = {json.dumps(winter_map, ensure_ascii=False)}",
        )
        .replace("__LOGO_DATA_URL__", logo_data_url)
        .replace("__LAST_UPDATED__", last_updated)
    )


# ============================================================
# HTML-Generierung: Druck
# ============================================================

SHEETS_DRUCK = {
    "direkt":  "Direkt 1 - 99",
    "mk":      "Hupa MK 882",
    "nms":     "Hupa 2221-4444",
    "malchow": "Hupa 7773-7779",
}


def generate_druck_html(up, logo_up, druck_template: str) -> str:
    """Repliziert die HTML-Erzeugungslogik aus Druck.py."""

    logo_preview_uri = logo_file_to_data_uri(logo_up) or load_logo_data_uri()

    all_data: dict = {}

    for area_key, sheet_name in SHEETS_DRUCK.items():
        with st.spinner(f"Verarbeite: {sheet_name} …"):
            try:
                up.seek(0)
                df = pd.read_excel(up, sheet_name=sheet_name)
            except Exception as e:
                st.warning(f"Blatt '{sheet_name}' nicht gefunden / übersprungen: {e}")
                continue

        cols    = df.columns.tolist()
        trip    = detect_triplets(cols)
        bmap    = detect_bspalten(cols)
        ds_trip = detect_ds_triplets(cols)

        data: dict = {}

        for _, r in df.iterrows():
            knr = norm(r.get("Nr", ""))
            if not knr:
                continue

            bestell: list = []
            for d_de in DAYS_DE:
                day_items: list = []

                # 1) Triplets
                if d_de in trip:
                    for group_text, f in trip[d_de].items():
                        s   = norm(r.get(f.get("Sort")))
                        t   = safe_time(r.get(f.get("Zeit")))
                        tag = norm(r.get(f.get("Tag")))
                        if s or t or tag:
                            actual_gid = canon_group_id(s)
                            day_items.append(
                                {
                                    "liefertag":      d_de,
                                    "sortiment":      s,
                                    "bestelltag":     tag,
                                    "bestellschluss": t,
                                    "prio":           SORT_PRIO.get(actual_gid, 50),
                                }
                            )

                # 2) B-Spalten
                for k in [kk for kk in bmap if kk[0] == d_de]:
                    f     = bmap[k]
                    s     = norm(r.get(f.get("sort", "")))
                    z     = safe_time(r.get(f.get("zeit", "")))
                    l_col = f.get("l")
                    if l_col:
                        tag = norm(r.get(l_col, ""))
                        if not tag:
                            tag = k[2]
                    else:
                        tag = k[2]
                    if s or z:
                        actual_gid = canon_group_id(s)
                        day_items.append(
                            {
                                "liefertag":      d_de,
                                "sortiment":      s,
                                "bestelltag":     tag,
                                "bestellschluss": z,
                                "prio":           SORT_PRIO.get(actual_gid, 50),
                            }
                        )

                # 3) Deutsche See
                if d_de in ds_trip:
                    for key_ds in ds_trip[d_de]:
                        f   = ds_trip[d_de][key_ds]
                        s   = norm(r.get(f.get("Sort")))
                        t   = safe_time(r.get(f.get("Zeit")))
                        tag = norm(r.get(f.get("Tag")))
                        if s or t or tag:
                            day_items.append(
                                {
                                    "liefertag":      d_de,
                                    "sortiment":      s,
                                    "bestelltag":     tag,
                                    "bestellschluss": t,
                                    "prio":           5.5,
                                }
                            )

                day_items.sort(key=lambda x: x["prio"])
                bestell.extend(day_items)

            data[knr] = {
                "plan_typ":   PLAN_TYP,
                "bereich":    BEREICH,
                "kunden_nr":  knr,
                "name":       norm(r.get("Name", "")),
                "strasse":    norm(r.get("Strasse", "")),
                "plz":        norm(r.get("Plz", "")),
                "ort":        norm(r.get("Ort", "")),
                "fachberater":norm(r.get("Fachberater", "")),
                "tours":      {d: norm(r.get(TOUR_COLS[d], "")) for d in DAYS_DE},
                "bestell":    bestell,
            }

        all_data[area_key] = data
        st.success(f"✓ {sheet_name}: {len(data)} Kunden verarbeitet")

    json_data = json.dumps(all_data, ensure_ascii=False, separators=(",", ":"))

    return (
        druck_template
        .replace("__DATA_JSON__",   json_data)
        .replace("__LOGO_DATAURI__", logo_preview_uri or "")
    )


# ============================================================
# HTML-Kombinierer
# ============================================================

_NAV_BTN_BASE = (
    "position:fixed;bottom:20px;right:20px;z-index:9999;"
    "padding:10px 22px;border:none;border-radius:8px;"
    "cursor:pointer;font-weight:700;font-size:14px;"
    "box-shadow:0 4px 14px rgba(0,0,0,.28);"
    "font-family:'Segoe UI',Arial,sans-serif;"
    "letter-spacing:.2px;"
)


def _inject_before_body_close(html: str, snippet: str) -> str:
    """Fügt einen HTML-Schnipsel unmittelbar vor </body> ein."""
    pos = html.rfind("</body>")
    if pos == -1:
        return html + snippet
    return html[:pos] + snippet + html[pos:]


def _escape_js_template(s: str) -> str:
    """
    Escaped einen String für die sichere Verwendung
    innerhalb eines JavaScript Template-Literals (Backtick-String).
    """
    s = s.replace("\\", "\\\\")
    s = s.replace("`",  "\\`")
    s = s.replace("${", "\\${")
    return s


def combine_html(suche_html: str, druck_html: str) -> str:
    """
    Fügt in jede Seite einen Navigations-Button ein und
    kombiniert beide vollständigen HTML-Dokumente in einer
    einzigen app.html.

    Die Isolation erfolgt über iframes mit Blob-URLs, sodass
    CSS und JavaScript beider Seiten vollständig getrennt
    bleiben und keinerlei Anpassungen an den Originaldateien
    nötig sind.
    """

    # --- Navigations-Buttons in die jeweiligen HTML-Seiten injizieren ---
    btn_to_druck = (
        '<button '
        'onclick="window.parent.postMessage(\'show-druck\',\'*\')" '
        f'style="{_NAV_BTN_BASE}background:#16a34a;color:white;">'
        "&#128424;&#160;Zum Druckbereich"
        "</button>"
    )
    btn_to_suche = (
        '<button '
        'onclick="window.parent.postMessage(\'show-suche\',\'*\')" '
        f'style="{_NAV_BTN_BASE}background:#2563eb;color:white;">'
        "&#128269;&#160;Zur Suche zur&#252;ck"
        "</button>"
    )

    suche_final = _inject_before_body_close(suche_html, btn_to_druck)
    druck_final  = _inject_before_body_close(druck_html,  btn_to_suche)

    s_esc = _escape_js_template(suche_final)
    d_esc = _escape_js_template(druck_final)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Kunden-App &#8211; Suche &amp; Druck</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  html,body{{height:100%;overflow:hidden;font-family:'Segoe UI',Arial,sans-serif}}

  /* ---- Top-Navigation ---- */
  .topnav{{
    height:48px;
    background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);
    display:flex;align-items:center;padding:0 18px;gap:10px;
    box-shadow:0 2px 10px rgba(0,0,0,.35);
    flex-shrink:0;
  }}
  .topnav-title{{
    color:#fff;font-weight:800;font-size:15px;
    margin-right:8px;letter-spacing:.2px;
  }}
  .nav-btn{{
    padding:6px 20px;border-radius:20px;
    border:1px solid rgba(255,255,255,.35);
    cursor:pointer;font-weight:700;font-size:12px;
    transition:all .15s ease;
    background:rgba(255,255,255,.15);color:#fff;
  }}
  .nav-btn:hover:not(.active){{background:rgba(255,255,255,.28)}}
  .nav-btn.active{{
    background:#fff;color:#1e3a5f;
    box-shadow:0 2px 8px rgba(0,0,0,.18);
  }}

  /* ---- Iframe-Container ---- */
  .frame-container{{height:calc(100vh - 48px);display:flex;flex-direction:column}}
  iframe{{flex:1;width:100%;border:none;display:none}}
  iframe.active{{display:block}}
</style>
</head>
<body>

<nav class="topnav">
  <span class="topnav-title">&#128203; Kunden-App</span>
  <button class="nav-btn active" id="btn-suche" onclick="showSection('suche')">
    &#128269; Suche
  </button>
  <button class="nav-btn" id="btn-druck" onclick="showSection('druck')">
    &#128424; Druckbereich
  </button>
</nav>

<div class="frame-container">
  <iframe id="frame-suche" class="active" title="Kunden-Suche"></iframe>
  <iframe id="frame-druck"               title="Druckbereich" ></iframe>
</div>

<script>
/* ---- Blob-URLs erzeugen und in die iframes laden ---- */
const SUCHE_HTML = `{s_esc}`;
const DRUCK_HTML  = `{d_esc}`;

(function () {{
  function mkUrl(html) {{
    return URL.createObjectURL(
      new Blob([html], {{ type: "text/html;charset=utf-8" }})
    );
  }}
  document.getElementById("frame-suche").src = mkUrl(SUCHE_HTML);
  document.getElementById("frame-druck" ).src = mkUrl(DRUCK_HTML);
}})();

/* ---- Bereichswechsel ---- */
function showSection(s) {{
  ["suche", "druck"].forEach(function (id) {{
    document.getElementById("frame-" + id).className =
      id === s ? "active" : "";
    document.getElementById("btn-" + id).className =
      "nav-btn" + (id === s ? " active" : "");
  }});
}}

/* ---- Nachrichten von den iframes empfangen ---- */
window.addEventListener("message", function (e) {{
  if (e.data === "show-druck") showSection("druck");
  if (e.data === "show-suche") showSection("suche");
}});
</script>
</body>
</html>"""


# ============================================================
# STREAMLIT-OBERFLÄCHE
# ============================================================

st.set_page_config(
    page_title="Kunden-App Generator",
    page_icon="📋",
    layout="wide",
)

st.title("📋 Kunden-App Generator")
st.write(
    "Erzeugt eine einzige **app.html**, die sowohl die Suche- als auch die "
    "Druckfunktion enthält. Navigationsbuttons verbinden beide Bereiche."
)

# ---- Session State initialisieren ----
for key in ("suche_html", "druck_html"):
    if key not in st.session_state:
        st.session_state[key] = None

# ---- Templates aus Quelldateien laden ----
HERE     = Path(__file__).resolve().parent
SUCHE_PY = HERE / "Suche.py"
DRUCK_PY  = HERE / "Druck.py"


@st.cache_resource
def load_templates():
    tmpl_suche = extract_html_template(str(SUCHE_PY))
    tmpl_druck  = extract_html_template(str(DRUCK_PY))
    return tmpl_suche, tmpl_druck


try:
    SUCHE_TEMPLATE, DRUCK_TEMPLATE = load_templates()
    st.success("✅ HTML-Templates aus **Suche.py** und **Druck.py** geladen.")
except Exception as exc:
    st.error(f"❌ Fehler beim Laden der Templates: {exc}")
    st.info(
        "Stelle sicher, dass **Suche.py** und **Druck.py** "
        "im selben Verzeichnis wie **app.py** liegen."
    )
    st.stop()

st.divider()

# ---- Eingabe-Tabs ----
tab_suche, tab_druck = st.tabs(
    ["🔍  Suche – Datei-Upload", "🖨️  Druck – Datei-Upload"]
)

# =========================================================
# Tab 1: Suche
# =========================================================
with tab_suche:
    st.subheader("Suche – Pflicht- und optionale Dateien")
    st.caption(
        "Druck: SAP-Spalte weg | LF-Header im Druck = Ladefolge | "
        "Kopieren: Outlook-optimierte HTML-Tabelle (klein bei STRG+V)."
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        s_excel      = st.file_uploader(
            "Quelldatei (Kundendaten)", type=["xlsx"], key="s_excel"
        )
    with col2:
        s_key        = st.file_uploader(
            "Schlüsseldatei (A=CSB, F=Schlüssel)", type=["xlsx"], key="s_key"
        )
    with col3:
        s_logo       = st.file_uploader(
            "Logo (PNG/JPG)", type=["png", "jpg", "jpeg"], key="s_logo"
        )

    s_berater     = st.file_uploader(
        "OPTIONAL: Fachberater-Telefonliste (A=Vorname, B=Nachname, C=Nummer)",
        type=["xlsx"], key="s_berater",
    )
    s_berater_csb = st.file_uploader(
        "OPTIONAL: Fachberater–CSB-Zuordnung "
        "(A=Fachberater, I=CSB, O=Markt-Tel, X=Markt-Mail)",
        type=["xlsx"], key="s_berater_csb",
    )

    if s_excel and s_key and s_logo:
        if st.button("✅ Suche-HTML generieren", key="btn_suche", type="primary"):
            try:
                st.session_state.suche_html = generate_suche_html(
                    s_excel, s_key, s_logo,
                    s_berater, s_berater_csb,
                    SUCHE_TEMPLATE,
                )
                st.success("Suche-HTML erfolgreich generiert!")
            except Exception as exc:
                st.error(f"Fehler: {exc}")
    else:
        st.info("Bitte Quelldatei, Schlüsseldatei und Logo hochladen.")

    if st.session_state.suche_html:
        st.caption(
            f"Suche-HTML steht bereit "
            f"({len(st.session_state.suche_html) // 1024} KB)"
        )

# =========================================================
# Tab 2: Druck
# =========================================================
with tab_druck:
    st.subheader("Druck – Sendeplan Generator")
    st.write("Verarbeitet 4 Bereiche: Direkt, MK, HuPa NMS, HuPa Malchow")

    d_logo = st.file_uploader(
        "Logo oben im Druck (PNG/JPG/SVG, optional)",
        type=["png", "jpg", "jpeg", "svg"],
        key="d_logo",
    )

    # Optional Preview
    d_logo_uri = logo_file_to_data_uri(d_logo) or load_logo_data_uri()
    if d_logo_uri:
        st.image(d_logo_uri, caption="Verwendetes Logo (Vorschau)", width=200)
    else:
        st.info(
            "Kein Logo gewählt/gefunden. "
            "(Upload oder Datei 'Logo_NORDfrische Center (NFC).png')"
        )

    d_excel = st.file_uploader(
        "Excel-Datei laden", type=["xlsx"], key="d_excel"
    )

    if d_excel:
        if st.button("✅ Druck-HTML generieren", key="btn_druck", type="primary"):
            try:
                st.session_state.druck_html = generate_druck_html(
                    d_excel, d_logo, DRUCK_TEMPLATE
                )
                st.success(
                    f"Druck-HTML erfolgreich generiert "
                    f"({len(st.session_state.druck_html) // 1024} KB)!"
                )
            except Exception as exc:
                st.error(f"Fehler: {exc}")
    else:
        st.info("Bitte Excel-Datei hochladen.")

    if st.session_state.druck_html:
        st.caption(
            f"Druck-HTML steht bereit "
            f"({len(st.session_state.druck_html) // 1024} KB)"
        )

# =========================================================
# Kombination & Download
# =========================================================
st.divider()
st.subheader("🔗 Kombinieren & app.html herunterladen")

col_a, col_b = st.columns(2)
with col_a:
    suche_ok = st.session_state.suche_html is not None
    st.metric(
        "Suche-HTML",
        "✅ bereit" if suche_ok else "❌ noch nicht generiert",
    )
with col_b:
    druck_ok = st.session_state.druck_html is not None
    st.metric(
        "Druck-HTML",
        "✅ bereit" if druck_ok else "❌ noch nicht generiert",
    )

if suche_ok and druck_ok:
    with st.spinner("Kombiniere zu app.html …"):
        app_html = combine_html(
            st.session_state.suche_html,
            st.session_state.druck_html,
        )

    st.download_button(
        label="⬇️  app.html herunterladen",
        data=app_html.encode("utf-8"),
        file_name="app.html",
        mime="text/html",
        type="primary",
    )
    st.caption(
        f"Enthält beide Bereiche · Gesamtgröße: ca. {len(app_html) // 1024} KB  ·  "
        f"Navigation über Buttons in der Kopfleiste sowie die jeweiligen "
        f"Schaltflächen in den Seiten selbst."
    )
else:
    st.warning(
        "Bitte zuerst in beiden Tabs die jeweiligen HTML-Bereiche generieren, "
        "dann erscheint hier der Download-Button."
    )

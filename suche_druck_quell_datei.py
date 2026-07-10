# =============================================================================
# app.py  -  Kombinierter Generator: Suche + Fahrzeugwäsche  ->  eine app.html
# =============================================================================
# Die erzeugte suche.html ist vollstaendig eigenstaendig.
# Alle PDF-Dokumente werden komprimiert in die einzelne HTML-Datei eingebettet.
# Starten:  streamlit run app.py
# =============================================================================

from __future__ import annotations

# Native Bibliotheken auf einen Thread begrenzen. Das reduziert Startspitzen
# und vermeidet instabile OpenMP-/BLAS-Konstellationen auf kleinen Cloud-VMs.
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("ARROW_NUM_THREADS", "1")
os.environ.setdefault("MALLOC_ARENA_MAX", "2")

import faulthandler
try:
    faulthandler.enable(all_threads=True)
except Exception:
    pass

def _boot_log(message: str) -> None:
    print(f"[NFC-BOOT] {message}", flush=True)

_boot_log("01 Python-Skript gestartet")

import streamlit as st
_boot_log("02 Streamlit importiert")

import importlib
import importlib.util
import json
import base64
import unicodedata
import re
import datetime
import time
import hashlib
import io
import zlib
from pathlib import Path
from typing import List


class _LazyPandas:
    """Importiert pandas erst bei der ersten tatsächlichen Dateiverarbeitung."""
    _module = None

    def _load(self):
        if self._module is None:
            _boot_log("PANDAS-START: pandas wird jetzt importiert")
            self._module = importlib.import_module("pandas")
            _boot_log(f"PANDAS-OK: pandas {getattr(self._module, '__version__', '?')} importiert")
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)


pd = _LazyPandas()
_boot_log("03 Standardimporte bereit; pandas wird verzögert geladen")

st.set_page_config(page_title="NFC Generator v37", layout="wide")
_boot_log("04 Seitenkonfiguration gesetzt")

APP_CACHE_VERSION = "fahrerbewertung-dashboard-2026-07-11-v37-diagnose"
EXTRA_CACHE_VERSION = "extra-parser-2026-07-11-v37-diagnose"
APP_DISPLAY_VERSION = "37"
APP_DISPLAY_NAME = "NFC Generator"



# =============================================================================
# EXCEL-ENGINE — calamine ist 5-10x schneller als openpyxl beim Lesen.
# Wenn 'python-calamine' installiert ist, nutzen wir es; sonst Fallback.
# Schreiben bleibt openpyxl (calamine kann nur lesen).
# =============================================================================
# Nur die Verfügbarkeit prüfen; kein nativer Import während des App-Starts.
_HAS_CALAMINE = importlib.util.find_spec("python_calamine") is not None
EXCEL_READ_ENGINE = "calamine" if _HAS_CALAMINE else "openpyxl"
_boot_log(f"05 Excel-Engine vorbereitet: {EXCEL_READ_ENGINE}")

EXCLUDED_DRIVER_NAMES = (
    "Ch.Holtz", "Paasch", "Meyer", "Ihde", "Spedition M+S Express 4", "Spedition M+S Express 3",
    "Spedition M+S Express 2", "Spedition M+S Express 1", "Spedition Meyer 1", "Spedition Meyer 2",
    "Spedition Meyer 3", "Spedition Meyer 4", "Spedition Meyer 5", "Spedition Meyer 6",
    "Spedition Meyer 7 (36er)", "Spedition Meyer 8", "Spedition Meyer Sz.", "Paasch & Reinke 1",
    "Paasch & Reinke 2", "Paasch & Reinke 3", "deVries", "Spedition Ihde", "Insellogistik 1",
    "Insellogistik 2", "Zippel Logistik T1", "Zippel Logistik T2", "Zippel Logistik T3",
    "Ch. Holtz T1", "Ch. Holtz T2", "Ch. Holtz T3", "T&D Spedition", "Kudex 1", "Kudex 2", "FP Fleischwerk",
)






def read_upload_bytes(uploaded_file) -> bytes:
    if uploaded_file is None:
        return b""
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    data = uploaded_file.read()
    if data is None:
        return b""
    if isinstance(data, str):
        return data.encode("utf-8")
    return data


def upload_signature(uploaded_file) -> str:
    """Liefert eine eindeutige Signatur fuer ein UploadedFile.

    Streamlit-UploadedFile-Objekte haben eine ``file_id`` bzw. ``_file_urls``,
    die sich nur dann aendert, wenn der Nutzer eine neue Datei hochlaedt.
    Wir benutzen Name + Size + file_id als billigen Schluessel und sparen uns
    das vollstaendige Auslesen + SHA1 ueber 10-50 MB pro Re-Run.
    Fallback ist die alte SHA1-Methode, falls kein file_id verfuegbar ist.
    """
    if uploaded_file is None:
        return ""
    name = getattr(uploaded_file, "name", "") or ""
    size = getattr(uploaded_file, "size", 0) or 0
    fid = getattr(uploaded_file, "file_id", None) or getattr(uploaded_file, "_file_urls", None) or ""
    if fid:
        return f"{name}|{size}|{fid}"
    # Fallback: SHA1 ueber Inhalt
    payload = read_upload_bytes(uploaded_file)
    digest = hashlib.sha1()
    digest.update(name.encode("utf-8", errors="ignore"))
    digest.update(b"|")
    digest.update(payload)
    return digest.hexdigest()


def uploads_signature(uploaded_files) -> str:
    digest = hashlib.sha1()
    for uploaded_file in uploaded_files or []:
        digest.update(upload_signature(uploaded_file).encode("utf-8", errors="ignore"))
        digest.update(b"|")
    return digest.hexdigest()


def combine_signatures(*parts: str) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update((part or "").encode("utf-8", errors="ignore"))
        digest.update(b"|")
    return digest.hexdigest()


def is_excluded_driver(name: str) -> bool:
    name = str(name)
    return any(excluded in name for excluded in EXCLUDED_DRIVER_NAMES)


def get_cached_export_b64(cache_key: str, source_value: str, builder) -> str:
    cache = st.session_state.setdefault("_export_b64_cache", {})
    cached = cache.get(cache_key)
    if cached and cached.get("source") == source_value:
        return cached.get("b64", "")

    payload = builder(source_value) or b""
    encoded = base64.b64encode(payload).decode("ascii") if payload else ""
    cache[cache_key] = {"source": source_value, "b64": encoded}
    return encoded




# =============================================================================
# CLOUD-STABILE ASSET-VERWALTUNG
# Große PDF-, HTML- und JavaScript-Daten liegen nicht mehr als Python-Literale
# in dieser Datei. Das senkt den RAM-Bedarf des Python-Parsers massiv.
# Die erzeugte suche.html bleibt weiterhin eine einzige, eigenständige Datei.
# =============================================================================
_ASSET_DIR = Path(__file__).resolve().parent / "nfc_assets"
_STATIC_ASSET_PATH = _ASSET_DIR / "static_payload.json.zlib"
_PDF_ASSET_PATH = _ASSET_DIR / "embedded_pdfs.json.zlib"
_ASSET_META_PATH = _ASSET_DIR / "asset_meta.json"
_STATIC_PAYLOAD_CACHE = None
_PDF_DOCUMENTS_CACHE = None
_ASSET_META_CACHE = None


def _read_zlib_json(path: Path, label: str):
    if not path.is_file():
        raise FileNotFoundError(
            f"{label} fehlt: {path}. Bitte den Ordner 'nfc_assets' zusammen "
            "mit suche_druck_quell_datei.py ins GitHub-Repository hochladen."
        )
    packed = path.read_bytes()
    try:
        raw = zlib.decompress(packed)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"{label} ist beschädigt oder unlesbar: {path}") from exc


def _static_payload() -> dict:
    global _STATIC_PAYLOAD_CACHE
    if _STATIC_PAYLOAD_CACHE is None:
        value = _read_zlib_json(_STATIC_ASSET_PATH, "Statische NFC-Daten")
        if not isinstance(value, dict):
            raise RuntimeError("Statische NFC-Daten haben ein ungültiges Format.")
        _STATIC_PAYLOAD_CACHE = value
    return _STATIC_PAYLOAD_CACHE


def _static_payload_text(key: str) -> str:
    value = _static_payload().get(key)
    if not isinstance(value, str):
        raise KeyError(f"Statischer NFC-Baustein fehlt: {key}")
    return value


def _load_embedded_pdf_documents() -> dict:
    global _PDF_DOCUMENTS_CACHE
    if _PDF_DOCUMENTS_CACHE is None:
        value = _read_zlib_json(_PDF_ASSET_PATH, "Eingebettete PDF-Daten")
        if not isinstance(value, dict):
            raise RuntimeError("Eingebettete PDF-Daten haben ein ungültiges Format.")
        _PDF_DOCUMENTS_CACHE = value
    return _PDF_DOCUMENTS_CACHE


def _load_asset_meta() -> dict:
    global _ASSET_META_CACHE
    if _ASSET_META_CACHE is None:
        if not _ASSET_META_PATH.is_file():
            raise FileNotFoundError(
                f"Asset-Metadaten fehlen: {_ASSET_META_PATH}. Bitte den Ordner "
                "'nfc_assets' vollständig hochladen."
            )
        value = json.loads(_ASSET_META_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("Asset-Metadaten haben ein ungültiges Format.")
        _ASSET_META_CACHE = value
    return _ASSET_META_CACHE


def _asset_installation_error() -> str:
    missing = [
        str(path.relative_to(Path(__file__).resolve().parent))
        for path in (_STATIC_ASSET_PATH, _PDF_ASSET_PATH, _ASSET_META_PATH)
        if not path.is_file()
    ]
    if not missing:
        return ""
    return "Fehlende Programmdateien: " + ", ".join(missing)


# =============================================================================
# EINGEBETTETE HTML-TEMPLATES  (Base64 -> werden beim Start dekodiert)
# =============================================================================

# _SUCHE_B64 wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.
# _DRUCK_B64 wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# Hinweis: SUCHE_HTML_TEMPLATE wird unten in get_suche_template() einmalig
# aufgebaut und gecached. Alle _patch_*-Funktionen bleiben Pure Functions.



def _patch_suche_template_layout(template: str) -> str:
    """Macht die Ergebnis-Tabelle robuster bei langen Notizen und vielen Touren."""
    replacements = [
        (
            '.cell{display:flex; flex-direction:column; gap:6px; min-height:84px; width:100%; justify-content:flex-start}',
            '.cell{display:flex; flex-direction:column; gap:8px; min-height:96px; width:100%; min-width:0; justify-content:flex-start}'
        ),
        (
            '.cell-stack{display:flex; flex-direction:column; gap:6px; min-height:100%}',
            '.cell-stack{display:flex; flex-direction:column; gap:8px; min-height:100%; min-width:0}'
        ),
        (
            '.cell-slot{display:flex; flex-wrap:wrap; gap:6px; align-items:flex-start; align-content:flex-start; min-height:26px}',
            '.cell-slot{display:flex; flex-wrap:wrap; gap:6px; align-items:flex-start; align-content:flex-start; min-height:26px; width:100%; min-width:0}'
        ),
        (
            '.cell-slot.note-slot{min-height:24px}',
            '.cell-slot.note-slot{min-height:32px}'
        ),
        (
            '.cell-notiz{\n  display:flex; flex-wrap:wrap; gap:6px; margin-top:0;\n}',
            '.cell-notiz{\n  display:flex; flex-wrap:wrap; gap:6px; margin-top:0; width:100%; min-width:0;\n}'
        ),
        (
            '.notiz-badge{\n  display:inline-block;\n  background:#fef3c7;\n  border:1px solid #fbbf24;\n  color:#92400e;\n  border-radius:4px;\n  padding:3px 8px;\n  font-size:10px;\n  font-weight:700;\n  line-height:1.25;\n  max-width:100%;\n  overflow:hidden;\n  text-overflow:ellipsis;\n  white-space:nowrap;\n}',
            '.notiz-badge{\n  display:inline-flex;\n  align-items:flex-start;\n  background:#fef3c7;\n  border:1px solid #fbbf24;\n  color:#92400e;\n  border-radius:4px;\n  padding:4px 8px;\n  font-size:10px;\n  font-weight:700;\n  line-height:1.3;\n  max-width:100%;\n  min-width:0;\n  overflow:hidden;\n  text-overflow:clip;\n  white-space:normal;\n  overflow-wrap:anywhere;\n  word-break:break-word;\n}'
        ),
        (
            '.tour-inline{display:flex; flex-wrap:wrap; gap:6px; align-content:flex-start; min-height:26px}',
            '.tour-inline{display:flex; flex-wrap:wrap; gap:6px; row-gap:8px; align-content:flex-start; min-height:26px; width:100%; min-width:0}'
        ),
        (
            '.tour-btn{\n  display:inline-flex;\n  align-items:center;\n  min-height:24px;\n  background:var(--chip-tour-bg);\n  border:1px solid var(--chip-tour-bd);\n  color:var(--chip-tour-tx);\n  padding:4px 8px;\n  border-radius:var(--radius-pill);\n  font-weight:800;\n  font-size:10px;\n  cursor:pointer;\n  line-height:1.2;\n  letter-spacing:.12px;\n  box-shadow:none;\n}',
            '.tour-btn{\n  display:inline-flex;\n  align-items:center;\n  min-height:24px;\n  max-width:100%;\n  min-width:0;\n  flex:0 1 auto;\n  background:var(--chip-tour-bg);\n  border:1px solid var(--chip-tour-bd);\n  color:var(--chip-tour-tx);\n  padding:4px 8px;\n  border-radius:var(--radius-pill);\n  font-weight:800;\n  font-size:10px;\n  cursor:pointer;\n  line-height:1.2;\n  letter-spacing:.12px;\n  box-shadow:none;\n}'
        ),
        (
            '.phone-col{display:grid; grid-template-columns:1fr; gap:6px; align-content:start; min-height:108px}',
            '.phone-col{display:grid; grid-template-columns:1fr; gap:6px; align-content:start; min-height:108px; min-width:0}'
        ),
        (
            '<col style="width:15%">\n            <col style="width:33%">\n            <col style="width:18%">\n            <col style="width:10%">\n            <col style="width:24%">',
            '<col style="width:13%">\n            <col style="width:38%">\n            <col style="width:21%">\n            <col style="width:8%">\n            <col style="width:20%">'
        ),
    ]
    for old, new in replacements:
        template = template.replace(old, new)
    return template


def _patch_suche_template_header(template: str) -> str:
    """Header etwas kompakter und linksbuendig (vorher Inline-Patch)."""
    return (
        template
        .replace(
            ".header{\n  padding:10px 12px;\n  background:linear-gradient(180deg,#f0f2f7 0%,#e6eaf1 100%);\n  border-bottom:1px solid var(--grid);\n  display:flex; align-items:center; justify-content:center;\n  position:relative;\n}",
            ".header{\n  padding:8px 18px;\n  background:linear-gradient(180deg,#f0f2f7 0%,#e6eaf1 100%);\n  border-bottom:1px solid var(--grid);\n  display:flex; align-items:center; justify-content:flex-start;\n  position:relative;\n}"
        )
        .replace(
            ".brand-logo{height:46px; width:auto}",
            ".brand-logo{height:32px; width:auto; margin-left:4px}"
        )
    )


def _patch_suche_template_weniger_luftig(template: str) -> str:
    """Macht die obere Übersicht wieder etwas kompakter, ohne ungleich zu wirken."""
    replacements = [
        (
            '.table-section{\n  padding:10px 12px 16px;\n  overflow:visible;\n}',
            '.table-section{\n  padding:8px 12px 12px;\n  overflow:visible;\n}'
        ),
        (
            'tbody td{\n  padding:10px 10px;\n  vertical-align:top;\n  font-weight:650;\n  border-bottom:1px solid var(--grid);\n  border-right:1px solid var(--grid);\n  background:#fff;\n  overflow:hidden;\n}',
            'tbody td{\n  padding:8px 9px;\n  vertical-align:top;\n  font-weight:650;\n  border-bottom:1px solid var(--grid);\n  border-right:1px solid var(--grid);\n  background:#fff;\n  overflow:hidden;\n}'
        ),
        (
            'tbody tr+tr td{border-top:2px solid var(--row-sep)}',
            'tbody tr+tr td{border-top:1px solid var(--row-sep)}'
        ),
        (
            '.cell{display:flex; flex-direction:column; gap:8px; min-height:96px; width:100%; min-width:0; justify-content:flex-start}',
            '.cell{display:flex; flex-direction:column; gap:4px; min-height:68px; width:100%; min-width:0; justify-content:flex-start}'
        ),
        (
            '.cell-stack{display:flex; flex-direction:column; gap:8px; min-height:100%; min-width:0}',
            '.cell-stack{display:flex; flex-direction:column; gap:4px; min-height:100%; min-width:0}'
        ),
        (
            '.cell-top{font-weight:800; min-height:18px; line-height:1.25}',
            '.cell-top{font-weight:800; min-height:16px; line-height:1.15}'
        ),
        (
            '.cell-sub{color:var(--muted); min-height:18px; line-height:1.25}',
            '.cell-sub{color:var(--muted); min-height:14px; line-height:1.15}'
        ),
        (
            '.cell-slot.note-slot{min-height:32px}',
            '.cell-slot.note-slot{min-height:22px}'
        ),
        (
            '.tour-inline{display:flex; flex-wrap:wrap; gap:6px; row-gap:8px; align-content:flex-start; min-height:26px; width:100%; min-width:0}',
            '.tour-inline{display:flex; flex-wrap:wrap; gap:6px; row-gap:6px; align-content:flex-start; min-height:24px; width:100%; min-width:0}'
        ),
        (
            '.tour-btn{\n  display:inline-flex;\n  align-items:center;\n  min-height:24px;\n  max-width:100%;\n  min-width:0;\n  flex:0 1 auto;\n  background:var(--chip-tour-bg);\n  border:1px solid var(--chip-tour-bd);\n  color:var(--chip-tour-tx);\n  padding:4px 8px;\n  border-radius:var(--radius-pill);\n  font-weight:800;\n  font-size:10px;\n  cursor:pointer;\n  line-height:1.2;\n  letter-spacing:.12px;\n  box-shadow:none;\n}',
            '.tour-btn{\n  display:inline-flex;\n  align-items:center;\n  min-height:22px;\n  max-width:100%;\n  min-width:0;\n  flex:0 1 auto;\n  background:var(--chip-tour-bg);\n  border:1px solid var(--chip-tour-bd);\n  color:var(--chip-tour-tx);\n  padding:3px 8px;\n  border-radius:var(--radius-pill);\n  font-weight:800;\n  font-size:10px;\n  cursor:pointer;\n  line-height:1.15;\n  letter-spacing:.12px;\n  box-shadow:none;\n}'
        ),
        (
            '.phone-col{display:grid; grid-template-columns:1fr; gap:6px; align-content:start; min-height:108px; min-width:0}',
            '.phone-col{display:grid; grid-template-columns:1fr; gap:5px; align-content:start; min-height:92px; min-width:0}'
        ),
    ]
    for old, new in replacements:
        template = template.replace(old, new)
    return template




def _patch_suche_template_aufklappbare_hinweise(template: str) -> str:
    # v16: Mehr-Buttons in der oberen Übersicht entfernt
    """Macht die Hinweise in der oberen Tour-Übersicht sichtbar aufklappbar."""
    original_css = """.ts-stack{
  min-height:46px;
  display:flex;
  flex-direction:column;
  justify-content:flex-start;
  gap:4px;
  width:100%;
}
.ts-main{
  font-weight:750;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.ts-main.name{
  font-weight:850;
  font-size:10.5px;
}
.ts-sub{
  min-height:21px;
  font-size:8.5px;
  font-weight:550;
  font-style:italic;
  color:#8b7355;
  line-height:1.3;
  white-space:normal;
  overflow:hidden;
  display:-webkit-box;
  -webkit-box-orient:vertical;
  -webkit-line-clamp:2;
}
.ts-sub.empty{
  visibility:hidden;
}
.tour-row{ cursor:pointer; }
.tour-row:hover td{ background:#eff6ff; }"""
    enhanced_css = """.ts-stack{
  min-height:46px;
  display:flex;
  flex-direction:column;
  justify-content:flex-start;
  gap:4px;
  width:100%;
}
.ts-main{
  font-weight:750;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.ts-main.name{
  font-weight:850;
  font-size:10.5px;
}
.ts-note-wrap{
  display:flex;
  flex-direction:column;
  align-items:flex-start;
  gap:3px;
  width:100%;
  min-width:0;
  padding-right:0;
}
.ts-sub{
  min-height:14px;
  width:100%;
  font-size:8.5px;
  font-weight:550;
  font-style:italic;
  color:#8b7355;
  line-height:1.25;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  display:block;
  cursor:default;
}
.ts-note-toggle{
  display:none !important;
}
.ts-note-wrap.empty{
  padding-right:0;
}
.ts-sub.empty{
  visibility:hidden;
}
.tour-row{ cursor:pointer; }
.tour-row:hover td{ background:#eff6ff; }
@media print{
  .ts-note-toggle{ display:none !important; }
  .ts-note-wrap{ padding-right:0 !important; }
  .ts-sub{
    white-space:normal !important;
    overflow:visible !important;
    text-overflow:clip !important;
    display:block !important;
  }
}"""

    template = template.replace(original_css, enhanced_css)
    template = template.replace(""".ts-note-wrap{
  display:flex;
  flex-direction:column;
  align-items:flex-start;
  gap:3px;
  width:100%;
  min-width:0;
}""", """.ts-note-wrap{
  display:flex;
  flex-direction:column;
  align-items:flex-start;
  gap:3px;
  width:100%;
  min-width:0;
  padding-right:0;
}""")
    template = template.replace(""".ts-sub{
  min-height:14px;
  width:100%;
  font-size:8.5px;
  font-weight:550;
  font-style:italic;
  color:#8b7355;
  line-height:1.25;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  display:block;
}""", """.ts-sub{
  min-height:14px;
  width:100%;
  font-size:8.5px;
  font-weight:550;
  font-style:italic;
  color:#8b7355;
  line-height:1.25;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  display:block;
}
""")
    template = template.replace(""".ts-note-toggle{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-height:18px;
  padding:1px 7px;
  border:1px solid #cfd7e3;
  border-radius:999px;
  background:#ffffff;
  color:#52627c;
  font-size:8px;
  font-weight:800;
  line-height:1;
  cursor:pointer;
}""", """.ts-note-toggle{
  display:none !important;
}""")
    template = template.replace(""".ts-note-wrap.empty .ts-note-toggle{
  display:none;
}""", """.ts-note-wrap.empty{
  padding-right:0;
}
.ts-note-wrap.empty .ts-note-toggle{
  display:none;
}""")
    template = template.replace("""@media print{
  .ts-note-toggle{ display:none !important; }
  .ts-sub{
    white-space:normal !important;
    overflow:visible !important;
    text-overflow:clip !important;
    display:block !important;
  }
}""", """@media print{
  .ts-note-toggle{ display:none !important; }
  .ts-note-wrap{ padding-right:0 !important; }
  .ts-sub{
    white-space:normal !important;
    overflow:visible !important;
    text-overflow:clip !important;
    display:block !important;
  }
}""")

    original_js = """    const td3=document.createElement('td');
    const td3w=document.createElement('div'); td3w.className='ts-stack';
    const td3m=document.createElement('div'); td3m.className='ts-main name'; td3m.textContent=name;
    const td3s=document.createElement('div'); td3s.className='ts-sub';
    const _nz = kundenNotizen[csb];
    const _parts=[];
    if(_nz){
      if(_nz.c) _parts.push(_nz.c);
      if(_nz.d) _parts.push(_nz.d);
    }
    if(_parts.length){
      td3s.textContent=_parts.join(' · ');
    } else {
      td3s.classList.add('empty');
      td3s.textContent='-';
    }
    td3w.append(td3m, td3s);
    td3.appendChild(td3w);

"""
    enhanced_js = """    const td3=document.createElement('td');
    const td3w=document.createElement('div'); td3w.className='ts-stack';
    const td3m=document.createElement('div'); td3m.className='ts-main name'; td3m.textContent=name;
    const td3noteWrap=document.createElement('div'); td3noteWrap.className='ts-note-wrap';
    const td3s=document.createElement('div'); td3s.className='ts-sub';
    const _nz = kundenNotizen[csb];
    const _parts=[];
    if(_nz){
      if(_nz.c) _parts.push(_nz.c);
      if(_nz.d) _parts.push(_nz.d);
    }
    if(_parts.length){
      const _noteText = _parts.join(' · ');
      td3s.textContent = _noteText;
      td3s.title = _noteText;
    } else {
      td3noteWrap.classList.add('empty');
      td3s.classList.add('empty');
      td3s.textContent='-';
    }
    td3noteWrap.append(td3s);
    td3w.append(td3m, td3noteWrap);
    td3.appendChild(td3w);

"""
    template = template.replace(original_js, enhanced_js)
    template = template.replace("""      td3s.textContent = _noteText;
      td3s.title = _noteText;
      td3btn.addEventListener('click', (ev)=>{
        ev.preventDefault();
        ev.stopPropagation();
        const expanded = td3noteWrap.classList.toggle('expanded');
        td3btn.textContent = expanded ? 'Weniger' : 'Mehr';
      });
""", """      td3s.textContent = _noteText;
      td3s.title = _noteText;
""")
    return template




def _patch_suche_template_tour_summary_collapsible(template: str) -> str:
    template = template.replace(""".tour-summary-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  padding:6px 8px;
  border-bottom:1px solid var(--grid);
  background:linear-gradient(180deg,#ffffff 0%, #f7f9fe 100%);
}""", """.tour-summary-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  padding:6px 8px;
  border-bottom:1px solid var(--grid);
  background:linear-gradient(180deg,#ffffff 0%, #f7f9fe 100%);
}
.tour-summary-head-main{
  display:flex;
  align-items:center;
  gap:8px;
  min-width:0;
  flex:1 1 auto;
  cursor:pointer;
}
.tour-summary-head-main:hover .tour-summary-title{
  color:#1d4ed8;
}
.tour-summary.collapsed .tour-summary-head{
  border-bottom:none;
}
.tour-summary.collapsed .tour-summary-tablewrap{
  display:none;
}
.tour-summary.collapsed #btnCopyTour,
.tour-summary.collapsed #btnPrintTour{
  display:none;
}""")

    template = template.replace(""".tour-summary-actions{ display:flex; align-items:center; gap:6px; }
.print-btn{""", """.tour-summary-actions{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
.summary-toggle-btn{
  min-width:128px;
  background:linear-gradient(180deg,#ffe27a 0%,#cbd5e1 100%) !important;
  border-color:#d4a514 !important;
  color:#5f4200 !important;
  box-shadow:0 1px 0 rgba(255,255,255,.55) inset, 0 1px 2px rgba(15,23,42,.08) !important;
}
.summary-toggle-btn:hover{
  background:linear-gradient(180deg,#ffea97 0%,#f6d458 100%) !important;
  border-color:#c99700 !important;
  color:#4d3400 !important;
}
.summary-toggle-btn:active{
  background:linear-gradient(180deg,#efc845 0%,#e2b62f 100%) !important;
}
.print-btn{""")

    template = template.replace("""  .tour-summary-head{
    border:none !important;
    background:#fff !important;
    padding:0 0 6mm 0 !important;
  }

  .print-btn{ display:none !important; }
""", """  .tour-summary-head{
    border:none !important;
    background:#fff !important;
    padding:0 0 6mm 0 !important;
  }
  .tour-summary-head-main{
    cursor:default !important;
  }
  #tourSummary.collapsed .tour-summary-tablewrap{
    display:block !important;
  }

  .print-btn{ display:none !important; }
""")

    template = template.replace("""        <div class="tour-summary-head">
          <div>
            <div class="tour-summary-title" id="tourSummaryTitle"></div>
            <div class="tour-summary-meta" id="tourSummaryMeta"></div>
          </div>
          <div class="tour-summary-actions">
            <button class="print-btn" id="btnCopyTour" title="Tour als Tabelle (Outlook) kopieren">Kopieren</button>
            <button class="print-btn" id="btnPrintTour" title="Tour-Übersicht drucken (A4)">Drucken</button>
          </div>
        </div>
""", """        <div class="tour-summary-head">
          <div class="tour-summary-head-main" id="tourSummaryHeadMain" title="Übersicht anzeigen oder ausblenden">
            <div>
              <div class="tour-summary-title" id="tourSummaryTitle"></div>
              <div class="tour-summary-meta" id="tourSummaryMeta"></div>
            </div>
          </div>
          <div class="tour-summary-actions">
            <button class="print-btn summary-toggle-btn" id="btnToggleTourSummary" type="button" aria-expanded="false" title="Übersicht anzeigen oder ausblenden">Übersicht anzeigen</button>
            <button class="print-btn" id="btnCopyTour" title="Tour als Tabelle (Outlook) kopieren">Kopieren</button>
            <button class="print-btn" id="btnPrintTour" title="Tour-Übersicht drucken (A4)">Drucken</button>
          </div>
        </div>
""")

    template = template.replace("""function closeTourSummary(){
  $('#tourSummary').style.display='none';
  $('#tourSummaryTitle').textContent='';
  $('#tourSummaryMeta').textContent='';
  $('#tourSummaryBody').innerHTML='';
}
""", """function setTourSummaryCollapsed(collapsed){
  const wrap = $('#tourSummary');
  if(!wrap) return;
  wrap.classList.toggle('collapsed', !!collapsed);
  const btn = $('#btnToggleTourSummary');
  if(btn){
    btn.textContent = collapsed ? 'Übersicht anzeigen' : 'Übersicht ausblenden';
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}
function toggleTourSummary(ev){
  if(ev){
    ev.preventDefault();
    ev.stopPropagation();
  }
  const wrap = $('#tourSummary');
  if(!wrap || wrap.style.display==='none') return;
  setTourSummaryCollapsed(!wrap.classList.contains('collapsed'));
}
function closeTourSummary(){
  $('#tourSummary').style.display='none';
  $('#tourSummaryTitle').textContent='';
  $('#tourSummaryMeta').textContent='';
  $('#tourSummaryBody').innerHTML='';
  setTourSummaryCollapsed(true);
}
""")

    template = template.replace("""  wrap.style.display='block';
}
""", """  wrap.style.display='block';
  setTourSummaryCollapsed(true);
}
""")

    template = template.replace("""$('#btnCopyTour').addEventListener('click', onCopyTour);

$('#btnPrintTour').addEventListener('click', ()=>{
  if($('#tourSummary').style.display==='none'){ return; }
  window.print();
});
""", """$('#btnCopyTour').addEventListener('click', onCopyTour);
$('#btnToggleTourSummary').addEventListener('click', toggleTourSummary);
$('#tourSummaryHeadMain').addEventListener('click', toggleTourSummary);
$('#tourSummaryHeadMain').addEventListener('keydown', (ev)=>{
  if(ev.key === 'Enter' || ev.key === ' '){
    toggleTourSummary(ev);
  }
});
$('#tourSummaryHeadMain').tabIndex = 0;

$('#btnPrintTour').addEventListener('click', ()=>{
  if($('#tourSummary').style.display==='none'){ return; }
  setTourSummaryCollapsed(false);
  window.print();
});
""")
    return template




def _patch_suche_template_tour_summary_collapsible_fix(template: str) -> str:
    """Macht den Aufklapper der oberen Übersicht robust und standardmäßig zugeklappt."""
    if '.tour-summary.collapsed .tour-summary-tablewrap{display:none !important;}' not in template:
        template = template.replace(
            '</style>',
            '.tour-summary.collapsed .tour-summary-tablewrap{display:none !important;}\n'
            '.tour-summary.collapsed #btnCopyTour,\n'
            '.tour-summary.collapsed #btnPrintTour{display:none !important;}\n'
            '.summary-toggle-btn{min-width:156px;font-weight:900;background:linear-gradient(180deg,#ffe27a 0%,#cbd5e1 100%) !important;border-color:#d4a514 !important;color:#5f4200 !important;box-shadow:0 1px 0 rgba(255,255,255,.55) inset, 0 1px 2px rgba(15,23,42,.08) !important;}\n'
            '.summary-toggle-btn:hover{background:linear-gradient(180deg,#ffea97 0%,#f6d458 100%) !important;border-color:#c99700 !important;color:#4d3400 !important;}\n'
            '.summary-toggle-btn:active{background:linear-gradient(180deg,#efc845 0%,#e2b62f 100%) !important;}\n'
            '.tour-summary-head-main{cursor:pointer;}\n'
            '</style>',
            1,
        )

    if 'id="tourSummaryTableWrap"' not in template:
        template = template.replace(
            '<div class="tour-summary-tablewrap">',
            '<div class="tour-summary-tablewrap" id="tourSummaryTableWrap">',
            1,
        )

    if 'id="btnToggleTourSummary"' not in template:
        template = template.replace(
            '<div class="tour-summary-actions">',
            '<div class="tour-summary-actions">\n'
            '            <button class="print-btn summary-toggle-btn" id="btnToggleTourSummary" type="button" '
            'onclick="toggleTourSummary(event); return false;" aria-expanded="false" '
            'aria-controls="tourSummaryTableWrap" title="Übersicht anzeigen oder ausblenden">Übersicht anzeigen</button>',
            1,
        )

    template = template.replace(
        'id="btnToggleTourSummary" type="button"',
        'id="btnToggleTourSummary" type="button" onclick="toggleTourSummary(event); return false;" aria-controls="tourSummaryTableWrap"',
    )
    template = template.replace(
        'id="tourSummaryHeadMain" title="Übersicht anzeigen oder ausblenden"',
        'id="tourSummaryHeadMain" title="Übersicht anzeigen oder ausblenden" onclick="toggleTourSummary(event)" role="button" aria-controls="tourSummaryTableWrap"',
    )

    template = re.sub(
        r"wrap\.style\.display='block';\\n(\s*setTourSummaryCollapsed\(true\);\\n)?",
        "wrap.style.display='block';\\n  ensureTourSummaryToggleBindings();\\n  setTourSummaryCollapsed(true);\\n",
        template,
        count=1,
    )

    template = template.replace(
        "$('#btnCopyTour').addEventListener('click', onCopyTour);",
        "ensureTourSummaryToggleBindings();\\n$('#btnCopyTour').addEventListener('click', onCopyTour);",
        1,
    )

    template = template.replace(
        "$('#btnPrintTour').addEventListener('click', ()=>{\\n  if($('#tourSummary').style.display==='none'){ return; }\\n  window.print();\\n});",
        "$('#btnPrintTour').addEventListener('click', ()=>{\\n  const wrap = $('#tourSummary');\\n  if(!wrap || getComputedStyle(wrap).display==='none'){ return; }\\n  setTourSummaryCollapsed(false);\\n  window.print();\\n});",
        1,
    )

    helper = """
function setTourSummaryCollapsed(collapsed){
  const wrap = $('#tourSummary');
  if(!wrap) return;
  wrap.classList.toggle('collapsed', !!collapsed);
  const btn = $('#btnToggleTourSummary');
  if(btn){
    btn.textContent = collapsed ? 'Übersicht anzeigen' : 'Übersicht ausblenden';
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}
function ensureTourSummaryToggleBindings(){
  const btn = $('#btnToggleTourSummary');
  if(btn && !btn.dataset.toggleBound){
    btn.dataset.toggleBound = '1';
    btn.onclick = function(ev){ toggleTourSummary(ev); return false; };
  }
  const head = $('#tourSummaryHeadMain');
  if(head && !head.dataset.toggleBound){
    head.dataset.toggleBound = '1';
    head.onclick = function(ev){ toggleTourSummary(ev); };
    head.tabIndex = 0;
    head.addEventListener('keydown', function(ev){
      if(ev.key === 'Enter' || ev.key === ' '){
        toggleTourSummary(ev);
      }
    });
  }
}
function toggleTourSummary(ev){
  if(ev){
    ev.preventDefault();
    ev.stopPropagation();
  }
  const wrap = $('#tourSummary');
  if(!wrap || getComputedStyle(wrap).display === 'none') return false;
  setTourSummaryCollapsed(!wrap.classList.contains('collapsed'));
  return false;
}
document.addEventListener('DOMContentLoaded', function(){
  ensureTourSummaryToggleBindings();
  setTourSummaryCollapsed(true);
});
"""
    if helper not in template:
        template = template.replace('</script>', helper + '\n</script>', 1)

    template = template.replace(
        "ensureTourSummaryToggleBindings();\\n$('#btnCopyTour').addEventListener('click', onCopyTour);",
        "ensureTourSummaryToggleBindings();\n$('#btnCopyTour').addEventListener('click', onCopyTour);"
    )
    template = template.replace(
        "$('#btnPrintTour').addEventListener('click', ()=>{\n  if($('#tourSummary').style.display==='none'){ return; }\n  window.print();\n});",
        "$('#btnPrintTour').addEventListener('click', ()=>{\n  const wrap = $('#tourSummary');\n  if(!wrap || getComputedStyle(wrap).display==='none'){ return; }\n  setTourSummaryCollapsed(false);\n  window.print();\n});",
        1,
    )

    return template





def _patch_suche_template_search_all_inputs(template: str) -> str:
    """Die Suche soll bei jeder Eingabe alle Felder durchsuchen,
    auch bei kurzen oder rein numerischen Werten wie 3-, 4- oder 5-stelligen Eingaben."""
    old = """function onSmart(){
  const qRaw=$('#smartSearch').value.trim();
  closeTourSummary();

  if(!qRaw){ renderTable([]); return; }

  if(/^\\d{1,3}$/.test(qRaw)){
    const n=qRaw.replace(/^0+(\\d)/,'$1');
    const r=allCustomers.filter(k=>(k.touren||[]).some(t=>(t.tournummer||'').startsWith(n)));
    renderTable(r);
    return;
  }

  if(/^\\d{4}$/.test(qRaw)){
    const n=qRaw.replace(/^0+(\\d)/,'$1');
    const tr=allCustomers.filter(k=>(k.touren||[]).some(t=>(t.tournummer||'')===n));
    const cr=allCustomers.filter(k=>(k.csb_nummer||'')===n);
    const r=dedupByCSB([...tr,...cr]);

    if(tr.length){
      renderTourSummary(tr, n);
    }
    renderTable(r);
    return;
  }

  const q=normDE(qRaw);
  const r=allCustomers.filter(k=>{
    const fb=k.fachberater||'';
    const text=(k.name+' '+k.strasse+' '+k.ort+' '+k.csb_nummer+' '+k.sap_nummer+' '+fb+' '+(k.schluessel||'')+' '+(k.fb_phone||'')+' '+(k.market_phone||'')+' '+(k.market_email||'')+' '+((kundenNotizen[k.csb_nummer]||{}).c||'')+' '+((kundenNotizen[k.csb_nummer]||{}).d||''));
    return normDE(text).includes(q);
  });
  renderTable(r);
}"""

    new = """function onSmart(){
  const qRaw=$('#smartSearch').value.trim();
  closeTourSummary();

  if(!qRaw){ renderTable([]); return; }

  const q = normDE(qRaw);
  const n = normalizeDigits(qRaw);
  const isNumeric = /^\\d+$/.test(qRaw);
  const normalizeRahmentourCode = (value) => String(value||'').toUpperCase().replace(/\\s+/g,'');
  const getRahmentourList = (tournummer, dayLabel) => {
    const key = normalizeDigits(tournummer);
    const raw = rahmentourIndex[key];
    const day = String(dayLabel||'').trim();
    const rows = Array.isArray(raw) ? raw : (raw ? [raw] : []);
    const exact = rows
      .filter(item => item && typeof item === 'object' && String(item.day||'').trim() === day)
      .map(item => normalizeRahmentourCode(item.sap))
      .filter(Boolean);
    const fallback = rows
      .map(item => {
        if(item && typeof item === 'object') return normalizeRahmentourCode(item.sap);
        return normalizeRahmentourCode(item);
      })
      .filter(Boolean);
    return (exact.length ? exact : fallback).filter((v, i, a) => a.indexOf(v) === i);
  };
  const rahmenQuery = normalizeRahmentourCode(qRaw);

  let r;

  if(isNumeric && n){
    // Strict numeric search: match only numbers with same digit count
    const nLen = n.length;
    r = allCustomers.filter(k => {
      if(normalizeDigits(k.csb_nummer) === n) return true;
      if(normalizeDigits(k.sap_nummer) === n) return true;
      if(normalizeDigits(k.schluessel) === n) return true;
      return (k.touren||[]).some(t => {
        const tn = normalizeDigits(t.tournummer);
        return tn && tn.length === nLen && tn === n;
      });
    });

    // Show tour summary for exact tour matches
    const tr = allCustomers.filter(k => (k.touren||[]).some(t => normalizeDigits(t.tournummer) === n));
    if(tr.length) renderTourSummary(tr, n);
    r = dedupByCSB(tr.length ? [...tr, ...r] : r);
  } else {
    // Text search: broad matching
    r = allCustomers.filter(k=>{
      const fb = k.fachberater || '';
      const tourText = (k.touren||[]).map(t => {
        const tourNum = t.tournummer || '';
        return [tourNum, t.liefertag || ''].filter(Boolean).join(' ');
      }).join(' ');
      const text = (
        (k.name||'') + ' ' +
        (k.strasse||'') + ' ' +
        (k.ort||'') + ' ' +
        (k.csb_nummer||'') + ' ' +
        (k.sap_nummer||'') + ' ' +
        fb + ' ' +
        (k.schluessel||'') + ' ' +
        (k.fb_phone||'') + ' ' +
        (k.market_phone||'') + ' ' +
        (k.market_email||'') + ' ' +
        ((kundenNotizen[k.csb_nummer]||{}).c||'') + ' ' +
        ((kundenNotizen[k.csb_nummer]||{}).d||'') + ' ' +
        tourText
      );
      return normDE(text).includes(q);
    });

    if(rahmenQuery){
      const rr = allCustomers.filter(k => (k.touren||[]).some(t => getRahmentourList(t.tournummer, t.liefertag).some(rahmen => rahmen === rahmenQuery)));
      if(rr.length){
        renderTourSummary(rr, rahmenQuery);
        r = dedupByCSB([...rr, ...r]);
      }
    }
  }

  renderTable(dedupByCSB(r));
}"""

    if old in template:
        return template.replace(old, new, 1)
    return template




def _patch_suche_template_kisoft_rahmentour(template: str) -> str:
    """Blendet die Kisoft-Rahmentour in der Tour-Uebersicht sichtbar ein."""
    template = template.replace(
        ".tour-summary-meta{ display:none !important; }",
        ".tour-summary-meta{display:block !important;font-size:11px;font-weight:850;color:#7c5b00;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}"
    )

    template = template.replace(
        """  // Wochentag(e) für diese Tour
  const daySet = new Set();
  for(const k of list){
    for(const t of (k.touren||[])){
      if(String(t.tournummer||'').trim() === String(tour).trim()){
        if(t.liefertag) daySet.add(String(t.liefertag).trim());
      }
    }
  }
  const dayOrder = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"];
  const days = Array.from(daySet).sort((a,b)=>dayOrder.indexOf(a)-dayOrder.indexOf(b));
  const dayLabel = days.length ? days.join("/") : "";

  $('#tourSummaryTitle').textContent = dayLabel ? `${tour} – ${dayLabel}` : `${tour}`;
  $('#tourSummaryMeta').textContent  = "";
""",
        """  const normalizeRahmentourCode = (value) => String(value||'').toUpperCase().replace(/\\s+/g,'');
  const queryTourDigits = normalizeDigits(tour);
  const queryRahmen = normalizeRahmentourCode(tour);
  const getRahmentourList = (tournummer, dayLabel) => {
    const tourNum = normalizeDigits(tournummer);
    const raw = rahmentourIndex[tourNum];
    const day = String(dayLabel||'').trim();
    const rows = Array.isArray(raw) ? raw : (raw ? [raw] : []);
    const exact = rows
      .filter(item => item && typeof item === 'object' && String(item.day||'').trim() === day)
      .map(item => normalizeRahmentourCode(item.sap))
      .filter(Boolean);
    const fallback = rows
      .map(item => {
        if(item && typeof item === 'object') return normalizeRahmentourCode(item.sap);
        return normalizeRahmentourCode(item);
      })
      .filter(Boolean);
    return (exact.length ? exact : fallback).filter((v, i, a) => a.indexOf(v) === i);
  };
  const matchesTour = (t) => {
    const tourNum = normalizeDigits(t && t.tournummer);
    const rahmenListe = getRahmentourList(t && t.tournummer, t && t.liefertag);
    if(queryTourDigits && tourNum === queryTourDigits) return true;
    if(queryRahmen && rahmenListe.includes(queryRahmen)) return true;
    return false;
  };
  const extractTournummer = (k) => {
    for(const t of (k.touren||[])){
      if(matchesTour(t)) return normalizeDigits(t.tournummer) || String(t.tournummer||'').trim();
    }
    return '';
  };

  const summaryTournummer = list.map(extractTournummer).find(Boolean) || normalizeDigits(tour) || String(tour||'').trim();

  // Wochentag(e) für diese Tour oder Rahmentour
  const daySet = new Set();
  for(const k of list){
    for(const t of (k.touren||[])){
      if(matchesTour(t)){
        if(t.liefertag) daySet.add(String(t.liefertag).trim());
      }
    }
  }
  const dayOrder = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"];
  const days = Array.from(daySet).sort((a,b)=>dayOrder.indexOf(a)-dayOrder.indexOf(b));
  const dayLabel = days.length ? days.join("/") : "";

  const summaryRahmentourSet = new Set();
  for(const k of list){
    for(const t of (k.touren||[])){
      if(matchesTour(t)){
        getRahmentourList(t.tournummer, t.liefertag).forEach(v => summaryRahmentourSet.add(v));
      }
    }
  }
  if(!summaryRahmentourSet.size){
    getRahmentourList(summaryTournummer, dayLabel).forEach(v => summaryRahmentourSet.add(v));
  }
  const summaryRahmentour = Array.from(summaryRahmentourSet).join(', ');

  $('#tourSummaryTitle').textContent = dayLabel ? `${summaryTournummer} – ${dayLabel}` : `${summaryTournummer}`;
  $('#tourSummaryMeta').textContent  = summaryRahmentour ? `Kisoft Rahmentouren: ${summaryRahmentour}` : "";
""",
        1,
    )

    template = template.replace(
        "const lfa = (a.lf_map && a.lf_map[tour]) ? a.lf_map[tour] : '';",
        "const lfa = (a.lf_map && a.lf_map[summaryTournummer]) ? a.lf_map[summaryTournummer] : '';",
        1,
    )
    template = template.replace(
        "const lfb = (b.lf_map && b.lf_map[tour]) ? b.lf_map[tour] : '';",
        "const lfb = (b.lf_map && b.lf_map[summaryTournummer]) ? b.lf_map[summaryTournummer] : '';",
        1,
    )
    template = template.replace(
        "const lf   = (k.lf_map && k.lf_map[tour]) ? String(k.lf_map[tour]).trim() : '';",
        "const lf   = (k.lf_map && k.lf_map[summaryTournummer]) ? String(k.lf_map[summaryTournummer]).trim() : '';",
        1,
    )
    return template





def _patch_suche_template_rahmentour_list_in_rows(template: str) -> str:
    """Zeigt in der Ergebnisliste alle Kisoft-Rahmentouren je Tour an."""
    old = """    const _rh = rahmentourIndex[tnum];
    if(_rh){
      const rhSpan = document.createElement('span');
      rhSpan.className = 'rahmen';
      rhSpan.textContent = _rh;
      b.appendChild(rhSpan);
    }
"""
    new = """    const _rhRaw = rahmentourIndex[tnum];
    const _day = String(t.liefertag || '').trim();
    const _rhRows = Array.isArray(_rhRaw) ? _rhRaw : (_rhRaw ? [_rhRaw] : []);
    const _rhExact = _rhRows
      .filter(item => item && typeof item === 'object' && String(item.day || '').trim() === _day)
      .map(item => String(item.sap || '').trim())
      .filter(Boolean);
    const _rhFallback = _rhRows
      .map(item => item && typeof item === 'object' ? String(item.sap || '').trim() : String(item || '').trim())
      .filter(Boolean);
    const _rhList = (_rhExact.length ? _rhExact : _rhFallback).filter((v, i, a) => a.indexOf(v) === i);
"""
    if old in template:
        return template.replace(old, new, 1)
    return template





def _patch_suche_template_sonderliste_marktkauf(template: str) -> str:
    """Sends Kunden-Liste data to parent via postMessage for standalone panel."""

    # Add JS helper functions and postMessage sender after allCustomers is built
    template = template.replace(
        "  allCustomers = Array.from(map.values());\n}",
        """  allCustomers = Array.from(map.values());
}

const KUNDEN_LISTE_GROUP_ORDER = ['SuL','Malchow','Neumünster','Direkt','Marktkauf','Gemischt','Ohne Rahmentour'];

function normalizeRahmentourCodeGlobal(value){
  return String(value||'').toUpperCase().replace(/\\s+/g,'').trim();
}
function getRahmentourListGlobal(tournummer, dayLabel){
  const key = normalizeDigits(tournummer);
  const raw = rahmentourIndex[key];
  const day = String(dayLabel||'').trim();
  const rows = Array.isArray(raw) ? raw : (raw ? [raw] : []);
  const exact = rows
    .filter(item => item && typeof item === 'object' && String(item.day||'').trim() === day)
    .map(item => normalizeRahmentourCodeGlobal(item.sap))
    .filter(Boolean);
  const fallback = rows
    .map(item => item && typeof item === 'object' ? normalizeRahmentourCodeGlobal(item.sap) : normalizeRahmentourCodeGlobal(item))
    .filter(Boolean);
  return (exact.length ? exact : fallback).filter((v, i, a) => a.indexOf(v) === i);
}
function getCustomerRahmentourCodes(k){
  const out = [];
  (k.touren||[]).forEach(t => {
    getRahmentourListGlobal(t.tournummer, t.liefertag).forEach(code => {
      if(code && out.indexOf(code) === -1) out.push(code);
    });
  });
  return out;
}
function classifyKundenCodes(codes){
  if(!codes.length) return 'Ohne Rahmentour';
  const hasZ = codes.some(code => code.includes('Z'));
  const hasM = codes.some(code => code.includes('M'));
  const hasN = codes.some(code => code.includes('N'));
  const count = (hasZ?1:0) + (hasM?1:0) + (hasN?1:0);
  if(count > 1) return 'Gemischt';
  if(hasZ) return 'SuL';
  if(hasM) return 'Malchow';
  if(hasN) return 'Neumünster';
  return 'Direkt';
}

function hasMarktkaufTour(k){
  return (k.touren||[]).some(t => {
    const num = String(t.tournummer||'').replace(/\\D/g,'');
    return /^\\d88\\d$/.test(num);
  });
}
function hasMalchowTour(k){
  return (k.touren||[]).some(t => {
    const num = String(t.tournummer||'').replace(/\\D/g,'');
    return /^\\d777\\d$/.test(num);
  });
}
function hasNMSTour(k){
  return (k.touren||[]).some(t => {
    const num = String(t.tournummer||'').replace(/\\D/g,'');
    return /^\\d222\\d$/.test(num);
  });
}

function sendKundenDataToParent(){
  if(!allCustomers || !allCustomers.length) return;
  const groups = {};
  KUNDEN_LISTE_GROUP_ORDER.forEach(g => { groups[g] = []; });
  const ordered = [...allCustomers].sort((a, b) => {
    const c = String(a.name||'').localeCompare(String(b.name||''), 'de');
    return c !== 0 ? c : String(a.sap_nummer||'').localeCompare(String(b.sap_nummer||''), 'de');
  });
  ordered.forEach(k => {
    const codes = getCustomerRahmentourCodes(k);
    let group = classifyKundenCodes(codes);
    if(hasMarktkaufTour(k)) group = 'Marktkauf';
    else if(hasMalchowTour(k) && (group === 'Direkt' || group === 'Ohne Rahmentour')) group = 'Malchow';
    else if(hasNMSTour(k) && (group === 'Direkt' || group === 'Ohne Rahmentour')) group = 'Neumünster';
    const touren = (k.touren||[])
      .map(t => {
        const num = normalizeDigits(t.tournummer) || String(t.tournummer||'').trim();
        const day = String(t.liefertag||'').trim();
        return day ? (num + ' (' + day + ')') : num;
      })
      .filter(Boolean)
      .filter((v, i, a) => a.indexOf(v) === i)
      .join(', ');
    if(!groups[group]) groups[group] = [];
    groups[group].push({
      sap: normalizeDigits(k.sap_nummer) || '',
      csb: normalizeDigits(k.csb_nummer) || '',
      name: k.name || '-',
      ort: [k.postleitzahl||'', k.ort||''].filter(Boolean).join(' '),
      bereich: k.bereich || '-',
      touren: touren || '-',
      rahmentouren: codes.length ? codes.join(', ') : '-'
    });
  });
  try {
    window.parent.postMessage({type:'kunden-liste-data', groups: groups, order: KUNDEN_LISTE_GROUP_ORDER}, '*');
  } catch(e){}
}
// Auto-send after a short delay to let iframe fully initialize
setTimeout(sendKundenDataToParent, 500);
window.onKundenListe = function(){ sendKundenDataToParent(); };
window.onMkListe = window.onKundenListe;
""",
        1,
    )

    return template



def _patch_suche_template_perf_searchindex(template: str) -> str:
    """Vor-normalisierter Suchindex pro Kunde + DocumentFragment-Rendering.

    Spart O(n) Stringkonkatenation + normDE() pro Tastendruck (#5),
    und O(n) DOM-Reflows beim Tabellen-Render (#6).
    """
    # 1) buildData: am Ende _search pro Kunde befuellen
    needle_build = "  allCustomers = Array.from(map.values());\n}"
    repl_build = (
        "  allCustomers = Array.from(map.values());\n"
        "  // Vor-normalisierter Suchindex pro Kunde — vermeidet Konkatenation pro Tastendruck\n"
        "  for(const c of allCustomers){\n"
        "    const csb = c.csb_nummer || '';\n"
        "    const noteC = (kundenNotizen[csb] && kundenNotizen[csb].c) || '';\n"
        "    const noteD = (kundenNotizen[csb] && kundenNotizen[csb].d) || '';\n"
        "    const tourText = (c.touren||[]).map(t => ((t.tournummer||'') + ' ' + (t.liefertag||''))).join(' ');\n"
        "    c._search = normDE([\n"
        "      c.name||'', c.strasse||'', c.ort||'', csb,\n"
        "      c.sap_nummer||'', c.fachberater||'', c.schluessel||'',\n"
        "      c.fb_phone||'', c.market_phone||'', c.market_email||'',\n"
        "      noteC, noteD, tourText\n"
        "    ].join(' '));\n"
        "  }\n"
        "}"
    )
    if needle_build in template:
        template = template.replace(needle_build, repl_build, 1)

    # 2) onSmart Text-Branch: lange Konkatenation durch k._search ersetzen
    # Wir suchen das gesamte "const text = ( ... );  return normDE(text).includes(q);"
    pattern_textsearch = re.compile(
        r"const text = \(\s*\n"
        r"(?:[^;]+\n)+?"
        r"\s*\);\s*\n"
        r"\s*return normDE\(text\)\.includes\(q\);",
        re.MULTILINE
    )
    template = pattern_textsearch.sub(
        "return (k._search || '').includes(q);",
        template,
        count=1
    )

    # 3) renderTable: DocumentFragment statt einzelnem appendChild
    needle_rt = (
        "  body.innerHTML='';\n"
        "  if(list.length){\n"
        "    list.forEach(k=>body.appendChild(rowFor(k)));\n"
        "    tbl.style.display='table';"
    )
    repl_rt = (
        "  body.innerHTML='';\n"
        "  if(list.length){\n"
        "    const frag = document.createDocumentFragment();\n"
        "    list.forEach(k=>frag.appendChild(rowFor(k)));\n"
        "    body.appendChild(frag);\n"
        "    tbl.style.display='table';"
    )
    if needle_rt in template:
        template = template.replace(needle_rt, repl_rt, 1)

    return template




def _patch_suche_template_multi_keys(template: str) -> str:
    """Unterstuetzt mehrere Schluesselnummern je Kundennummer."""
    normalize_needle = """function normalizeDigits(v){
  if(v == null) return '';
  let s = String(v).trim().replace(/\\.0$/,'');
  s = s.replace(/[^0-9]/g,'').replace(/^0+(\\d)/,'$1');
  return s;
}"""
    normalize_repl = normalize_needle + """
function normalizeKeyList(value){
  const source = Array.isArray(value)
    ? value
    : String(value == null ? '' : value).split(/[\\s,;|/]+/);
  const out = [];
  for(const item of source){
    const key = normalizeDigits(item);
    if(key && !out.includes(key)) out.push(key);
  }
  return out;
}
function customerKeys(customer){
  const direct = normalizeKeyList(customer && customer.schluessel);
  if(direct.length) return direct;
  const csb = customer && customer.csb_nummer ? customer.csb_nummer : '';
  return normalizeKeyList(keyIndex[csb]);
}
function customerKeyText(customer){
  return customerKeys(customer).join(' / ');
}"""
    if normalize_needle in template:
        template = template.replace(normalize_needle, normalize_repl, 1)

    template = template.replace(
        "rec.schluessel   = normalizeDigits(rec.schluessel) || (keyIndex[csb]||'');",
        "rec.schluessel   = normalizeKeyList(rec.schluessel).length ? normalizeKeyList(rec.schluessel) : normalizeKeyList(keyIndex[csb]);",
        1,
    )

    template = template.replace(
        "if(normalizeDigits(k.schluessel) === n) return true;",
        "if(customerKeys(k).includes(n)) return true;",
        1,
    )

    template = template.replace(
        "c.sap_nummer||'', c.fachberater||'', c.schluessel||'',",
        "c.sap_nummer||'', c.fachberater||'', customerKeyText(c),",
        1,
    )

    old_row = """  const key=(k.schluessel||'')||(keyIndex[csb]||'');
  keySlot.appendChild(key ? el('span','badge-key',key) : makePlaceholder('Kein Schlüssel'));"""
    new_row = """  const keys = customerKeys(k);
  if(keys.length){
    keys.forEach(key => keySlot.appendChild(el('span','badge-key',key)));
  } else {
    keySlot.appendChild(makePlaceholder('Kein Schlüssel'));
  }"""
    if old_row in template:
        template = template.replace(old_row, new_row, 1)

    old_key_search = """  const r=[];
  for(const k of allCustomers){
    const key=(k.schluessel||'')||(keyIndex[k.csb_nummer]||'');
    if(key===n) r.push(k);
  }"""
    new_key_search = """  const r=[];
  for(const k of allCustomers){
    if(customerKeys(k).includes(n)) r.push(k);
  }"""
    if old_key_search in template:
        template = template.replace(old_key_search, new_key_search, 1)

    return template

def _patch_suche_template_optik(template: str) -> str:
    """Kleinere Optik-Korrekturen (#8 Webfont, #10 Body-Weight)."""
    # #8: Webfont-Cocktail abspecken — nur Inter Tight (3 Weights) + JetBrains Mono (1 Weight)
    old_fonts = (
        '<link href="https://fonts.googleapis.com/css2?'
        'family=Inter:wght@500;600;700;800;900'
        '&family=Inter+Tight:wght@500;600;700;800;900'
        '&family=JetBrains+Mono:wght@500;600;700&display=swap" rel="stylesheet">'
    )
    new_fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?'
        'family=Inter+Tight:wght@600;700;900'
        '&family=JetBrains+Mono:wght@600&display=swap" rel="stylesheet">'
    )
    if old_fonts in template:
        template = template.replace(old_fonts, new_fonts, 1)

    # #10: body font-weight 650 → 600 (lesbarer, ruhiger) — auch ohne Semikolon
    template = re.sub(r"font-weight\s*:\s*650\b", "font-weight:600", template)

    return template



def _patch_druck_template_cleanup(template: str) -> str:
    """Entfernt Debug-console.log-Spam aus dem Druck-Template (#7).

    Im Live-Einsatz unnoetig und unprofessionell.
    """
    # Alle console.log / console.warn / console.error Statements entfernen
    template = re.sub(
        r"^\s*console\.(?:log|warn|error|info|debug)\([^;]*\);?\s*\n",
        "",
        template,
        flags=re.MULTILINE,
    )
    return template

DRUCK_HTML_TEMPLATE_PATCHES = (_patch_druck_template_cleanup,)


@st.cache_resource(show_spinner=False)

def _patch_suche_template_kundenart_absetzer_rampe(template: str) -> str:
    """Zeigt je Kunde eine SAP-basierte Kennzeichnung Absetzer/Rampenkunde in der Suche."""
    # Daten-Konstante einfuegen
    template = template.replace(
        "const rahmentourIndex  = {  };",
        "const rahmentourIndex  = {  };\nconst kundenArtIndex    = {  };",
        1,
    )

    # Kleine Badge-Optik fuer die Ergebnisliste
    css = """
.kundenart-chip{
  display:inline-flex;
  align-items:center;
  min-height:24px;
  max-width:100%;
  padding:4px 8px;
  border-radius:5px;
  border:1px solid #8fb7ff;
  background:#eef6ff;
  color:#1d4ed8;
  font-size:10px;
  font-weight:900;
  line-height:1.1;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.kundenart-chip.rampe{
  border-color:#86efac;
  background:#ecfdf5;
  color:#166534;
}
.kundenart-chip.kombi{
  border-color:#fbbf24;
  background:#fffbeb;
  color:#92400e;
}
"""
    if ".kundenart-chip" not in template:
        template = template.replace("</style>", css + "\n</style>", 1)

    helper = """function makeKundenArtChip(value){
  const label = String(value||'').trim();
  if(!label) return null;
  const s = document.createElement('span');
  const n = normDE(label);
  s.className = 'kundenart-chip' + (n.includes('rampe') && n.includes('absetzer') ? ' kombi' : (n.includes('rampe') ? ' rampe' : ''));
  s.textContent = label;
  s.title = 'Kundenart: ' + label;
  return s;
}
"""
    if "function makeKundenArtChip" not in template:
        template = template.replace(
            "function makeEmptyChip(label, value='-'){",
            helper + "\nfunction makeEmptyChip(label, value='-'){",
            1,
        )

    # Kundenart bereits beim Datenaufbau an den Kunden haengen
    template = template.replace(
        "        rec.sap_nummer   = normalizeDigits(rec.sap_nummer);",
        "        rec.sap_nummer   = normalizeDigits(rec.sap_nummer);\n        rec.kunden_art   = kundenArtIndex[rec.sap_nummer] || '';",
        1,
    )

    # In der CSB/SAP-Spalte anzeigen
    template = template.replace(
        "  idSlot1.appendChild(makeIdChip('CSB', csb));\n  idSlot2.appendChild(makeIdChip('SAP', sap));\n  c1.append(idSlot1,idSlot2);",
        "  idSlot1.appendChild(makeIdChip('CSB', csb));\n  idSlot2.appendChild(makeIdChip('SAP', sap));\n  c1.append(idSlot1,idSlot2);\n  const artChip = makeKundenArtChip(k.kunden_art || '');\n  if(artChip){ const artSlot = el('div','cell-slot compact'); artSlot.appendChild(artChip); c1.appendChild(artSlot); }",
        1,
    )

    # Kundenart in den Suchindex aufnehmen, damit Suche nach Absetzer/Rampe funktioniert
    template = template.replace(
        "      noteC, noteD, tourText",
        "      noteC, noteD, c.kunden_art||'', tourText",
        1,
    )
    return template

def _patch_suche_template_enter_search(template: str) -> str:
    """Enter im Suchfeld loest nur die zum fokussierten Feld passende Suche aus.
    Vorher rief der Enter-Handler onSmart() UND onKey() auf – bei leerem Exakt-Feld
    leerte onKey() die gerade gefuellte Trefferliste sofort wieder."""
    old = (
        "    if(e.key === 'Enter'){\n"
        "      const a = document.activeElement;\n"
        "      if(a && (a.id==='smartSearch' || a.id==='keySearch')){\n"
        "        onSmart();\n"
        "        onKey();\n"
        "      }\n"
        "    }"
    )
    new = (
        "    if(e.key === 'Enter'){\n"
        "      const a = document.activeElement;\n"
        "      if(a && a.id==='smartSearch'){ e.preventDefault(); onSmart(); }\n"
        "      else if(a && a.id==='keySearch'){ e.preventDefault(); onKey(); }\n"
        "    }"
    )
    return template.replace(old, new)

def get_suche_template() -> str:
    """Baut das Suche-Template einmalig (nach Re-import gecached).
    Spart bei jedem Streamlit-Re-Run die ~11 String-Patches uebers ~700 KB Template."""
    tpl = base64.b64decode(_static_payload_text("_SUCHE_B64")).decode("utf-8")
    for patch in (
        _patch_suche_template_layout,
        _patch_suche_template_header,
        _patch_suche_template_weniger_luftig,
        _patch_suche_template_aufklappbare_hinweise,
        _patch_suche_template_tour_summary_collapsible,
        _patch_suche_template_tour_summary_collapsible_fix,
        _patch_suche_template_search_all_inputs,
        _patch_suche_template_enter_search,
        _patch_suche_template_kisoft_rahmentour,
        _patch_suche_template_rahmentour_list_in_rows,
        _patch_suche_template_sonderliste_marktkauf,
        _patch_suche_template_perf_searchindex,
        _patch_suche_template_kundenart_absetzer_rampe,
        _patch_suche_template_multi_keys,
        _patch_suche_template_optik,
    ):
        tpl = patch(tpl)
    return tpl


@st.cache_resource(show_spinner=False)
def get_druck_template() -> str:
    """Baut das Druck-Template einmalig (nach Re-import gecached)."""
    tpl = base64.b64decode(_static_payload_text("_DRUCK_B64")).decode("utf-8")
    for patch in DRUCK_HTML_TEMPLATE_PATCHES:
        tpl = patch(tpl)
    return tpl


# =============================================================================
# DRUCK – Konstanten & Hilfsfunktionen
# =============================================================================

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
    if not s:
        return ""
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        return s + " Uhr"
    if re.fullmatch(r"\d{1,2}", s):
        return s.zfill(2) + ":00 Uhr"
    return s


def safe_time(val) -> str:
    raw = norm_val(val)
    if re.fullmatch(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag)", raw):
        return ""
    return normalize_time(val)


def canon_group_id(label: str) -> str:
    s = norm_val(label).lower()
    m = re.search(r"\b(1011|21|41|65|0|91|22)\b", s)
    if m:
        return m.group(1)
    if "bio" in s and "gefl" in s:
        return "41"
    if "wiesenhof" in s or "gefl" in s:
        return "1011"
    if "frischfleisch" in s or "veredlung" in s or "schwein" in s or "p" in s and "k" in s:
        return "65"
    if "fleisch" in s or "wurst" in s or "heidemark" in s:
        return "21"
    if "avo" in s or "gew" in s:
        return "0"
    if "werbe" in s:
        return "91"
    if any(x in s for x in ("pfeiffer","gmyrek","siebert","bard","mago")):
        return "22"
    return "?"


def detect_neue_bspalten(columns: list) -> dict:
    """
    Erkennt neues B-Spalten-Format: '{Tag} {Gruppe} B_{Feld}'
    Feld = Zeit | Sortiment | Sort | Tag
    Bestelltag steht als Datenwert in der B_Tag-Spalte (nicht im Spaltennamen).
    Gibt dict zurück: {(liefertag, gruppe): {"zeit": col, "sort": col, "tag_col": col}}
    """
    rx = re.compile(
        r"^(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+(\d+)\s+B_(Zeit|Sortiment|Sort|Tag)$",
        re.IGNORECASE,
    )
    groups: dict = {}
    order:  list = []
    for col in columns:
        m = rx.match(str(col).strip())
        if not m:
            continue
        day = DAY_SHORT_TO_DE.get(m.group(1))
        grp = m.group(2)
        feld = m.group(3).lower()
        if not day:
            continue
        key = (day, grp)
        if key not in groups:
            groups[key] = {}
            order.append(key)
        fk = "sort" if feld in ("sortiment", "sort") else feld
        groups[key][fk] = col
    return groups


def detect_bspalten(columns: List[str]) -> dict:
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
            dd = DAY_SHORT_TO_DE.get(m.group(1))
            zl = m.group(2).upper()
            gt = m.group(3).strip()
            bd = DAY_SHORT_TO_DE.get(m.group(4))
            if dd and bd:
                key = (dd, gt, bd)
                mapping.setdefault(key, {})
                if zl == "Z":   mapping[key]["zeit"] = c
                elif zl == "L": mapping[key]["l"]    = c
    for c in columns:
        m = rx_b.match(c.strip())
        if m:
            dd = DAY_SHORT_TO_DE.get(m.group(1))
            zl = (m.group(2) or "").upper()
            gt = m.group(3).strip()
            bd = DAY_SHORT_TO_DE.get(m.group(4))
            if dd and bd:
                key = (dd, gt, bd)
                mapping.setdefault(key, {})
                if zl == "Z":
                    if "zeit" not in mapping[key]: mapping[key]["zeit"] = c
                elif zl == "L":
                    if "l" not in mapping[key]:    mapping[key]["l"]    = c
                else:
                    mapping[key]["sort"]       = c
                    mapping[key]["group_text"] = gt
    return mapping


def detect_neue_triplets(columns: list) -> list:
    """
    Erkennt neue Spaltenstruktur: Montag_Zeit, Montag_Sort, Montag_Tag (ggf. mit .1, .2 Suffixen).
    Gibt eine Liste von Dicts zurueck:
      {"liefertag": "Montag", "zeit_col": "Montag_Zeit", "sort_col": "Montag_Sort", "tag_col": "Montag_Tag"}
    """
    TAGE = "Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag"
    FELDER = "Zeit|Sort|Tag"
    # Suffix wie .1 .2 usw. (pandas doppelte Spaltennamen)
    rx = re.compile(
        r"^(" + TAGE + r")_(" + FELDER + r")(?:[.](\d+))?$",
        re.IGNORECASE,
    )
    groups: dict = {}
    order:  list = []
    for col in columns:
        m = rx.match(str(col).strip())
        if not m:
            continue
        day   = m.group(1).capitalize()
        field = m.group(2).lower()          # zeit | sort | tag
        suf   = int(m.group(3)) if m.group(3) else 0
        key   = (day, suf)
        if key not in groups:
            groups[key] = {}
            order.append(key)
        groups[key][field] = col
    result = []
    for key in order:
        g = groups[key]
        if "zeit" in g or "sort" in g or "tag" in g:
            result.append({
                "liefertag": key[0],
                "zeit_col":  g.get("zeit"),
                "sort_col":  g.get("sort"),
                "tag_col":   g.get("tag"),
            })
    return result


def detect_triplets(columns: List[str]) -> dict:
    rx = re.compile(
        r"^(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+(.+?)\s+"
        r"(Zeit|Zeitende|Bestellzeitende|Uhrzeit|Sort|Sortiment|Tag|Bestelltag)$",
        re.IGNORECASE,
    )
    found: dict = {}
    for c in columns:
        m = rx.match(c.strip())
        if not m: continue
        dd  = DAY_SHORT_TO_DE.get(m.group(1))
        if not dd: continue
        gt  = m.group(2).strip()
        ek  = m.group(3).lower()
        key = "Sort" if ek in ("sort","sortiment") else "Tag" if ek in ("tag","bestelltag") else "Zeit"
        found.setdefault(dd, {}).setdefault(gt, {})[key] = c
    return found


def detect_ds_triplets(columns: List[str]) -> dict:
    rx = re.compile(
        r"^DS\s+(.+?)\s+zu\s+(Mo|Die|Di|Mitt|Mit|Mi|Don|Donn|Do|Fr|Sam|Sa)\s+(Zeit|Sort|Tag)$",
        re.IGNORECASE,
    )
    tmp: dict = {}
    for c in columns:
        m = rx.match(c.strip())
        if not m: continue
        dd = DAY_SHORT_TO_DE.get(m.group(2))
        if dd:
            k = f"DS {m.group(1)} zu {m.group(2)}"
            tmp.setdefault(dd, {}).setdefault(k, {})[m.group(3).capitalize()] = c
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
                return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        except Exception:
            continue
    return ""


def logo_file_to_data_uri(f) -> str:
    if not f:
        return ""
    return f"data:{f.type or 'image/png'};base64," + base64.b64encode(f.getvalue()).decode("ascii")


# =============================================================================
# SUCHE – Hilfsfunktionen
# =============================================================================

BLATTNAMEN = ["DIREKT", "MK", "HUPA_NMS", "HUPA_MALCHOW"]

# Blattnamen robust finden:
# Excel kann bei Blattnamen Gross-/Kleinschreibung, Leerzeichen oder alte Namen enthalten.
# Darum wird nicht mehr nur exakt verglichen, sondern normalisiert gesucht.
BLATT_ALIASE = {
    "DIREKT": ["DIREKT", "Direkt", "Direkt 1 - 99"],
    "MK": ["MK", "Hupa MK 882"],
    "HUPA_NMS": ["HUPA_NMS", "HuPa_NMS", "Hupa 2221-4444"],
    "HUPA_MALCHOW": ["HUPA_MALCHOW", "HuPa_Malchow", "Hupa 7773-7779"],
}


def normalize_sheet_name_py(name: str) -> str:
    s = str(name or "")
    s = (s.replace("\u00A0", " ").replace("\ufeff", "").strip())
    s = re.sub(r"\s+", " ", s)
    return s.casefold().replace(" ", "").replace("_", "").replace("-", "")


def find_existing_sheet_name(available_names, *candidates) -> str:
    by_norm = {}
    for real_name in available_names or []:
        by_norm.setdefault(normalize_sheet_name_py(real_name), real_name)
    for candidate in candidates:
        real = by_norm.get(normalize_sheet_name_py(candidate))
        if real:
            return real
    return ""


def find_customer_sheet_names(available_names) -> list:
    found = []
    seen = set()
    for target in BLATTNAMEN:
        real = find_existing_sheet_name(available_names, *BLATT_ALIASE.get(target, [target]))
        if real and real not in seen:
            found.append(real)
            seen.add(real)
    return found

# Spaltennamen in der Quelldatei koennen je nach Version unterschiedlich heissen.
# Aktuelles Format laut Datei: CSB | SAP | Name | Strasse | Plz | Ort | Mo | Die | Mitt | Don | Fr | Sam
# Altes Format war unter anderem: Nr | SAP-Nr. | ...
SPALTEN_ALIASE = {
    "csb_nummer":   ["CSB", "Nr", "CSB-Nr", "CSB-Nr.", "CSB Nummer", "CSB-Nummer", "Kunden Nr", "Kunden-Nr", "Kundennummer"],
    "sap_nummer":   ["SAP", "SAP-Nr.", "SAP-Nr", "SAP Nr", "SAP Nummer", "SAP-Nummer"],
    "name":         ["Name", "Marktname", "Kundenname", "Marktname / Kundenname"],
    "strasse":      ["Strasse", "Straße", "Str.", "Strasse / Nr", "Straße / Nr"],
    "postleitzahl": ["Plz", "PLZ", "Postleitzahl"],
    "ort":          ["Ort"],
    "fachberater":  ["Fachberater", "Berater"],
}


def normalize_header_py(value: str) -> str:
    x = str(value or "")
    x = x.replace("\u00A0", " ").replace("\ufeff", "").strip().lower()
    x = (x.replace("ä", "ae").replace("ö", "oe")
           .replace("ü", "ue").replace("ß", "ss"))
    x = unicodedata.normalize("NFD", x)
    x = "".join(ch for ch in x if unicodedata.category(ch) != "Mn")
    x = re.sub(r"[^a-z0-9]+", "", x)
    return x


def find_column_index(columns, aliases) -> int | None:
    by_norm = {normalize_header_py(col): idx for idx, col in enumerate(columns)}
    for alias in aliases:
        idx = by_norm.get(normalize_header_py(alias))
        if idx is not None:
            return idx
    return None


def first_existing_column(columns, aliases) -> str:
    by_norm = {normalize_header_py(col): col for col in columns}
    for alias in aliases:
        col = by_norm.get(normalize_header_py(alias))
        if col is not None:
            return col
    return ""


def row_get_first(row, aliases, default=""):
    # Kompatibel mit pd.Series (hat .index) und dict (.keys())
    keys = row.index if hasattr(row, "index") else row.keys()
    for col in aliases:
        if col in keys:
            return row.get(col, default)
    by_norm = {normalize_header_py(col): col for col in keys}
    for alias in aliases:
        real = by_norm.get(normalize_header_py(alias))
        if real is not None:
            return row.get(real, default)
    return default
LIEFERTAGE_MAPPING = {
    "Montag": "Mo", "Dienstag": "Die", "Mittwoch": "Mitt",
    "Donnerstag": "Don", "Freitag": "Fr", "Samstag": "Sam",
}


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
    x = (s.replace("\u200b","").replace("\u200c","")
          .replace("\u200d","").replace("\ufeff","")
          .replace("\u00A0"," ").replace("\u2013","-").replace("\u2014","-").lower()
          .replace("\xe4","ae").replace("\xf6","oe").replace("\xfc","ue").replace("\xdf","ss"))
    x = unicodedata.normalize("NFD", x)
    x = "".join(ch for ch in x if unicodedata.category(ch) != "Mn")
    x = re.sub(r"\(.*?\)", " ", x)
    x = re.sub(r"[./,;:+*_#|]", " ", x)
    x = re.sub(r"-", " ", x)
    x = re.sub(r"[^a-z\s]", " ", x)
    return " ".join(x.split())


def build_key_map(df: pd.DataFrame) -> dict:
    """Liest alte und neue Schluesseldateien robust ein.

    Neues Format:
      Titelzeile
      Knd-Nr. | Schluessel-Nr.

    Das alte Format mit Kundennummer in Spalte 1 und Schluessel in Spalte 6
    bleibt weiterhin kompatibel. Mehrere Schluessel je Kunde werden erhalten.
    """
    if df is None or df.empty:
        return {}

    raw = df.dropna(how="all").reset_index(drop=True)
    if raw.empty or raw.shape[1] < 2:
        st.warning("Schluesseldatei enthaelt keine auswertbaren Spalten.")
        return {}

    csb_aliases = [
        "Knd-Nr.", "Knd-Nr", "Knd Nr", "Kundennummer", "Kunden-Nr.",
        "Kunden-Nr", "Kunden Nr", "CSB", "CSB-Nr.", "CSB-Nr",
        "CSB Nummer", "CSB-Nummer",
    ]
    key_aliases = [
        "Schluessel-Nr.", "Schluessel-Nr", "Schluessel Nr",
        "Schluesselnummer", "Schluessel", "Key", "Key-Nr.", "Key Nr",
    ]

    csb_norms = {normalize_header_py(v) for v in csb_aliases}
    key_norms = {normalize_header_py(v) for v in key_aliases}
    header_row = None
    csb_col = None
    key_col = None

    # Ueberschriften koennen wegen einer Titelzeile erst in Zeile 2 stehen.
    for row_idx in range(min(len(raw), 20)):
        values = raw.iloc[row_idx].tolist()
        normalized = [normalize_header_py(v) if not pd.isna(v) else "" for v in values]
        row_csb = next((idx for idx, val in enumerate(normalized) if val in csb_norms), None)
        row_key = next((idx for idx, val in enumerate(normalized) if val in key_norms), None)
        if row_csb is not None and row_key is not None and row_csb != row_key:
            header_row = row_idx
            csb_col = row_csb
            key_col = row_key
            break

    if csb_col is None or key_col is None:
        # Rueckwaertskompatibilitaet zum bisherigen 6-Spalten-Format.
        csb_col = 0
        key_col = 5 if raw.shape[1] > 5 else 1
        data = raw
    else:
        data = raw.iloc[header_row + 1:].reset_index(drop=True)

    collected: dict[str, list[str]] = {}
    for row in data.itertuples(index=False, name=None):
        csb = normalize_digits_py(row[csb_col] if len(row) > csb_col else "")
        key = normalize_digits_py(row[key_col] if len(row) > key_col else "")
        if not csb or not key:
            continue
        keys = collected.setdefault(csb, [])
        if key not in keys:
            keys.append(key)

    # Einzelschluessel bleiben Strings; nur Mehrfachzuordnungen werden Listen.
    # So bleiben auch aeltere HTML-Staende weitgehend kompatibel.
    return {csb: keys[0] if len(keys) == 1 else keys for csb, keys in collected.items()}


def build_berater_map(df: pd.DataFrame) -> dict:
    out = {}
    for row in df.itertuples(index=False, name=None):
        v = ("" if len(row) < 1 or pd.isna(row[0]) else str(row[0])).strip()
        n = ("" if len(row) < 2 or pd.isna(row[1]) else str(row[1])).strip()
        t = ("" if len(row) < 3 or pd.isna(row[2]) else str(row[2])).strip()
        if not t:
            continue
        for k in {norm_de_py(f"{v} {n}"), norm_de_py(f"{n} {v}")}:
            if k and k not in out:
                out[k] = t
    return out


def build_berater_csb_map(df: pd.DataFrame) -> dict:
    out = {}
    for row in df.itertuples(index=False, name=None):
        fach = str(row[0]).strip() if len(row) > 0 and not pd.isna(row[0]) else ""
        csb = normalize_digits_py(row[8]) if len(row) > 8 and not pd.isna(row[8]) else ""
        tel = str(row[14]).strip() if len(row) > 14 and not pd.isna(row[14]) else ""
        mail = str(row[23]).strip() if len(row) > 23 and not pd.isna(row[23]) else ""
        if csb:
            out[csb] = {"name": fach, "telefon": tel, "email": mail}
    return out


def format_lf(v) -> str:
    if pd.isna(v): return ""
    s = str(v).strip().replace(".0", "")
    if not s: return ""
    if s.isdigit(): return f"LF{int(s)}"
    s2 = s.replace(" ", "").upper()
    return s2 if s2.startswith("LF") else s


def build_winter_map(excel_file_obj) -> dict:
    """Liest die Ladefolge aus der Quelldatei.

    Neues Format laut aktueller Datei:
      Blatt: T-B-Druck Quelle
      Spalten: Datum | Tour | LA.F | CSB | NAME | STRASSE | ORT | PLZ | SAP-Nr.

    Fuer aeltere Dateien bleibt "Mo-Sa Winter" als Rueckfall erhalten.
    Ergebnis: {CSB: {Tour: LF...}}
    """
    out = {}

    try:
        excel_file_obj.seek(0)
    except Exception:
        pass

    try:
        book = pd.ExcelFile(excel_file_obj, engine=EXCEL_READ_ENGINE)
        available = list(book.sheet_names)
        sheet = find_existing_sheet_name(
            available,
            "T-B-Druck Quelle",
            "T-B Druck Quelle",
            "T B Druck Quelle",
            "T_B_Druck_Quelle",
            "TBDruckQuelle",
            "Mo-Sa Winter",
            "Mo Sa Winter",
        )
        if not sheet:
            return out
        dfw = pd.read_excel(book, sheet_name=sheet, header=0)
    except Exception:
        return out

    cols = list(dfw.columns)
    tour_col = first_existing_column(cols, ["Tour", "Tournr", "Tour Nr", "Tour-Nr", "Tournummer"])
    lf_col   = first_existing_column(cols, ["LA.F", "LAF", "LA F", "Ladefolge", "Lade Folge", "LF"])
    csb_col  = first_existing_column(cols, ["CSB", "Kunde", "Kundennummer", "Kunden Nr", "Kunden-Nr", "Nr"])

    # Fallback auf feste Positionen aus dem Screenshot:
    # A Datum, B Tour, C LA.F, D CSB
    if not tour_col and len(cols) > 1:
        tour_col = cols[1]
    if not lf_col and len(cols) > 2:
        lf_col = cols[2]
    if not csb_col and len(cols) > 3:
        csb_col = cols[3]

    if not (tour_col and lf_col and csb_col):
        return out

    # Vektorisiert statt iterrows — pro Zeile fielen sonst 3 Funktionsaufrufe an,
    # bei 10k+ Zeilen war das mit 200-500ms der teuerste einzelne Schritt.
    sub = dfw[[csb_col, tour_col, lf_col]]

    def _vec_norm_digits(series: pd.Series) -> pd.Series:
        s = series.astype(str)
        s = s.str.replace(r"\.0$", "", regex=True)
        s = s.str.replace(r"\D", "", regex=True)
        s = s.str.lstrip("0")
        s = s.where(s != "", pd.NA)
        return s

    csb_s  = _vec_norm_digits(sub[csb_col])
    tour_s = _vec_norm_digits(sub[tour_col])

    valid = csb_s.notna() & tour_s.notna()
    if not valid.any():
        return out
    csb_arr  = csb_s[valid].tolist()
    tour_arr = tour_s[valid].tolist()
    lf_raw   = sub.loc[valid, lf_col].tolist()

    for kd, tour, raw in zip(csb_arr, tour_arr, lf_raw):
        lf = format_lf(raw)
        if lf:
            out.setdefault(kd, {})[tour] = lf
    return out

def to_data_url_suche(f) -> str:
    mime = f.type or ("image/png" if f.name.lower().endswith(".png") else "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(read_upload_bytes(f)).decode("utf-8")


# =============================================================================
# CACHED WRAPPER  – arbeiten mit rohen Bytes, sodass @st.cache_data greift.
# Vermeidet, dass dieselben Excel/CSV-Dateien bei jedem Streamlit-Re-Run
# erneut geparst werden. winter_map/notizen_map liefen vorher 2x pro Run.
# =============================================================================

@st.cache_data(show_spinner=False, max_entries=8)
def cached_winter_map(excel_bytes: bytes) -> dict:
    if not excel_bytes:
        return {}
    return build_winter_map(io.BytesIO(excel_bytes))


@st.cache_data(show_spinner=False, max_entries=8)
def cached_key_map(key_bytes: bytes) -> dict:
    if not key_bytes:
        return {}
    # header=None ist absichtlich: Die neue Datei hat eine Titelzeile oberhalb
    # der eigentlichen Spaltennamen. build_key_map erkennt die Kopfzeile selbst.
    df = pd.read_excel(io.BytesIO(key_bytes), sheet_name=0, header=None, engine=EXCEL_READ_ENGINE)
    return build_key_map(df)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_berater_map(berater_bytes: bytes) -> dict:
    if not berater_bytes:
        return {}
    bf = pd.read_excel(io.BytesIO(berater_bytes), sheet_name=0, header=None, engine=EXCEL_READ_ENGINE)
    bf = bf.rename(columns={0: "Vorname", 1: "Nachname", 2: "Nummer"}).dropna(how="all")
    return build_berater_map(bf)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_berater_csb_map(bcsb_bytes: bytes) -> dict:
    if not bcsb_bytes:
        return {}
    try:
        bcf = pd.read_excel(io.BytesIO(bcsb_bytes), sheet_name=0, header=0, engine=EXCEL_READ_ENGINE)
    except Exception:
        bcf = pd.read_excel(io.BytesIO(bcsb_bytes), sheet_name=0, header=None, engine=EXCEL_READ_ENGINE)
    return build_berater_csb_map(bcf)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_lieferhinweis_map(csv_bytes: bytes) -> dict:
    if not csv_bytes:
        return {}
    return build_lieferhinweis_csv(io.BytesIO(csv_bytes))


@st.cache_data(show_spinner=False, max_entries=8)
def cached_rahmentour_map(csv_bytes: bytes) -> dict:
    if not csv_bytes:
        return {}
    return build_rahmentour_map(io.BytesIO(csv_bytes))


@st.cache_data(show_spinner=False, max_entries=8)
def cached_kundenart_map(csv_bytes: bytes) -> dict:
    if not csv_bytes:
        return {}
    return build_kundenart_map(io.BytesIO(csv_bytes))


# =============================================================================
# HTML-ERZEUGUNG: SUCHE
# =============================================================================

def build_kundenart_map(csv_file) -> dict:
    """Liest die Kundenart-CSV mit SAP NR., LKW-Absetzer und LKW-Rampe.
    Ergebnis: {sap_nummer: "Absetzer" | "Rampenkunde" | "Absetzer + Rampenkunde"}.
    """
    if csv_file is None:
        return {}
    try:
        csv_file.seek(0)
        raw = csv_file.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="ignore")
    except Exception:
        return {}

    if not raw:
        return {}

    def _read_csv_bytes(payload: bytes):
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
            try:
                return pd.read_csv(io.BytesIO(payload), sep=None, engine="python", encoding=enc, dtype=str)
            except Exception:
                continue
        try:
            return pd.read_csv(io.BytesIO(payload), sep=";", encoding="utf-8-sig", dtype=str)
        except Exception:
            return pd.DataFrame()

    def _norm_header_local(value: str) -> str:
        return norm_de_py(str(value or "")).replace("-", " ").replace(".", " ").strip()

    def _is_marked(value) -> bool:
        if value is None or pd.isna(value):
            return False
        s = str(value).strip().lower()
        if not s or s in ("nan", "none", "0", "nein", "no", "false", "falsch", "-"):
            return False
        return True

    df = _read_csv_bytes(raw)
    if df.empty:
        return {}

    norm_cols = {_norm_header_local(col): col for col in df.columns}

    sap_col = None
    for key, col in norm_cols.items():
        if "sap" in key and ("nr" in key or "nummer" in key or key.strip() == "sap"):
            sap_col = col
            break
    if sap_col is None:
        sap_col = df.columns[0]

    absetzer_col = None
    rampe_col = None
    for key, col in norm_cols.items():
        if "absetzer" in key:
            absetzer_col = col
        if "rampe" in key:
            rampe_col = col

    result = {}
    for _, row in df.iterrows():
        sap = normalize_digits_py(row.get(sap_col, ""))
        if not sap:
            continue
        labels = []
        if absetzer_col is not None and _is_marked(row.get(absetzer_col, "")):
            labels.append("Absetzer")
        if rampe_col is not None and _is_marked(row.get(rampe_col, "")):
            labels.append("Rampenkunde")
        if labels:
            result[sap] = " + ".join(labels)
    return result


def build_lieferhinweis_csv(csv_file) -> dict:
    """Liest Lieferhinweis-CSV: ';'-getrennt, gequotet.
    Felder: [0]=SAP-Nr, [1]=CSB-Nr, [2]=Name, [3]=Strasse, [4]=PLZ,
            [5]=Ort, [6]=Art/Rollcontainer, [7]=Lieferhinweis, ...
    Key im Ergebnis: CSB-Nr (normalisiert, führende Nullen entfernt).
    Gibt {csb: {'d': lieferhinweis}} zurück; Ladehilfsmittel wird nicht angezeigt."""
    if csv_file is None:
        return {}
    import csv as _csv
    import io as _io
    try:
        csv_file.seek(0)
        raw = csv_file.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
    except Exception:
        return {}
    result = {}
    reader = _csv.reader(_io.StringIO(raw), delimiter=";", quotechar='"')
    for row in reader:
        if len(row) < 2:
            continue
        csb = normalize_digits_py(row[1]) if len(row) > 1 else ""
        if not csb:
            continue
        art      = row[6].strip() if len(row) > 6 else ""
        hinweis  = row[7].strip() if len(row) > 7 else ""
        if art or hinweis:
            entry = {}
            if art:
                pass
            if hinweis:
                entry["d"] = hinweis
            result[csb] = entry
    return result


def build_rahmentour_map(csv_file) -> dict:
    """Liest Rahmentourprofil-CSV: ';'-getrennt, gequotet.
    Erwartet bevorzugt die Kopfzeilen "SAP Rahmentour", "CSB Tournummer" und "Wochentag".
    Gibt {csb_tournr: [{"sap": "...", "day": "Montag"}, ...]} zurück.
    Buchstaben wie M/N/Z bleiben erhalten; Mehrfachzuordnungen bleiben erhalten."""
    if csv_file is None:
        return {}
    import csv as _csv
    import io as _io
    try:
        csv_file.seek(0)
        raw = csv_file.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        return {}

    def _norm_header(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _norm_sap(value) -> str:
        s = str(value or "").strip().replace(".0", "")
        s = re.sub(r"\s+", "", s).upper()
        return s

    result = {}
    reader = _csv.reader(_io.StringIO(raw), delimiter=";", quotechar='"')

    try:
        header = next(reader)
    except StopIteration:
        return result

    header_norm = [_norm_header(col) for col in header]
    sap_idx = header_norm.index("sap rahmentour") if "sap rahmentour" in header_norm else 0
    csb_idx = header_norm.index("csb tournummer") if "csb tournummer" in header_norm else 1
    day_idx = header_norm.index("wochentag") if "wochentag" in header_norm else 2

    for row in reader:
        if not row:
            continue
        sap = _norm_sap(row[sap_idx] if len(row) > sap_idx else "")
        csb = normalize_digits_py(row[csb_idx]) if len(row) > csb_idx else ""
        day = str(row[day_idx]).strip() if len(row) > day_idx else ""
        if not (csb and sap):
            continue
        result.setdefault(csb, [])
        entry = {"sap": sap, "day": day}
        if entry not in result[csb]:
            result[csb].append(entry)
    return result


def generate_suche_html(excel_file, key_file, logo_file,
                         berater_file, berater_csb_file,
                         lieferhinweis_csv=None, rahmentour_csv=None,
                         kundenart_csv=None) -> str:
    if logo_file is None:
        raise ValueError("Bitte Logo hochladen.")

    logo_data_url = to_data_url_suche(logo_file)
    excel_bytes = read_upload_bytes(excel_file)

    with st.spinner("Lese Schluesseldatei ..."):
        key_map = cached_key_map(read_upload_bytes(key_file))

    berater_map: dict = {}
    if berater_file is not None:
        with st.spinner("Lese Fachberater-Telefonliste ..."):
            berater_map = cached_berater_map(read_upload_bytes(berater_file))

    berater_csb_map: dict = {}
    if berater_csb_file is not None:
        with st.spinner("Lese Fachberater-CSB-Zuordnung ..."):
            berater_csb_map = cached_berater_csb_map(read_upload_bytes(berater_csb_file))

    with st.spinner("Lese Ladefolgen (T-B-Druck Quelle) ..."):
        winter_map = cached_winter_map(excel_bytes)

    tour_dict: dict = {}

    def kunden_sammeln(df: pd.DataFrame):
        column_index = {str(col): idx for idx, col in enumerate(df.columns)}
        if not column_index:
            return

        day_columns = [
            (tag, spaltenname, column_index.get(spaltenname))
            for tag, spaltenname in LIEFERTAGE_MAPPING.items()
            if spaltenname in column_index
        ]
        field_columns = {field: find_column_index(df.columns, aliases) for field, aliases in SPALTEN_ALIASE.items()}
        csb_idx = field_columns.get("csb_nummer")
        n_cols = len(df.columns)

        # Lokale Refs sparen Attribut-Lookups in der Hot-Loop
        _norm_dig = normalize_digits_py
        _kmap = key_map
        _bmap = berater_csb_map
        _setdef = tour_dict.setdefault

        # Felder die normalisiert werden muessen
        field_items = [(f, i) for f, i in field_columns.items() if i is not None]

        for row in df.itertuples(index=False, name=None):
            # Erst pruefen ob ueberhaupt ein gueltiger Liefertag in der Zeile ist
            # — vermeidet das Bauen des Entry-Dicts wenn der Kunde "leer" ist.
            valid_days = []
            for tag, _, day_idx in day_columns:
                if day_idx is None or day_idx >= n_cols:
                    continue
                v = row[day_idx]
                # Schneller String-Check ohne unnoetige Konversion
                if v is None or (isinstance(v, float) and v != v):  # NaN
                    continue
                tournr_raw = str(v).strip()
                if not tournr_raw:
                    continue
                # isdigit nach . entfernen
                if not tournr_raw.replace(".", "", 1).isdigit():
                    continue
                valid_days.append((tag, _norm_dig(tournr_raw)))

            if not valid_days:
                continue

            # Entry EINMAL pro Zeile bauen — vorher 1-3x (pro Liefertag) identisch.
            base_entry = {}
            for field, idx in field_items:
                if idx < n_cols:
                    v = row[idx]
                    base_entry[field] = "" if v is None else str(v).strip()
                else:
                    base_entry[field] = ""

            csb = _norm_dig(row[csb_idx] if csb_idx is not None and csb_idx < n_cols else "")
            base_entry["csb_nummer"] = csb
            base_entry["sap_nummer"] = _norm_dig(base_entry.get("sap_nummer", ""))
            base_entry["postleitzahl"] = _norm_dig(base_entry.get("postleitzahl", ""))
            base_entry["schluessel"] = _kmap.get(csb, "")

            if csb:
                bcsb = _bmap.get(csb)
                if bcsb and bcsb.get("name"):
                    base_entry["fachberater"] = bcsb["name"]

            # Pro Liefertag eine flache Kopie + tournummer/liefertag setzen
            for tag, tournr in valid_days:
                entry = base_entry.copy()
                entry["liefertag"] = tag
                _setdef(tournr, []).append(entry)

    with st.spinner("Verarbeite Kundendatei ..."):
        try:
            excel_book = pd.ExcelFile(io.BytesIO(excel_bytes), engine=EXCEL_READ_ENGINE)
            alle_blaetter = list(excel_book.sheet_names)
        except Exception:
            excel_book = None
            alle_blaetter = []

        vorhandene = find_customer_sheet_names(alle_blaetter)
        zu_lesen = vorhandene or alle_blaetter or BLATTNAMEN

        if excel_book is not None:
            for blatt in zu_lesen:
                try:
                    kunden_sammeln(pd.read_excel(excel_book, sheet_name=blatt))
                except (ValueError, KeyError):
                    continue
        else:
            for blatt in zu_lesen:
                try:
                    kunden_sammeln(pd.read_excel(io.BytesIO(excel_bytes), sheet_name=blatt))
                except (ValueError, KeyError):
                    continue

    if not tour_dict:
        blaetter_info = ", ".join(alle_blaetter) if alle_blaetter else "unbekannt"
        raise ValueError(
            f"Keine gueltigen Kundendaten gefunden. "
            f"Verfuegbare Blaetter: {blaetter_info}. "
            f"Erwartet: {', '.join(BLATTNAMEN)}"
        )

    sorted_tours = dict(sorted(
        tour_dict.items(),
        key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0,
    ))

    notizen_map: dict = cached_lieferhinweis_map(read_upload_bytes(lieferhinweis_csv))
    rahmen_map:  dict = cached_rahmentour_map(read_upload_bytes(rahmentour_csv))
    kundenart_map: dict = cached_kundenart_map(read_upload_bytes(kundenart_csv))

    # separators=(",", ":") — spart bei grossen Maps 10-20% Output-Groesse
    # und ist auch beim json.dumps selbst etwas schneller.
    _dump = lambda d: json.dumps(d, ensure_ascii=False, separators=(",", ":"))

    return (
        get_suche_template()
        .replace("const tourkundenData   = {  }",
                 f"const tourkundenData   = {_dump(sorted_tours)}")
        .replace("const keyIndex         = {  }",
                 f"const keyIndex         = {_dump(key_map)}")
        .replace("const beraterIndex     = {  }",
                 f"const beraterIndex     = {_dump(berater_map)}")
        .replace("const beraterCSBIndex  = {  }",
                 f"const beraterCSBIndex  = {_dump(berater_csb_map)}")
        .replace("const winterIndex      = {  }",
                 f"const winterIndex      = {_dump(winter_map)}")
        .replace("const kundenNotizen    = {  }",
                 f"const kundenNotizen    = {_dump(notizen_map)}")
        .replace("const rahmentourIndex  = {  }",
                 f"const rahmentourIndex  = {_dump(rahmen_map)}")
        .replace("const kundenArtIndex    = {  }",
                 f"const kundenArtIndex    = {_dump(kundenart_map)}")
        .replace("__LOGO_DATA_URL__", logo_data_url)
        .replace("</style>", ".header{display:none !important;} .page{padding-top:0 !important;} .container{margin-top:0 !important;} </style>")
    )


# =============================================================================
# WOCHENAUSLASTUNG: Aggregation aus der Quelldatei
# =============================================================================
# Liest die vier Depot-Blaetter und zaehlt pro Wochentag:
#   - Kunden/Tag = gefuellte (gueltige) Tournummer-Zellen
#   - Touren/Tag = distinkte Tournummern
# Rueckgabe-Struktur (kompakt, wird per Instanz in INSTANCES eingebettet):
#   {"days":[...], "depots":[...], "kunden":{depot:[6 Werte]}, "touren":{depot:[6 Werte]}}
WOCHE_DEPOT_LABELS = [
    ("DIREKT", "Direkt"),
    ("MK", "Marktkauf"),
    ("HUPA_NMS", "Neumünster"),
    ("HUPA_MALCHOW", "Malchow"),
]
WOCHE_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa"]
# Reihenfolge passend zu WOCHE_DAYS; Schluessel = Spaltenname in LIEFERTAGE_MAPPING
WOCHE_DAY_COLS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]
# Kunden, die als eigene Gruppe (statt unter ihrem Depot) gefuehrt werden.
# (Anzeige-Label, Suchbegriff im Namen [klein]). Reihenfolge = Anzeige-Reihenfolge am Ende.
WOCHE_SONDERKUNDEN = [("Picnic", "picnic")]


def compute_woche_data(excel_file) -> dict:
    try:
        excel_bytes = read_upload_bytes(excel_file)
    except Exception:
        return {}
    if not excel_bytes:
        return {}

    try:
        book = pd.ExcelFile(io.BytesIO(excel_bytes), engine=EXCEL_READ_ENGINE)
        available = list(book.sheet_names)
    except Exception:
        return {}

    depot_labels = [label for _, label in WOCHE_DEPOT_LABELS]
    sonder_labels = [lab for lab, _ in WOCHE_SONDERKUNDEN]
    # Akkumulatoren fuer alle moeglichen Gruppen
    kunden = {g: [0] * len(WOCHE_DAYS) for g in depot_labels + sonder_labels}
    touren_sets = {g: [set() for _ in WOCHE_DAYS] for g in depot_labels + sonder_labels}
    cust = {}  # ckey -> {"bits":int, "grp":label, "name":str}  (distinkte Kunden, Tage vereinigt)
    seen_depot = set()

    for target, label in WOCHE_DEPOT_LABELS:
        sheet = find_existing_sheet_name(available, *BLATT_ALIASE.get(target, [target]))
        if not sheet:
            continue
        try:
            df = pd.read_excel(book, sheet_name=sheet)
        except (ValueError, KeyError):
            continue
        seen_depot.add(label)

        cols_list = list(df.columns)
        pos = {str(c): idx for idx, c in enumerate(cols_list)}
        name_pos = pos.get("Name")
        csb_pos = pos.get("CSB")
        sap_pos = pos.get("SAP")
        ort_pos = pos.get("Ort")
        day_pos = [pos.get(LIEFERTAGE_MAPPING.get(sn)) for sn in WOCHE_DAY_COLS]
        n_cols = len(cols_list)

        for row in df.itertuples(index=False, name=None):
            # Zielgruppe bestimmen: Sonderkunde (z. B. Picnic) oder Depot
            nm_raw = ""
            if name_pos is not None and name_pos < n_cols:
                nm_raw = str(row[name_pos]).strip()
            grp = label
            nm_low = nm_raw.lower()
            for lab, needle in WOCHE_SONDERKUNDEN:
                if needle in nm_low:
                    grp = lab
                    break
            row_bits = 0
            for c, dp in enumerate(day_pos):
                if dp is None or dp >= n_cols:
                    continue
                v = row[dp]
                if v is None or (isinstance(v, float) and v != v):
                    continue
                s = str(v).strip()
                if not s or not s.replace(".", "", 1).isdigit():
                    continue
                kunden[grp][c] += 1
                touren_sets[grp][c].add(normalize_digits_py(s))
                row_bits |= (1 << c)

            if row_bits:
                csb_val = ""
                if csb_pos is not None and csb_pos < n_cols and row[csb_pos] is not None:
                    csb_val = normalize_digits_py(row[csb_pos])
                sap_val = ""
                if sap_pos is not None and sap_pos < n_cols and row[sap_pos] is not None:
                    sap_val = normalize_digits_py(row[sap_pos])
                ort_val = ""
                if ort_pos is not None and ort_pos < n_cols and row[ort_pos] is not None and not (isinstance(row[ort_pos], float) and row[ort_pos] != row[ort_pos]):
                    ort_val = str(row[ort_pos]).strip()
                # Kunde eindeutig per CSB (sonst Name) – Tage vereinigen
                ckey = csb_val if csb_val else ("n:" + nm_low)
                ent = cust.get(ckey)
                if ent is None:
                    cust[ckey] = {"bits": row_bits, "grp": grp, "name": nm_raw,
                                  "csb": csb_val, "sap": sap_val, "ort": ort_val}
                else:
                    ent["bits"] |= row_bits
                    if not ent.get("sap"):
                        ent["sap"] = sap_val
                    if not ent.get("ort"):
                        ent["ort"] = ort_val

    # Anzeige-Reihenfolge: vorhandene Depots, dann Sonderkunden mit Daten
    out_depots = [d for d in depot_labels if d in seen_depot]
    for lab in sonder_labels:
        if any(kunden[lab]):
            out_depots.append(lab)

    if not out_depots:
        return {}

    touren = {g: [len(s) for s in touren_sets[g]] for g in out_depots}
    kunden = {g: kunden[g] for g in out_depots}

    # Rhythmus-Aggregation (distinkte Kunden, Tage vereinigt)
    grp_index = {g: i for i, g in enumerate(out_depots)}
    patterns = {}
    kunden_liste = []
    for ent in cust.values():
        bits = ent["bits"]
        if not bits:
            continue
        key = str(bits)
        patterns[key] = patterns.get(key, 0) + 1
        gi = grp_index.get(ent["grp"], 0)
        kunden_liste.append([ent["name"], gi, bits, ent.get("csb", ""), ent.get("sap", ""), ent.get("ort", "")])
    # Kompakt + stabil sortiert (nach Name)
    kunden_liste.sort(key=lambda x: x[0].lower())

    return {
        "days": WOCHE_DAYS,
        "depots": out_depots,
        "kunden": kunden,
        "touren": touren,
        "patterns": patterns,
        "kunden_liste": kunden_liste,
    }


# =============================================================================
# HTML-ERZEUGUNG: DRUCK
# =============================================================================

SHEETS_DRUCK = {
    "direkt":  "DIREKT",
    "mk":      "MK",
    "nms":     "HUPA_NMS",
    "malchow": "HUPA_MALCHOW",
}


def generate_druck_html(up, logo_up, fcsb_file=None, lieferhinweis_csv=None) -> str:
    logo_uri = logo_file_to_data_uri(logo_up) or load_logo_data_uri()
    all_data: dict = {}

    _SHEETS_ALT = {
        "direkt":  "Direkt 1 - 99",
        "mk":      "Hupa MK 882",
        "nms":     "Hupa 2221-4444",
        "malchow": "Hupa 7773-7779",
    }

    # Excel einmal komplett in den Speicher lesen + ExcelFile-Handle wiederverwenden
    excel_bytes = read_upload_bytes(up)
    excel_buf = io.BytesIO(excel_bytes)
    try:
        excel_book = pd.ExcelFile(excel_buf, engine=EXCEL_READ_ENGINE)
        available_sheets = list(excel_book.sheet_names)
    except Exception:
        excel_book = None
        available_sheets = []

    for area_key, sheet_name in SHEETS_DRUCK.items():
        with st.spinner(f"Verarbeite: {sheet_name} ..."):
            df = None
            if excel_book is not None:
                candidates = [sheet_name, _SHEETS_ALT.get(area_key, "")]
                if area_key == "direkt":
                    candidates += BLATT_ALIASE["DIREKT"]
                elif area_key == "mk":
                    candidates += BLATT_ALIASE["MK"]
                elif area_key == "nms":
                    candidates += BLATT_ALIASE["HUPA_NMS"]
                elif area_key == "malchow":
                    candidates += BLATT_ALIASE["HUPA_MALCHOW"]
                real_sheet = find_existing_sheet_name(available_sheets, *candidates)
                if real_sheet:
                    try:
                        df = pd.read_excel(excel_book, sheet_name=real_sheet)
                    except Exception:
                        df = None
            if df is None:
                continue

        cols    = df.columns.tolist()
        trip    = detect_triplets(cols)
        neue    = detect_neue_triplets(cols)   # neues Format: Montag_Zeit / Montag_Sort / Montag_Tag
        bmap    = detect_bspalten(cols)
        nbmap   = detect_neue_bspalten(cols)   # neues B-Format: "Die 1001 B_Zeit" etc.
        ds_trip = detect_ds_triplets(cols)
        data: dict = {}

        # Indizes pro Liefertag einmalig vorbauen (vorher in jeder Zeile neu gefiltert).
        bmap_by_day:  dict = {}
        nbmap_by_day: dict = {}
        for k in bmap:
            bmap_by_day.setdefault(k[0], []).append(k)
        for k in nbmap:
            nbmap_by_day.setdefault(k[0], []).append(k)
        # Liste der neuen Triplets pro Liefertag
        neue_by_day: dict = {}
        for nt in neue:
            neue_by_day.setdefault(nt["liefertag"], []).append(nt)

        # Spalten-Indizes EINMAL vor der Schleife berechnen — vorher wurde
        # pro Zeile ein dict(zip(cols, row_tuple)) und 7x row_get_first() (mit
        # eigener normalize_header_py-Schleife je Aufruf) ausgefuehrt. Das war
        # bei 10k+ Zeilen der groesste Hot-Path in generate_druck_html.
        col_to_idx = {c: i for i, c in enumerate(cols)}
        col_to_idx_norm = {normalize_header_py(c): i for i, c in enumerate(cols)}

        def _alias_idx(aliases):
            for a in aliases:
                if a in col_to_idx:
                    return col_to_idx[a]
            for a in aliases:
                idx = col_to_idx_norm.get(normalize_header_py(a))
                if idx is not None:
                    return idx
            return None

        idx_csb   = _alias_idx(SPALTEN_ALIASE["csb_nummer"])
        idx_sap   = _alias_idx(SPALTEN_ALIASE["sap_nummer"])
        idx_name  = _alias_idx(SPALTEN_ALIASE["name"])
        idx_str   = _alias_idx(SPALTEN_ALIASE["strasse"])
        idx_plz   = _alias_idx(SPALTEN_ALIASE["postleitzahl"])
        idx_ort   = _alias_idx(SPALTEN_ALIASE["ort"])
        idx_fach  = _alias_idx(SPALTEN_ALIASE["fachberater"])
        idx_tour  = {d: col_to_idx.get(TOUR_COLS[d]) for d in DAYS_DE}

        # Spalten-Indizes fuer Triplet-/B-Spalten vorbauen — sonst wird r.get()
        # pro Zeile fuer jede Spalte aufgerufen.
        trip_idx: dict = {}      # {day: [(gt, sort_idx, zeit_idx, tag_idx)]}
        for d in DAYS_DE:
            if d in trip:
                bucket = []
                for gt, f_cols in trip[d].items():
                    bucket.append((
                        gt,
                        col_to_idx.get(f_cols.get("Sort")),
                        col_to_idx.get(f_cols.get("Zeit")),
                        col_to_idx.get(f_cols.get("Tag")),
                    ))
                trip_idx[d] = bucket

        neue_idx: dict = {}      # {day: [(sort_idx, zeit_idx, tag_idx)]}
        for d in DAYS_DE:
            bucket = []
            for nt in neue_by_day.get(d, ()):
                bucket.append((
                    col_to_idx.get(nt.get("sort_col")) if nt.get("sort_col") else None,
                    col_to_idx.get(nt.get("zeit_col")) if nt.get("zeit_col") else None,
                    col_to_idx.get(nt.get("tag_col"))  if nt.get("tag_col")  else None,
                ))
            if bucket:
                neue_idx[d] = bucket

        bmap_idx: dict = {}      # {day: [(sort_idx, zeit_idx, l_idx, fallback_tag)]}
        for d in DAYS_DE:
            bucket = []
            for bk in bmap_by_day.get(d, ()):
                bf = bmap[bk]
                bucket.append((
                    col_to_idx.get(bf.get("sort", "")),
                    col_to_idx.get(bf.get("zeit", "")),
                    col_to_idx.get(bf.get("l")) if bf.get("l") else None,
                    bk[2],
                ))
            if bucket:
                bmap_idx[d] = bucket

        nbmap_idx: dict = {}     # {day: [(sort_idx, zeit_idx, tag_idx)]}
        for d in DAYS_DE:
            bucket = []
            for nbk in nbmap_by_day.get(d, ()):
                nbf = nbmap[nbk]
                bucket.append((
                    col_to_idx.get(nbf.get("sort", "")),
                    col_to_idx.get(nbf.get("zeit", "")),
                    col_to_idx.get(nbf.get("tag", "")),
                ))
            if bucket:
                nbmap_idx[d] = bucket

        ds_trip_idx: dict = {}   # {day: [(sort_idx, zeit_idx, tag_idx)]}
        for d in DAYS_DE:
            if d in ds_trip:
                bucket = []
                for k_ds, f_cols in ds_trip[d].items():
                    bucket.append((
                        col_to_idx.get(f_cols.get("Sort")),
                        col_to_idx.get(f_cols.get("Zeit")),
                        col_to_idx.get(f_cols.get("Tag")),
                    ))
                ds_trip_idx[d] = bucket

        # Lokale Refs sparen Attribute-Lookups in der Hot-Loop
        _norm_val = norm_val
        _safe_time = safe_time
        _canon_group_id = canon_group_id
        _SORT_PRIO = SORT_PRIO

        def _gv(row, idx):
            """Sicherer Zugriff auf Tuple-Index, gibt '' zurueck wenn idx None."""
            if idx is None:
                return ""
            try:
                return row[idx]
            except IndexError:
                return ""

        for row in df.itertuples(index=False, name=None):
            knr = _norm_val(_gv(row, idx_csb))
            if not knr: continue
            bestell: list = []
            for d_de in DAYS_DE:
                day_items: list = []
                if d_de in trip_idx:
                    for gt, si, zi, ti in trip_idx[d_de]:
                        s   = _norm_val(_gv(row, si))
                        t   = _safe_time(_gv(row, zi))
                        tag = _norm_val(_gv(row, ti))
                        if s or t or tag:
                            day_items.append({
                                "liefertag": d_de, "sortiment": s,
                                "bestelltag": tag, "bestellschluss": t,
                                "prio": _SORT_PRIO.get(_canon_group_id(s), 50),
                            })
                # Neues Format: Montag_Zeit / Montag_Sort / Montag_Tag
                if d_de in neue_idx:
                    for si, zi, ti in neue_idx[d_de]:
                        s   = _norm_val(_gv(row, si)) if si is not None else ""
                        t   = _safe_time(_gv(row, zi)) if zi is not None else ""
                        tag = _norm_val(_gv(row, ti)) if ti is not None else ""
                        if s or t or tag:
                            day_items.append({
                                "liefertag": d_de, "sortiment": s,
                                "bestelltag": tag, "bestellschluss": t,
                                "prio": _SORT_PRIO.get(_canon_group_id(s), 50),
                            })
                if d_de in bmap_idx:
                    for si, zi, li, fallback_tag in bmap_idx[d_de]:
                        s = _norm_val(_gv(row, si))
                        z = _safe_time(_gv(row, zi))
                        tag = _norm_val(_gv(row, li)) if li is not None else fallback_tag
                        if not tag: tag = fallback_tag
                        if s or z:
                            day_items.append({
                                "liefertag": d_de, "sortiment": s,
                                "bestelltag": tag, "bestellschluss": z,
                                "prio": _SORT_PRIO.get(_canon_group_id(s), 50),
                            })
                # Neues B-Format: "Die 1001 B_Zeit" / "Die 1001 B_Sortiment" / "Die 1001 B_Tag"
                if d_de in nbmap_idx:
                    for si, zi, ti in nbmap_idx[d_de]:
                        s   = _norm_val(_gv(row, si))
                        z   = _safe_time(_gv(row, zi))
                        tag = _norm_val(_gv(row, ti))
                        if s or z:
                            day_items.append({
                                "liefertag": d_de, "sortiment": s,
                                "bestelltag": tag, "bestellschluss": z,
                                "prio": _SORT_PRIO.get(_canon_group_id(s), 50),
                            })
                if d_de in ds_trip_idx:
                    for si, zi, ti in ds_trip_idx[d_de]:
                        s   = _norm_val(_gv(row, si))
                        t   = _safe_time(_gv(row, zi))
                        tag = _norm_val(_gv(row, ti))
                        if s or t or tag:
                            day_items.append({
                                "liefertag": d_de, "sortiment": s,
                                "bestelltag": tag, "bestellschluss": t,
                                "prio": 5.5,
                            })
                day_items.sort(key=lambda x: x["prio"])
                bestell.extend(day_items)

            data[knr] = {
                "plan_typ":    "",
                "bereich":     BEREICH,
                "kunden_nr":   knr,
                "sap_nummer":  _norm_val(_gv(row, idx_sap)),
                "name":        _norm_val(_gv(row, idx_name)),
                "strasse":     _norm_val(_gv(row, idx_str)),
                "plz":         _norm_val(_gv(row, idx_plz)),
                "ort":         _norm_val(_gv(row, idx_ort)),
                "fachberater": _norm_val(_gv(row, idx_fach)),
                "tours":       {d: _norm_val(_gv(row, idx_tour[d])) for d in DAYS_DE},
                "bestell":     bestell,
            }

        all_data[area_key] = data
        st.success(f"✓ {sheet_name}: {len(data)} Kunden verarbeitet")

    # Ladefolge aus Blatt T-B-Druck Quelle — gecached, vermeidet zweite Berechnung
    ladefolge_map: dict = {}
    try:
        ladefolge_map = cached_winter_map(excel_bytes)
    except Exception:
        pass

    json_data      = json.dumps(all_data,      ensure_ascii=False, separators=(",", ":"))
    ladefolge_json = json.dumps(ladefolge_map, ensure_ascii=False, separators=(",", ":"))

    # Notizen aus Lieferhinweis-CSV (gecached)
    notizen_map: dict = cached_lieferhinweis_map(read_upload_bytes(lieferhinweis_csv))
    notizen_json = json.dumps(notizen_map, ensure_ascii=False, separators=(",", ":"))

    return (
        get_druck_template()
        .replace("__DATA_JSON__",      json_data)
        .replace("__LADEFOLGE_JSON__", ladefolge_json)
        .replace("__KUNDEN_NOTIZEN_JSON__", notizen_json)
        .replace("__LOGO_DATAURI__",   logo_uri or "")
    )


# =============================================================================
# HTML KOMBINIEREN  →  app.html
# =============================================================================

# Die Sa-/So-Auswertung wird direkt aus TIMEREC_DATA aufgebaut.

def parse_fahrer_excel(dateien: list) -> str:
    """Verarbeitet mehrere Touren-Excel-Dateien → JSON [{name, years:{year:{krank,urlaub,ausgleich,arbeit,arbeit_samstag,touren,eintraege:[]}}}]"""
    import json as _json
    from io import BytesIO
    import datetime as _dt
    import pandas as _pd
    import re as _re

    WOCHENTAGE = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"]

    def ist_ausgeschlossen(name):
        return is_excluded_driver(name)

    def fmt_zeit(val):
        try:
            if _pd.isna(val): return "n.A."
        except: pass
        if isinstance(val, str):
            val = val.strip()
            if val in ["0:00","00:00","00:00:00"]: return "00:00"
            if ":" in val:
                p = val.split(":")
                if len(p) >= 2:
                    try: return f"{int(p[0]):02d}:{int(p[1]):02d}"
                    except: pass
        elif isinstance(val, float) and val > 0:
            h = int(val * 24); m = int((val * 1440) % 60)
            return f"{h:02d}:{m:02d}"
        elif isinstance(val, (_dt.datetime, _pd.Timestamp)):
            return val.strftime("%H:%M")
        elif isinstance(val, _dt.time):
            return val.strftime("%H:%M")
        return "n.A."

    def fmt_tour(val):
        """Tournummer / Tourtext robust aus Excel übernehmen.
        Auch Texte wie 'z.b.v. / Sonderaufgaben' werden unverändert als Tour angezeigt.
        """
        try:
            if _pd.isna(val):
                return ""
        except Exception:
            pass
        txt = str(val).strip()
        if txt.lower() in ("nan", "none", "nat"):
            return ""
        # Excel-Zellumbrüche und doppelte Leerzeichen sauber im HTML anzeigen
        txt = _re.sub(r"\s+", " ", txt)
        return txt

    # fahrer_map: name → { year → { eintraege:[], krank, urlaub, ausgleich } }
    fahrer_map = {}

    for datei in dateien:
        try:
            datei.seek(0)
            df = _pd.read_excel(BytesIO(datei.read()), sheet_name="Touren", header=None)
            df = df.iloc[5:].reset_index(drop=True)
        except Exception:
            continue

        current_year = _dt.datetime.now().year
        for row in df.itertuples(index=False, name=None):
            datum_raw = row[14] if len(row) > 14 else None
            datum = _pd.to_datetime(datum_raw, errors="coerce")
            if _pd.isna(datum):
                continue

            kw = int(datum.strftime("%W")) + 1  # Sonntag-Start wie original
            jahr = datum.year
            if datum.month == 1 and kw >= 52:
                jahr -= 1
            if datum.year != current_year:
                continue

            wd = WOCHENTAGE[datum.weekday()]
            datum_str = f"{wd}, {datum.strftime('%d.%m.%Y')}"
            uhrzeit = fmt_zeit(row[8] if len(row) > 8 else None)
            tour = fmt_tour(row[15] if len(row) > 15 else None)
            # Sonderfälle: Bei z.b.v. / Sonderaufgaben steht der Tourtext in manchen
            # Exporten nicht in der normalen Tournummer-Spalte P. Dann gezielt
            # nach diesem Text in den nahen Tour-/Bemerkungsspalten suchen.
            if not tour:
                for _idx in (0, 1, 2, 12, 13, 15, 16, 17, 18):
                    if len(row) <= _idx:
                        continue
                    _cand = fmt_tour(row[_idx])
                    if _cand and _re.search(r"z\.?\s*b\.?\s*v|sonderaufgaben|sonder", _cand, _re.I):
                        tour = _cand
                        break
            lkw_raw = row[11] if len(row) > 11 and _pd.notna(row[11]) else ""
            lkw_str = str(lkw_raw).strip()
            lkw = str(int(float(lkw_str))) if lkw_str.replace(".", "").replace("-", "").isdigit() else (lkw_str if lkw_str not in ("nan", "None", "") else "")

            paare = []

            def _cell_text(v):
                try:
                    if _pd.isna(v):
                        return ""
                except Exception:
                    pass
                s = str(v).strip()
                if s.lower() in ("nan", "none", "nat"):
                    return ""
                return _re.sub(r"\s+", " ", s)

            def _add_pair(nachname, vorname):
                nachname = _cell_text(nachname)
                vorname = _cell_text(vorname)
                if not nachname or not vorname:
                    return
                key = (nachname.lower(), vorname.lower())
                if key not in {(a.lower(), b.lower()) for a, b in paare}:
                    paare.append((nachname, vorname))

            def _add_full_name(value):
                full = _cell_text(value)
                if not full:
                    return
                if _re.search(r"z\.?\s*b\.?\s*v|sonderaufgaben|sonder|krank|urlaub|ausgleich", full, _re.I):
                    return
                # Standard: "Nachname, Vorname"
                if "," in full:
                    parts = [p.strip() for p in full.split(",") if p.strip()]
                    if len(parts) >= 2:
                        _add_pair(parts[0], " ".join(parts[1:]))
                    return
                # Fallback für eine einzelne Namenszelle: beide üblichen Reihenfolgen
                # zulassen; der spätere JavaScript-Abgleich ist ebenfalls robust.
                parts = full.split()
                if len(parts) >= 2 and not any(ch.isdigit() for ch in full):
                    _add_pair(parts[-1], " ".join(parts[:-1]))
                    _add_pair(parts[0], " ".join(parts[1:]))

            # Normale Exportstruktur: D/E und G/H sind Nachname/Vorname.
            if len(row) > 4:
                _add_pair(row[3], row[4])
            if len(row) > 7:
                _add_pair(row[6], row[7])
            # Robuster Fallback: z.b.v.-Zeilen haben den Fahrer manchmal in nur einer Zelle.
            for _idx in (3, 4, 6, 7):
                if len(row) > _idx:
                    _add_full_name(row[_idx])

            # Falls die Tourenzeile keinen Fahrer enthält, bleibt sie trotzdem als
            # Planungs-Eintrag erhalten. Die 10H-Zuordnung kann sie dann über
            # Anfangsdatum, LKW und Startzeit finden.
            if not paare and (tour or lkw or uhrzeit != "n.A."):
                paare.append(("Unzugeordnet", "Planung"))

            for nachname, vorname in paare:
                if not nachname or not vorname:
                    continue
                if ist_ausgeschlossen(nachname):
                    continue
                if nachname in ("0", "nan") or vorname in ("0", "nan"):
                    continue
                name = f"{nachname}, {vorname}"
                yr = str(jahr)
                if name not in fahrer_map:
                    fahrer_map[name] = {}
                if yr not in fahrer_map[name]:
                    fahrer_map[name][yr] = {"eintraege": []}
                fahrer_map[name][yr]["eintraege"].append({
                    "kw": kw,
                    "datum": datum_str,
                    "tour": tour,
                    "zeit": uhrzeit,
                    "lkw": lkw,
                    "samstag": wd == "Samstag" or (wd == "Freitag" and uhrzeit >= "18:00" and uhrzeit != "n.A."),
                })

    # Aggregate
    result = []
    for name, years in sorted(fahrer_map.items()):
        years_out = {}
        for yr, data in sorted(years.items()):
            eintr = data["eintraege"]
            tour_cnt = {}
            for e in eintr:
                t = e["tour"].lower()
                if "krank" not in t and "urlaub" not in t and "ausgleich" not in t and e["tour"]:
                    tour_cnt[e["tour"]] = tour_cnt.get(e["tour"], 0) + 1
            lkw_cnt = {}
            for e in eintr:
                lv = e.get("lkw","").strip()
                if lv and lv not in ("nan","None","","0") and not e["tour"].lower().strip() in ("ausgleich",) and not any(k in e["tour"].lower() for k in ["krank","urlaub","ausgleich"]):
                    lkw_cnt[lv] = lkw_cnt.get(lv, 0) + 1
            years_out[yr] = {
                "krank":          sum(1 for e in eintr if "krank"     in e["tour"].lower()),
                "urlaub":         sum(1 for e in eintr if "urlaub"    in e["tour"].lower()),
                "ausgleich":      sum(1 for e in eintr if "ausgleich" in e["tour"].lower()),
                "arbeit":         sum(1 for e in eintr if e["zeit"] != "n.A."),
                "arbeit_samstag": sum(1 for e in eintr if e["samstag"] and e["zeit"] != "n.A."),
                "touren":         dict(sorted(tour_cnt.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 9999, x[0]))),
                "lkw":            dict(sorted(lkw_cnt.items(), key=lambda x: -x[1])),
                "eintraege":      sorted(eintr, key=lambda x: (x["kw"],)),
            }
        result.append({"name": name, "years": years_out})
    return _json.dumps(result, ensure_ascii=False)


# =============================================================================
# SPEDITEURE — Auswertung der Tourenpläne nach externen Speditionen.
# Sucht in den Touren-Excel die Spediteur-Namen (inkl. Abwandlungen) und
# wertet pro Spedition nach Jahr/Monat/Untername aus: was wurde wann mit welcher
# Tournummer gefahren.
# =============================================================================
SPEDITEUR_KATALOG = [
    ("8001", "Spedition Meyer 1"), ("8002", "Spedition Meyer 2 (36er)"), ("8003", "Spedition Meyer 3"),
    ("8004", "Spedition Meyer 4"), ("8005", "Spedition Meyer 5"), ("8006", "Spedition Meyer 6"),
    ("8007", "Spedition Meyer 7"), ("8008", "Spedition Meyer 8"), ("8009", "Spedition Meyer SZ"),
    ("8010", "Spedition Meyer 9"), ("80101", "Spedition Meyer 10"), ("80102", "Spedition Meyer 11"),
    ("8011", "Paasch & Reinke 1"), ("8012", "Paasch & Reinke 2"), ("8013", "Paasch & Reinke 3"),
    ("8015", "Ch. Holtz T1"), ("8016", "Ch. Holtz T2"), ("8017", "Ch. Holtz T3"),
    ("8019", "deVries - 1"), ("8020", "deVries - 2"), ("8022", "Spedition Ihde"),
    ("8024", "Zippel Logistik T2"), ("8025", "Zippel Logistik T1"), ("8026", "Zippel Logistik T3"),
    ("8027", "Zippel Logistik T4"), ("8029", "Zippel Logistik T5"),
    ("8030", "Insellogistik 1"), ("8031", "Insellogistik 2"), ("8032", "Insellogistik 3"), ("8033", "Insellogistik 4"),
    ("8034", "T & D"), ("8035", "Sped. Maas"), ("8036", "Nordfrost"), ("8037", "Emons"), ("8038", "Thermotraffic"),
    ("8039", "Kudex 1"), ("8040", "Kudex 2"), ("8045", "Kudex 3"), ("8046", "Kudex 4"),
    ("8041", "Pfenning 1"), ("8042", "Pfenning 2"), ("8043", "Pfenning 3"), ("8044", "Pfenning 4"),
]
_SPED_GENERIC = {"spedition", "sped", "logistik", "gmbh", "kg", "co", "transporte", "transport", "cocg", "und"}
_SPED_COMPANY_ALIASES = {"paasch": "paaschreinke", "reinke": "paaschreinke"}

# Oberkategorie (Spedition) je Katalog-Nummer. Die Katalog-Namen sind Unterkategorien.
_SPED_GRUPPEN = {
    "Spedition Meyer": {"8001","8002","8003","8004","8005","8006","8007","8008","8009","8010","80101","80102"},
    "Paasch & Reinke": {"8011","8012","8013"},
    "Ch. Holtz":       {"8015","8016","8017"},
    "deVries":         {"8019","8020"},
    "Spedition Ihde":  {"8022"},
    "Zippel Logistik": {"8024","8025","8026","8027","8029"},
    "Insellogistik":   {"8030","8031","8032","8033"},
    "Kudex":           {"8039","8040","8045","8046"},
    "Pfenning":        {"8041","8042","8043","8044"},
}
_SPED_NR_GRUPPE = {nr: g for g, nrs in _SPED_GRUPPEN.items() for nr in nrs}


def _sped_gruppe(nr: str, name: str) -> str:
    """Liefert die Ober-Spedition zu einer Katalog-Nummer.
    Einzeleinträge ohne Familie (T&D, Maas, Nordfrost, ...) bilden ihre eigene Gruppe."""
    return _SPED_NR_GRUPPE.get(str(nr), name)


def _sped_tokens(text: str) -> list:
    s = str(text or "").lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\([^)]*\)", " ", s)   # Zusätze wie "(36er)" entfernen
    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t]
    return [t for t in toks if t not in _SPED_GENERIC]


def _sped_parse(text: str):
    """Zerlegt einen Namen in (compact, company, variant).

    variant ist die Tour-Kennung am Ende (z.B. '1', '10', 't2', 'sz')."""
    toks = _sped_tokens(text)
    if not toks:
        return "", "", ""
    if re.fullmatch(r"(t\d+|sz|\d+)", toks[-1]):
        variant = toks[-1]
        comp_toks = toks[:-1]
    else:
        variant = ""
        comp_toks = toks[:]
    company = "".join(comp_toks)
    company = _SPED_COMPANY_ALIASES.get(company, company)
    return company + variant, company, variant


def _sped_build_index():
    by_compact, by_company, company_disp = {}, {}, {}
    for nr, name in SPEDITEUR_KATALOG:
        compact, company, variant = _sped_parse(name)
        entry = {"nr": nr, "name": name, "company": company, "variant": variant}
        by_compact[compact] = entry
        by_company.setdefault(company, []).append(entry)
        disp = re.sub(r"\s*[-–]?\s*(T\d+|SZ|\d+)\s*$", "", name, flags=re.I)
        disp = re.sub(r"\s*\([^)]*\)\s*", "", disp).strip()
        company_disp.setdefault(company, disp or name)
    return by_compact, by_company, company_disp


_SPED_BY_COMPACT, _SPED_BY_COMPANY, _SPED_COMPANY_DISP = _sped_build_index()
_SPED_BY_NR = {nr: {"nr": nr, "name": name, **dict(zip(("compact", "company", "variant"), _sped_parse(name)))}
               for nr, name in SPEDITEUR_KATALOG}


def _sped_clean_number(value) -> str:
    """Normiert Nummernzellen aus Excel, zum Beispiel 8035.0 -> 8035."""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    s = str(value or "").strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    return s if re.fullmatch(r"\d+", s) else ""


def _sped_has_marker(text: str) -> bool:
    """Erkennt, ob ein Text wirklich nach Spediteur/Firma aussieht.

    Wichtig gegen Fehlzuordnungen: Der Nachname 'Maas' allein darf nicht
    automatisch als 'Sped. Maas' gewertet werden.
    """
    s = str(text or "").lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return bool(re.search(r"\b(sped|spedition|logistik|transport|transporte|gmbh|kg)\b", s))


def _sped_match(text: str, *, allow_bare_single_company: bool = False):
    # Exakte Katalog-Nummer hat Vorrang. Damit wird nicht über Namen geraten,
    # wenn in der Datei bereits eine eindeutige Spediteur-Nummer steht.
    nr = _sped_clean_number(text)
    if nr and nr in _SPED_BY_NR:
        e = _SPED_BY_NR[nr]
        return {"nr": e["nr"], "name": e["name"], "company": e["company"], "variant": e["variant"]}

    toks = _sped_tokens(text)
    # Einzelne blanke Nachnamen/Firmennamen sind zu unsicher: 'Maas' kann
    # ein Fahrer-Nachname sein. Erlaubt ist das nur, wenn die Zelle einen
    # Firmenmarker wie 'Sped.' enthält oder bewusst freigeschaltet wird.
    if len(toks) == 1 and not _sped_has_marker(text) and not allow_bare_single_company:
        return None

    compact, company, variant = _sped_parse(text)
    if not compact:
        return None
    if compact in _SPED_BY_COMPACT:
        return _SPED_BY_COMPACT[compact]
    ents = _SPED_BY_COMPANY.get(company)
    if ents:
        if variant:
            for e in ents:
                if e["variant"] == variant:
                    return e
            return None  # Firma bekannt, Variante unbekannt -> nicht raten
        if len(ents) == 1:
            return ents[0]
    return None


def _sped_match_pair(left, right):
    """Matcht die beiden Fahrer-/Namensspalten zusammen.

    Wenn links eine Nummer steht, ist diese bindend. Eine fremde Fahrer-Nummer
    darf nicht durch einen zufälligen Nachnamen rechts überschrieben werden
    (Beispiel: Michael Maas != Sped. Maas 8035).
    """
    ltxt = str(left or "").strip()
    rtxt = str(right or "").strip()
    lnr = _sped_clean_number(left)
    rnr = _sped_clean_number(right)

    if lnr:
        if lnr in _SPED_BY_NR:
            return _sped_match(lnr)
        # Nummer vorhanden, aber keine Spediteur-Katalognummer: nicht über
        # den Namen raten. Genau dadurch wurden Fahrer mit gleichem Nachnamen
        # bisher fälschlich als Spedition erkannt.
        return None
    if rnr:
        if rnr in _SPED_BY_NR:
            return _sped_match(rnr)
        return None

    # Erst die Paar-Zelle prüfen, damit 'Sped.' + 'Maas' sauber erkannt wird.
    combo = (ltxt + " " + rtxt).strip()
    if combo:
        ent = _sped_match(combo)
        if ent:
            return ent

    # Einzelzellen nur mit strenger Erkennung. Bare Ein-Wort-Namen wie 'Maas'
    # werden hier absichtlich nicht angenommen.
    for val in (ltxt, rtxt):
        ent = _sped_match(val)
        if ent:
            return ent
    return None


def parse_spediteure_excel(dateien: list) -> str:
    """Liest mehrere Touren-Excel und liefert JSON {katalog, fahrten}.

    Pro Treffer: Spediteur-Nr/Name, Jahr, Monat, Datum, Wochentag, Tour, LKW, Zeit.
    """
    import json as _json
    from io import BytesIO
    import datetime as _dt
    import pandas as _pd

    WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

    def _zeit(val):
        try:
            if _pd.isna(val):
                return ""
        except Exception:
            pass
        if isinstance(val, str):
            v = val.strip()
            if ":" in v:
                p = v.split(":")
                try:
                    return f"{int(p[0]):02d}:{int(p[1]):02d}"
                except Exception:
                    return ""
            return ""
        if isinstance(val, float) and val > 0:
            total_min = int(round(val * 24 * 60))
            h = (total_min // 60) % 24
            m = total_min % 60
            return f"{h:02d}:{m:02d}"
        if isinstance(val, (_dt.datetime, _pd.Timestamp)):
            return val.strftime("%H:%M")
        if isinstance(val, _dt.time):
            return val.strftime("%H:%M")
        return ""

    def _tour(val):
        try:
            if _pd.isna(val):
                return ""
        except Exception:
            pass
        if isinstance(val, float) and val.is_integer():
            return str(int(val))
        t = str(val).strip()
        if t.lower() in ("nan", "none", "nat"):
            return ""
        if re.fullmatch(r"\d+\.0", t):
            t = t[:-2]
        return re.sub(r"\s+", " ", t)

    def _lkw(val):
        try:
            if _pd.isna(val):
                return ""
        except Exception:
            pass
        s = str(val).strip()
        if s.lower() in ("nan", "none", ""):
            return ""
        return str(int(float(s))) if s.replace(".", "").replace("-", "").isdigit() else s

    seen = set()
    fahrten = []
    used_nr = set()

    for datei in dateien:
        try:
            datei.seek(0)
            df = _pd.read_excel(BytesIO(datei.read()), sheet_name="Touren", header=None)
            df = df.iloc[5:].reset_index(drop=True)
        except Exception:
            continue
        quelle = getattr(datei, "name", "") or ""

        for row in df.itertuples(index=False, name=None):
            datum = _pd.to_datetime(row[14] if len(row) > 14 else None, errors="coerce")
            if _pd.isna(datum):
                continue

            # Spediteur-Erkennung aus den Fahrer-/Namenspaaren (D/E, G/H).
            # Die Nummernspalte ist bindend: Steht dort eine fremde Fahrer-Nummer,
            # darf ein gleicher Nachname nicht als Spedition gewertet werden.
            ent = None
            for a, b in ((3, 4), (6, 7)):
                left = row[a] if len(row) > a else ""
                right = row[b] if len(row) > b else ""
                ent = _sped_match_pair(left, right)
                if ent:
                    break
            if not ent:
                continue

            tour = _tour(row[15] if len(row) > 15 else "")
            lkw = _lkw(row[11] if len(row) > 11 else "")
            zeit = _zeit(row[8] if len(row) > 8 else None)
            iso = datum.strftime("%Y-%m-%d")
            key = (ent["nr"], iso, tour, lkw, zeit)
            if key in seen:
                continue
            seen.add(key)
            used_nr.add(ent["nr"])
            fahrten.append({
                "nr": ent["nr"],
                "name": ent["name"],
                "gruppe": _sped_gruppe(ent["nr"], ent["name"]),
                "jahr": str(datum.year),
                "monat": f"{datum.month:02d}",
                "datum": datum.strftime("%d.%m.%Y"),
                "iso": iso,
                "wd": WOCHENTAGE[datum.weekday()],
                "tour": tour,
                "lkw": lkw,
                "zeit": zeit,
                "quelle": quelle,
            })

    katalog = [{"nr": nr, "name": name, "gruppe": _sped_gruppe(nr, name)}
               for nr, name in SPEDITEUR_KATALOG if nr in used_nr]
    fahrten.sort(key=lambda x: (x["iso"], (int(x["nr"]) if x["nr"].isdigit() else 0)))
    return _json.dumps({"katalog": katalog, "fahrten": fahrten}, ensure_ascii=False)


def parse_versp_abfahrt_excel(dateien: list) -> str:
    """Liest die reguläre Abfahrtszeit je Tour direkt aus dem Tourenplan.

    Grundlage im Tourenplan:
      Spalte A (Index 0) = Tournummer
      Spalte I (Index 8) = Abfahrtszeit

    Die Verspätungstabelle braucht die Abfahrt zur Tournummer, nicht zur
    Kundenzeile. Deshalb wird ein flacher Index aufgebaut:
      {"__by_tour": {"1006": "20:00", ...}}

    Wichtig für die Tourenpläne:
    - Tage stehen oft als Blocküberschrift, nicht sauber je Zeile in einer Datumsspalte.
    - Abendtouren für Montag stehen im Sonntag-Block. Für die Verspätung wird daher
      zuerst exakt nach Tournummer gesucht, unabhängig vom Tagesblock.
    - Zeiten können als 20:00, 20.00, 7.00, 09:00, Excel-Zeitwert oder 0 stehen.
      0 beziehungsweise 0:00 ist eine gültige Uhrzeit und darf nicht als leer gelten.
    - Es werden nur echte Tourenplan-Blätter gelesen. Kundendaten-/Tourkundenlisten
      werden bewusst ignoriert, weil dort ebenfalls Tournummern und Uhrzeiten stehen
      und sonst Lieferzeiten statt Abfahrtszeiten übernommen würden.
    """
    import json as _json
    from io import BytesIO
    import datetime as _dt
    import re as _re
    import pandas as _pd
    from collections import Counter

    WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    WD_BY_LOWER = {w.lower(): w for w in WOCHENTAGE}
    DAY_HEADER_RE = _re.compile(
        r"\b(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)\b\s*,?\s*\d{1,2}\.",
        _re.IGNORECASE,
    )

    def _is_empty(val) -> bool:
        try:
            if _pd.isna(val):
                return True
        except Exception:
            pass
        return str(val).strip().lower() in ("", "nan", "none", "nat")

    def _zeit(val):
        if _is_empty(val):
            return ""

        if isinstance(val, (_dt.datetime, _pd.Timestamp)):
            return val.strftime("%H:%M")
        if isinstance(val, _dt.time):
            return val.strftime("%H:%M")

        if isinstance(val, (int, float)):
            try:
                f = float(val)
            except Exception:
                return ""
            if f < 0:
                return ""
            # Excel speichert Uhrzeiten als Tagesbruchteil: 0,8333 = 20:00.
            # 0,0 ist 00:00 und bleibt gültig.
            if 0 <= f < 1:
                total_min = int(round(f * 24 * 60)) % (24 * 60)
                h = total_min // 60
                m = total_min % 60
                return f"{h:02d}:{m:02d}"
            # Zahl als Stunde, zum Beispiel 7 oder 15.
            if float(f).is_integer() and 0 <= int(f) <= 23:
                return f"{int(f):02d}:00"
            # Zahl als HHMM, zum Beispiel 700, 1530 oder 2000.
            iv = int(round(f))
            h = iv // 100
            m = iv % 100
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
            return ""

        v = str(val).strip()
        if not v:
            return ""
        low = v.lower().replace(" ", "")
        if low in ("n.a.", "n.a", "na", "nan", "none", "elternzeit"):
            return ""

        m = _re.match(r"^\s*(\d{1,2})\s*[:\.]\s*(\d{1,2})(?:\s*:\s*\d{1,2})?\s*$", v)
        if m:
            h = int(m.group(1))
            mi = int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return f"{h:02d}:{mi:02d}"
            return ""

        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) in (1, 2):
            h = int(digits)
            if 0 <= h <= 23:
                return f"{h:02d}:00"
        if len(digits) in (3, 4):
            h = int(digits[:-2])
            mi = int(digits[-2:])
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return f"{h:02d}:{mi:02d}"
        return ""

    def _tour_code(val):
        """Nur echte Tournummern übernehmen, keine Zahlen aus Namen/Freitext ziehen."""
        if _is_empty(val):
            return ""
        if isinstance(val, (int, float)):
            try:
                f = float(val)
            except Exception:
                return ""
            if f.is_integer() and f >= 0:
                return str(int(f))
            return ""
        s = str(val).strip()
        if _re.fullmatch(r"\d+", s):
            return s
        if _re.fullmatch(r"\d+\.0+", s):
            return s.split(".", 1)[0]
        return ""

    def _tour_code_from_text(val):
        """Nur für Sonderfälle wie 'NMS-Shuttle Tour 5045' als Fallback."""
        if _is_empty(val):
            return ""
        s = str(val).strip()
        m = _re.search(r"\bTour\s+(\d{3,6})\b", s, flags=_re.IGNORECASE)
        if m:
            return m.group(1)
        return ""

    def _weekday_from_header_row(row) -> str:
        """Erkennt den Tagesblock im Tourenplan.

        Wichtig: In echten Excel-Tourenplänen ist die Tagesüberschrift oft kein
        Text wie "Montag, 1. Juni 2026", sondern ein echter Datumswert mit
        Zellformat. Beim Lesen mit pandas/calamine kommt dann zum Beispiel
        Timestamp(2026-06-01) an. Genau deshalb waren zuletzt alle Abfahrten leer:
        das Blatt wurde als "ohne Tagesblock" verworfen.
        """
        cells = list(row[: min(len(row), 10)])

        # 1) Echte Excel-/pandas-Datumswerte erkennen.
        for x in cells:
            if _is_empty(x):
                continue
            try:
                if isinstance(x, (_pd.Timestamp, _dt.datetime)):
                    if getattr(x, "year", 0) >= 2020:
                        return WOCHENTAGE[int(x.weekday())]
                elif isinstance(x, _dt.date):
                    if getattr(x, "year", 0) >= 2020:
                        return WOCHENTAGE[int(x.weekday())]
            except Exception:
                pass

            # 2) Excel-Seriendatum als Zahl, falls der Reader nicht in Datum wandelt.
            try:
                if isinstance(x, (int, float)) and 40000 <= float(x) <= 60000:
                    d = _dt.datetime(1899, 12, 30) + _dt.timedelta(days=float(x))
                    if d.year >= 2020:
                        return WOCHENTAGE[int(d.weekday())]
            except Exception:
                pass

        joined = " ".join(str(x).strip() for x in cells if not _is_empty(x))

        # 3) Anzeigeformat mit ausgeschriebenem Wochentag.
        m = DAY_HEADER_RE.search(joined)
        if m:
            return WD_BY_LOWER.get(m.group(1).lower(), "")

        # 4) Datums-Text ohne Wochentag, zum Beispiel 01.06.2026.
        m = _re.search(r"\b(\d{1,2})[\./-](\d{1,2})[\./-](\d{2,4})\b", joined)
        if m:
            try:
                dd = int(m.group(1)); mm = int(m.group(2)); yy = int(m.group(3))
                if yy < 100:
                    yy += 2000
                if yy >= 2020:
                    d = _dt.date(yy, mm, dd)
                    return WOCHENTAGE[int(d.weekday())]
            except Exception:
                pass

        # 5) Deutscher Datums-Text ohne Wochentag, zum Beispiel 1. Juni 2026.
        month_map = {
            "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
            "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
            "oktober": 10, "november": 11, "dezember": 12,
        }
        m = _re.search(r"\b(\d{1,2})\.\s*(Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(\d{4})\b", joined, _re.IGNORECASE)
        if m:
            try:
                dd = int(m.group(1))
                mm = month_map.get(m.group(2).lower(), 0)
                yy = int(m.group(3))
                if mm and yy >= 2020:
                    d = _dt.date(yy, mm, dd)
                    return WOCHENTAGE[int(d.weekday())]
            except Exception:
                pass

        return ""

    def _read_sheets(raw: bytes):
        """Liest bevorzugt das Blatt 'Touren', fällt aber robust auf alle Blätter zurück."""
        if not raw:
            return []
        frames = []
        try:
            xl = _pd.ExcelFile(BytesIO(raw), engine=EXCEL_READ_ENGINE)
            names = list(xl.sheet_names or [])
        except Exception:
            try:
                xl = _pd.ExcelFile(BytesIO(raw))
                names = list(xl.sheet_names or [])
            except Exception:
                return []

        # Erst Touren-Blätter, danach alle übrigen. So funktioniert es auch,
        # wenn das Blatt anders heißt oder mehrere Tourenpläne enthalten sind.
        ordered = []
        for n in names:
            if str(n).strip().lower() == "touren":
                ordered.append(n)
        for n in names:
            if n not in ordered:
                ordered.append(n)

        for sheet in ordered:
            try:
                df = _pd.read_excel(BytesIO(raw), sheet_name=sheet, header=None, engine=EXCEL_READ_ENGINE)
            except Exception:
                try:
                    df = _pd.read_excel(BytesIO(raw), sheet_name=sheet, header=None)
                except Exception:
                    continue
            if df is not None and not df.empty:
                frames.append((str(sheet), df))
        return frames

    def _sheet_has_day_headers(df) -> bool:
        """Echte Tourenpläne haben Tagesüberschriften wie 'Montag, 1. Juni 2026'.

        Kundendaten-/Tourkundenlisten können ebenfalls in Spalte A Tournummern und
        in Spalte I Uhrzeiten enthalten. Diese Listen dürfen für die Verspätungstabelle
        nicht als Quelle dienen, weil das Liefer-/Kundenzeiten sind und keine
        Abfahrtszeiten. Deshalb akzeptieren wir nur Blätter mit Tagesblöcken.
        """
        hits = 0
        try:
            rows_iter = df.itertuples(index=False, name=None)
            for i, row in enumerate(rows_iter):
                if i > 500:
                    break
                if _weekday_from_header_row(row):
                    hits += 1
                    if hits >= 1:
                        return True
        except Exception:
            return False
        return False

    # {wochentag: {tour: Counter(zeiten)}} und flacher Index tour -> Counter(zeiten)
    agg: dict = {}
    flat_agg: dict = {}
    source_rows = 0
    skipped_sheets = 0

    for datei in dateien or []:
        try:
            datei.seek(0)
        except Exception:
            pass
        try:
            raw = datei.read()
        except Exception:
            raw = b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="ignore")

        for _sheet_name, df in _read_sheets(raw):
            if not _sheet_has_day_headers(df):
                skipped_sheets += 1
                continue

            current_wd = ""
            # nicht pauschal Zeilen wegschneiden; Überschriften werden sowieso übersprungen.
            for row in df.itertuples(index=False, name=None):
                header_wd = _weekday_from_header_row(row)
                if header_wd:
                    current_wd = header_wd
                    continue

                # Vor der ersten Tagesüberschrift nichts übernehmen. So vermeiden wir,
                # dass Kopfzeilen oder fremde Tabellen als Tourenplan gewertet werden.
                if not current_wd:
                    continue

                tour = _tour_code(row[0] if len(row) > 0 else "")
                # Sonderfall: wenn in A ein Strich steht, aber in B 'NMS-Shuttle Tour 5045'.
                if not tour:
                    tour = _tour_code_from_text(row[1] if len(row) > 1 else "")
                if not tour:
                    continue

                zeit = _zeit(row[8] if len(row) > 8 else None)
                if not zeit:
                    continue

                source_rows += 1
                flat_agg.setdefault(tour, Counter())[zeit] += 1
                agg.setdefault(current_wd, {}).setdefault(tour, Counter())[zeit] += 1

    def _best(counter):
        if not counter:
            return ""
        # häufigste Zeit; bei Gleichstand früheste Uhrzeit
        best = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return best[0][0] if best else ""

    out: dict = {"__by_tour": {}}
    for tour, counter in flat_agg.items():
        z = _best(counter)
        if z:
            out["__by_tour"][tour] = z

    for wd, tours in agg.items():
        out[wd] = {}
        for tour, counter in tours.items():
            z = _best(counter)
            if z:
                out[wd][tour] = z

    # Diagnose im JSON, damit man im Browser/Quelltext sofort sieht, ob etwas geladen wurde.
    out["__meta"] = {
        "touren_mit_abfahrt": len(out.get("__by_tour", {})),
        "gelesene_zeilen": source_rows,
        "ignorierte_blaetter_ohne_tagesblock": skipped_sheets,
    }
    return _json.dumps(out, ensure_ascii=False)


def parse_versp_abfahrt_csv(uploaded_file) -> str:
    """Liest Tourenstart-Zeiten fuer die Verspaetungstabelle aus einer CSV.

    Standard ohne Kopfzeile:
        Tournummer;Uhrzeit
        1005;1:00
        1006;20:00

    Optional mit Kopfzeile:
        Wochentag;Tour;Uhrzeit
        Montag;1005;01:00
    """
    import csv as _csv
    import json as _json
    import datetime as _dt
    import re as _re
    from io import StringIO

    raw = read_upload_bytes(uploaded_file)
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="ignore")
    if not raw:
        return "{}"

    text = ""
    for enc in ("utf-8-sig", "cp1252", "latin1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            pass
    if not text:
        text = raw.decode("utf-8", errors="replace")

    def _is_empty(v) -> bool:
        return str(v or "").strip().lower() in ("", "nan", "none", "nat")

    def _zeit(val):
        if _is_empty(val):
            return ""
        if isinstance(val, (_dt.datetime, _dt.time)):
            return val.strftime("%H:%M")
        v = str(val).strip()
        low = v.lower().replace(" ", "")
        if low in ("n.a.", "n.a", "na", "nan", "none", "elternzeit"):
            return ""
        m = _re.match(r"^\s*(\d{1,2})\s*[:\.]\s*(\d{1,2})(?:\s*:\s*\d{1,2})?\s*$", v)
        if m:
            h = int(m.group(1)); mi = int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return f"{h:02d}:{mi:02d}"
            return ""
        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) in (1, 2):
            h = int(digits)
            if 0 <= h <= 23:
                return f"{h:02d}:00"
        if len(digits) in (3, 4):
            h = int(digits[:-2]); mi = int(digits[-2:])
            if 0 <= h <= 23 and 0 <= mi <= 59:
                return f"{h:02d}:{mi:02d}"
        return ""

    WD = {
        "mo":"Montag", "montag":"Montag",
        "di":"Dienstag", "dienstag":"Dienstag",
        "mi":"Mittwoch", "mittwoch":"Mittwoch",
        "do":"Donnerstag", "donnerstag":"Donnerstag",
        "fr":"Freitag", "freitag":"Freitag",
        "sa":"Samstag", "samstag":"Samstag",
        "so":"Sonntag", "sonntag":"Sonntag",
    }

    def _day(v):
        k = str(v or "").strip().lower().rstrip(".")
        return WD.get(k, "")

    def _tour(v):
        s = str(v or "").strip().replace("\ufeff", "")
        if not s or s.lower() in ("nan", "none"):
            return ""
        if _re.fullmatch(r"\d+\.0+", s):
            s = s.split(".", 1)[0]
        return _re.sub(r"\s+", " ", s)

    sample = "\n".join(text.splitlines()[:20])
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except Exception:
        class _Dialect(_csv.excel):
            delimiter = ";" if text.count(";") >= max(text.count(","), text.count("\t")) else ","
        dialect = _Dialect

    rows = []
    for row in _csv.reader(StringIO(text), dialect):
        if not row or all(_is_empty(c) for c in row):
            continue
        rows.append([str(c).strip() for c in row])
    if not rows:
        return "{}"

    first = [c.lower().strip().replace("ä", "ae") for c in rows[0]]
    has_header = any(("tour" in c or "uhr" in c or "zeit" in c or "abfahrt" in c or "wochentag" in c or c == "tag") for c in first)

    tour_col = 0
    time_col = 1 if len(rows[0]) > 1 else 0
    day_col = None
    start_idx = 0
    if has_header:
        start_idx = 1
        for idx, c in enumerate(first):
            if day_col is None and ("wochentag" in c or c == "tag" or c.endswith("tag")):
                day_col = idx
            if "tour" in c:
                tour_col = idx
            if "uhr" in c or "start" in c or "abfahrt" in c or c in ("zeit", "uhrzeit"):
                time_col = idx
    elif len(rows[0]) >= 3 and _day(rows[0][0]):
        day_col = 0
        tour_col = 1
        time_col = 2

    out = {"__by_tour": {}}
    loaded = 0
    skipped = 0
    duplicates = 0
    for row in rows[start_idx:]:
        if len(row) <= max(tour_col, time_col):
            skipped += 1
            continue
        tour = _tour(row[tour_col])
        zeit = _zeit(row[time_col])
        if not tour or not zeit:
            skipped += 1
            continue
        key = tour if not _re.fullmatch(r"\d+\.0+", tour) else tour.split(".", 1)[0]
        if key in out["__by_tour"]:
            if out["__by_tour"][key] != zeit:
                duplicates += 1
            # Bei mehrfacher Tour bleibt bewusst die erste Zeile der CSV gültig.
            # So kann die CSV-Reihenfolge steuern, welche Startzeit für Sammel-/HUPA-Touren gilt.
        else:
            out["__by_tour"][key] = zeit
        digits = "".join(ch for ch in key if ch.isdigit())
        if digits and digits != key and digits not in out["__by_tour"]:
            out["__by_tour"][digits] = zeit
        if day_col is not None and len(row) > day_col:
            wd = _day(row[day_col])
            if wd:
                bucket = out.setdefault(wd, {})
                if key not in bucket:
                    bucket[key] = zeit
                if digits and digits != key and digits not in bucket:
                    bucket[digits] = zeit
        loaded += 1

    out["__meta"] = {
        "quelle": getattr(uploaded_file, "name", "") or "Tourenstart CSV",
        "touren_mit_abfahrt": len(out.get("__by_tour", {})),
        "gelesene_zeilen": loaded,
        "uebersprungene_zeilen": skipped,
        "abweichende_dubletten": duplicates,
    }
    return _json.dumps(out, ensure_ascii=False)

def parse_timerecording_csv(uploaded_file) -> str:
    """Liest die Tachograph-Schicht-Datei (CSV oder XLSX) und liefert JSON
    pro Fahrer:
        {"Nachname, Vorname": [
            {"tag":"DD.MM.YYYY","wochentag":"Mo","beginn":"HH:MM","ende":"HH:MM",
             "ende_naechster_tag":bool,"schichtdauer":"HH:MM","profil":"HH:MM","lkw":"..."}, ...]}
    """
    import json as _json
    import csv as _csv
    import datetime as _dt
    from io import StringIO, BytesIO

    raw = read_upload_bytes(uploaded_file)
    if not raw:
        return "{}"

    # ── Detect XLSX vs CSV ──────────────────────────────────────────────────
    is_xlsx = raw[:4] == b'PK\x03\x04'  # ZIP magic bytes = xlsx
    rows = []
    header_raw = []

    if is_xlsx:
        try:
            import pandas as _pd
            df = _pd.read_excel(BytesIO(raw), header=0, dtype=str)
            header_raw = [str(c) for c in df.columns]
            for _, r in df.iterrows():
                rows.append([str(r[c]) if _pd.notna(r[c]) else "" for c in df.columns])
        except Exception:
            return "{}"
    else:
        # CSV path (original)
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return "{}"
        reader = _csv.reader(StringIO(text), delimiter=";", quotechar='"')
        all_rows = list(reader)
        if not all_rows or len(all_rows) < 2:
            return "{}"
        header_raw = [h.strip() for h in all_rows[0]]
        rows = all_rows[1:]

    if not rows:
        return "{}"

    header = [h.lower().strip() for h in header_raw]

    def col(name_variants):
        for v in name_variants:
            v = v.lower().strip()
            for i, h in enumerate(header):
                if v in h:  # substring match for longer XLSX headers
                    return i
        return -1

    idx_person = col(["person"])
    idx_beg    = col(["schichtbeginn", "beginn"])
    idx_end    = col(["schichtende", "ende"])
    idx_dauer  = col(["schichtdauer"])
    idx_profil = col(["arbeitszeit nach arbeitszeitprofil"])
    idx_lkw    = col(["fahrzeuge", "terminal"])
    idx_card   = col(["fahrerschlüssel", "fahrerschluessel", "driver card", "kartennummer"])
    idx_ma     = col(["ma-nummer", "ma nummer", "personalnummer", "mitarbeiternummer"])

    if idx_person < 0 or idx_beg < 0:
        return "{}"

    WD = ["Mo","Di","Mi","Do","Fr","Sa","So"]
    by_driver = {}

    def split_dt(s):
        """Parse 'DD.MM.YYYY HH:MM' or '2026-01-02 00:30:00' → (date_str, time_str)."""
        s = (s or "").strip()
        if not s:
            return ("", "")
        # Try ISO format first (from XLSX datetime strings: '2026-01-02 00:30:00')
        try:
            d_obj = _dt.datetime.fromisoformat(s)
            return (d_obj.strftime("%d.%m.%Y"), d_obj.strftime("%H:%M"))
        except (ValueError, TypeError):
            pass
        # Fallback: "DD.MM.YYYY HH:MM"
        parts = s.split(" ")
        if len(parts) >= 2:
            return (parts[0].strip(), parts[1].strip()[:5])
        return (s, "")

    def fmt_duration(s):
        """Normalize timedelta strings like '0 days 09:14:00' → '09:14'."""
        s = (s or "").strip()
        if not s:
            return ""
        import re
        m = re.search(r'(\d{1,2}):(\d{2})', s)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        return s

    for r in rows:
        if not r or len(r) <= idx_person:
            continue
        name = (r[idx_person] or "").strip()
        if not name or name.lower() in ("nan", "none", ""):
            continue
        beg_d, beg_t = split_dt(r[idx_beg]) if idx_beg < len(r) else ("", "")
        end_d, end_t = split_dt(r[idx_end]) if 0 <= idx_end < len(r) else ("", "")
        dauer  = fmt_duration(r[idx_dauer])  if 0 <= idx_dauer  < len(r) else ""
        profil = fmt_duration(r[idx_profil]) if 0 <= idx_profil < len(r) else ""
        lkw    = (r[idx_lkw]    or "").strip() if 0 <= idx_lkw    < len(r) else ""
        card   = (r[idx_card]   or "").strip() if 0 <= idx_card   < len(r) else ""
        ma_nr  = (r[idx_ma]     or "").strip() if 0 <= idx_ma     < len(r) else ""
        if lkw.lower() in ("nan", "none"):
            lkw = ""
        if card.lower() in ("nan", "none", "0"):
            card = ""
        if ma_nr.lower() in ("nan", "none", "0"):
            ma_nr = ""

        if not beg_d:
            continue

        # Wochentag + ISO-Sortierschlüssel
        sort_key = beg_d
        wd = ""
        try:
            d_obj = _dt.datetime.strptime(beg_d, "%d.%m.%Y")
            wd = WD[d_obj.weekday()]
            sort_key = d_obj.strftime("%Y-%m-%d") + " " + (beg_t or "00:00")
        except Exception:
            pass

        next_day = bool(end_d) and end_d != beg_d

        entry = {
            "tag": beg_d,
            "wochentag": wd,
            "beginn": beg_t,
            "ende": end_t,
            "ende_naechster_tag": next_day,
            "schichtdauer": dauer,
            "profil": profil,
            "lkw": lkw,
            "fahrerschluessel": card,
            "ma_nummer": ma_nr,
            "_sort": sort_key,
        }
        by_driver.setdefault(name, []).append(entry)

    # Sortieren je Fahrer
    for n in by_driver:
        by_driver[n].sort(key=lambda e: e.get("_sort", ""))
        for e in by_driver[n]:
            e.pop("_sort", None)

    return _json.dumps(by_driver, ensure_ascii=False)



# =============================================================================
# JAVASCRIPT-BAUSTEINE DES DASHBOARDS
# Statische Skripte liegen bewusst außerhalb von combine_html(). Dadurch bleibt
# die eigentliche Zusammenstellung der HTML-Datei kurz und besser wartbar.
# =============================================================================

# _static_payload_text("_JS_SPESEN") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_FA") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_ZULAGE") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_WASH") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_WASH_RANKING") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_FW_GRAPH") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_VERSTOSS") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

_JS_KNAPP = r""""""

# _static_payload_text("_JS_SPED") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_FABEW") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_BUS") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_ARZT") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_VERSP") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

# _static_payload_text("_JS_WA") wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.

def _dashboard_javascript_parts() -> dict:
    """Liefert die statischen JavaScript-Bausteine für die HTML-Erzeugung."""
    return {
        'spesen': _static_payload_text("_JS_SPESEN"),
        'fa': _static_payload_text("_JS_FA"),
        'zulage': _static_payload_text("_JS_ZULAGE"),
        'wash': _static_payload_text("_JS_WASH"),
        'wash_ranking': _static_payload_text("_JS_WASH_RANKING"),
        'fw_graph': _static_payload_text("_JS_FW_GRAPH"),
        'verstoss': _static_payload_text("_JS_VERSTOSS"),
        'knapp': _JS_KNAPP,
        'sped': _static_payload_text("_JS_SPED"),
        'fabew': _static_payload_text("_JS_FABEW"),
        'bus': _static_payload_text("_JS_BUS"),
        'arzt': _static_payload_text("_JS_ARZT"),
        'versp': _static_payload_text("_JS_VERSP"),
        'wa': _static_payload_text("_JS_WA"),
    }



# =============================================================================
# HTML-TEMPLATE DES DASHBOARDS
# Dieser Bereich enthält ausschließlich das fertige HTML-Grundgerüst. Die
# Datenaufbereitung und Komprimierung bleibt getrennt in combine_html().
# =============================================================================

# =============================================================================
# DATENAUFBEREITUNG FÜR DIE HTML-ERZEUGUNG
# Kleine, getrennt testbare Helfer ersetzen den früheren monolithischen Block
# innerhalb von combine_html().
# =============================================================================

def _to_js_array(b64: str, width: int = 100) -> str:
    chunks = [b64[i:i + width] for i in range(0, len(b64), width)]
    return ",\n".join(f'"{chunk}"' for chunk in chunks)


_PDF_DOCUMENTS_JS_TEMPLATE = r"""
// ── Komprimiert eingebettete PDF-Dokumente ──────────────────────────────────
// Die PDF-Daten stecken in dieser HTML-Datei und werden erst bei Bedarf
// entpackt. Dadurch ist kein zusaetzlicher Dokumentenordner erforderlich.
var PDF_DOCUMENTS = __PDF_DOCUMENTS__;
var PDF_URL_CACHE = {};

async function documentPdfInflate(chunks){
  var b64 = (chunks || []).join("");
  if(!b64) return null;
  var bin = atob(b64);
  var bytes = new Uint8Array(bin.length);
  for(var i=0; i<bin.length; i++) bytes[i] = bin.charCodeAt(i);
  if(typeof DecompressionStream !== "function"){
    throw new Error("Dieser Browser kann die eingebettete PDF nicht entpacken.");
  }
  // Wichtig: Schreiben und Lesen muessen gleichzeitig laufen. Wenn zuerst auf
  // writer.write() gewartet wird, kann der TransformStream wegen Backpressure
  // haengen bleiben und die PDF-Vorschau bleibt leer.
  var inputStream = new Blob([bytes]).stream();
  var outputStream = inputStream.pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(outputStream).arrayBuffer());
}

async function documentPdfGetUrl(id){
  if(PDF_URL_CACHE[id]) return PDF_URL_CACHE[id];
  var doc = PDF_DOCUMENTS[id];
  if(!doc || !doc.z) return "";
  try{
    var pdfBytes = await documentPdfInflate(doc.z);
    if(!pdfBytes) return "";
    var url = URL.createObjectURL(new Blob([pdfBytes], {type:"application/pdf"}));
    PDF_URL_CACHE[id] = url;
    return url;
  }catch(err){
    console.error("PDF konnte nicht entpackt werden:", id, err);
    return "";
  }
}

async function documentPdfInit(id){
  var frame = document.getElementById(id + "-pdf-frame");
  var fallback = document.getElementById(id + "-pdf-fallback");
  var url = await documentPdfGetUrl(id);
  if(!frame || !url){
    if(frame) frame.style.display = "none";
    if(fallback) fallback.style.display = "flex";
    return;
  }
  if(!frame.dataset.loaded){
    frame.src = url + "#view=FitH&toolbar=1&navpanes=0";
    frame.dataset.loaded = "1";
  }
  frame.style.display = "block";
  if(fallback) fallback.style.display = "none";
}

async function documentPdfOpen(id){
  var url = await documentPdfGetUrl(id);
  if(!url){ alert("Die PDF konnte nicht geladen werden."); return; }
  var a = document.createElement("a");
  a.href = url + "#view=FitH&toolbar=1&navpanes=0";
  a.target = "_blank";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function documentPdfDownload(id){
  var doc = PDF_DOCUMENTS[id];
  var url = await documentPdfGetUrl(id);
  if(!doc || !url){ alert("Die PDF konnte nicht geladen werden."); return; }
  var a = document.createElement("a");
  a.href = url;
  a.download = doc.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Kompatibilitaetsfunktionen fuer den bereits vorhandenen KNAPP-Bereich.
function knappGetPdfUrl(){ return documentPdfGetUrl("knapp"); }
function knappInit(){ documentPdfInit("knapp"); }
function knappOpenPdf(){ documentPdfOpen("knapp"); }
function knappDownloadPdf(){ documentPdfDownload("knapp"); }
"""


def _build_pdf_documents_js() -> str:
    payload = {
        doc_id: {
            "z": list(meta["z"]),
            "filename": meta["filename"],
            "title": meta["title"],
        }
        for doc_id, meta in _load_embedded_pdf_documents().items()
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _PDF_DOCUMENTS_JS_TEMPLATE.replace("__PDF_DOCUMENTS__", payload_json)


def _safe_json_object(value: str) -> str:
    try:
        obj = json.loads(value or "{}")
        if not isinstance(obj, dict):
            obj = {}
    except Exception:
        obj = {}
    return json.dumps(obj, ensure_ascii=False)


def _build_instances_js(instances: list) -> str:
    normal_versp_start_json = "{}"
    if instances and isinstance(instances[0], dict):
        normal_versp_start_json = instances[0].get("versp_start_json") or "{}"

    parts = []
    for idx, inst in enumerate(instances):
        search_b64 = base64.b64encode(
            zlib.compress(inst["suche_html"].encode("utf-8"), 9)
        ).decode("ascii")
        print_b64 = base64.b64encode(
            zlib.compress(inst["druck_html"].encode("utf-8"), 9)
        ).decode("ascii")
        search_js = _to_js_array(search_b64)
        print_js = _to_js_array(print_b64)
        name_escaped = inst["name"].replace('"', "&quot;").replace("'", "&#39;")
        versp_json = inst.get("versp_start_json") or "{}"
        if idx > 0 and versp_json in ("{}", "", None):
            versp_json = normal_versp_start_json
        versp_js = _safe_json_object(versp_json)
        week_obj = inst.get("woche_data") or {}
        try:
            week_js = json.dumps(week_obj, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            week_js = "{}"
        parts.append(
            f'{{name:"{name_escaped}",'
            f's:[{search_js}],'
            f'd:[{print_js}],'
            f'versp:{versp_js},'
            f'woche:{week_js}}}'
        )
    return ",\n".join(parts)


def _build_saturday_json_from_timerecording(timerec_json: str) -> str:
    sam_json = "[]"
    try:
        import json as _j
        import datetime as _dt2

        timerec = _j.loads(timerec_json) if timerec_json and timerec_json != "{}" else {}
        if not isinstance(timerec, dict):
            timerec = {}

        def _sam_excluded_driver(name):
            norm = str(name or "").strip().casefold()
            if not norm:
                return True
            if norm in ("unzugeordnet, planung", "planung, unzugeordnet"):
                return True
            return any(str(ex or "").casefold() in norm for ex in EXCLUDED_DRIVER_NAMES)

        def _sam_parse_mins(zeit_str):
            if not zeit_str or zeit_str == "n.A.":
                return None
            try:
                parts = str(zeit_str).strip().split(":")
                return int(parts[0]) * 60 + int(parts[1])
            except Exception:
                return None

        # Ohne Tachograph-Datei keine vermeintlichen Nullstände aus Planungsdaten
        # erzeugen. Die Oberfläche zeigt dann einen klaren Upload-Hinweis.
        if not timerec:
            sam_json = "[]"
        else:
            # Fahrer werden über die eindeutigen Kennungen der Timerecording-Datei
            # zusammengeführt. Primär gilt die MA-Nummer. Falls sie fehlt, wird die
            # Fahrerkartennummer verwendet; erst danach dient der bereinigte Name als
            # Fallback. Damit werden auch Schreibvarianten wie „Vassili“/„Vasilli“
            # korrekt derselben Person zugeordnet, sofern die MA-Nummer identisch ist.
            sam_by_name = {}       # person_key -> Fahrerobjekt
            sam_by_day = {}        # person_key -> {YYYY-MM-DD -> Einsatz}
            sam_active_years = {}  # person_key -> set(Jahr)

            def _sam_clean_name(value):
                s = str(value or "").replace("\xa0", " ")
                # Unsichtbare Unicode-Zeichen entfernen, die optisch gleiche Namen
                # sonst technisch unterschiedlich machen würden.
                s = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", s)
                s = re.sub(r"\s+", " ", s).strip()
                s = re.sub(r"\s*,\s*", ", ", s)
                return s.strip(" ,;-")

            def _sam_id(value):
                s = str(value or "").strip()
                if s.casefold() in ("", "nan", "none", "0"):
                    return ""
                return re.sub(r"\s+", "", s)

            def _sam_name_key(value):
                """Reihenfolge-, Groß-/Kleinschreibungs- und Umlaut-unabhängiger Schlüssel."""
                s = _sam_clean_name(value).casefold()
                if not s:
                    return ""
                s = (s.replace("ä", "ae").replace("ö", "oe")
                       .replace("ü", "ue").replace("ß", "ss"))
                s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
                tokens = [t for t in re.findall(r"[a-z0-9]+", s) if not t.isdigit()]
                return "|".join(sorted(tokens))

            def _sam_display_score(value):
                """Bevorzugt die gut lesbare Schreibweise 'Nachname, Vorname'."""
                s = _sam_clean_name(value)
                score = 0
                if "," in s:
                    score += 100
                if s and not s.isupper() and not s.islower():
                    score += 20
                score += min(len(s), 60) / 100.0
                return score

            def _sam_update_display(driver, candidate):
                candidate = _sam_clean_name(candidate)
                if not candidate:
                    return
                current = driver.get("name", "")
                if not current or _sam_display_score(candidate) > _sam_display_score(current):
                    driver["name"] = candidate
                    parts = candidate.split(",", 1)
                    if len(parts) > 1:
                        driver["nachname"] = parts[0].strip()
                        driver["vorname"] = parts[1].strip()
                    else:
                        driver["nachname"] = candidate
                        driver["vorname"] = ""

            # Zuordnungshilfen aus der kompletten Timerecording-Datei bilden.
            # Eine neue Fahrerkarte derselben Person bleibt über die MA-Nummer verbunden.
            name_to_ma = {}
            card_to_ma = {}
            for raw_name, shifts in timerec.items():
                if not isinstance(shifts, list):
                    continue
                clean_name = _sam_clean_name(raw_name)
                ma_values = set()
                for shift in shifts:
                    if not isinstance(shift, dict):
                        continue
                    ma_val = _sam_id(shift.get("ma_nummer", ""))
                    card_val = _sam_id(shift.get("fahrerschluessel", ""))
                    if ma_val:
                        ma_values.add(ma_val)
                        if card_val:
                            card_to_ma[card_val] = ma_val
                if len(ma_values) == 1:
                    name_to_ma[_sam_name_key(clean_name)] = next(iter(ma_values))

            def _sam_person_key(raw_name, shift):
                ma_val = _sam_id((shift or {}).get("ma_nummer", ""))
                card_val = _sam_id((shift or {}).get("fahrerschluessel", ""))
                name_key = _sam_name_key(raw_name)
                if ma_val:
                    return "ma:" + ma_val
                if card_val and card_val in card_to_ma:
                    return "ma:" + card_to_ma[card_val]
                if name_key and name_key in name_to_ma:
                    return "ma:" + name_to_ma[name_key]
                if card_val:
                    return "card:" + card_val
                return "name:" + name_key if name_key else ""

            def _ensure_sam_driver(raw_name, person_key):
                display_name = _sam_clean_name(raw_name)
                if not person_key:
                    return ""
                if person_key not in sam_by_name:
                    sam_by_name[person_key] = {
                        "person_key": person_key,
                        "name": display_name,
                        "nachname": "",
                        "vorname": "",
                        "einsaetze": 0,
                        "daten": [],
                        "aktive_jahre": [],
                    }
                    sam_by_day[person_key] = {}
                    sam_active_years[person_key] = set()
                _sam_update_display(sam_by_name[person_key], display_name)
                return person_key

            # Sa immer, Freitag ab 18:00 als Fr→Sa, Sonntag bis einschließlich 15:00.
            # Pro Fahrer und Anfangsdatum wird höchstens ein Einsatz gezählt.
            # Fahrerbasis, aktive Jahre und Einsätze stammen ausschließlich aus
            # den tatsächlich geladenen Timerecording-Schichten.
            for driver_name, shifts in timerec.items():
                if _sam_excluded_driver(driver_name):
                    continue
                if not isinstance(shifts, list):
                    continue

                name = _sam_clean_name(driver_name)
                for shift in shifts:
                    if not isinstance(shift, dict):
                        continue
                    person_key = _sam_person_key(name, shift)
                    driver_key = _ensure_sam_driver(name, person_key)
                    if not driver_key:
                        continue

                    tag_str = str(shift.get("tag", "") or "").strip()       # Anfangstag DD.MM.YYYY
                    wd      = str(shift.get("wochentag", "") or "").strip() # Mo ... So
                    beginn  = str(shift.get("beginn", "") or "").strip()
                    lkw     = str(shift.get("lkw", "") or "").strip()

                    try:
                        d_obj = _dt2.datetime.strptime(tag_str, "%d.%m.%Y")
                    except Exception:
                        continue
                    sam_active_years[driver_key].add(d_obj.year)

                    mins = _sam_parse_mins(beginn)
                    is_sa = wd == "Sa"
                    is_fr_abend = wd == "Fr" and mins is not None and mins >= 18 * 60
                    is_so_frueh = wd == "So" and mins is not None and mins <= 15 * 60
                    if not (is_sa or is_fr_abend or is_so_frueh):
                        continue

                    day_key = d_obj.strftime("%Y-%m-%d")
                    kw = d_obj.isocalendar()[1]
                    tag_label = "So" if is_so_frueh else ("Fr→Sa" if is_fr_abend else "Sa")
                    day_map = sam_by_day[driver_key]
                    if day_key not in day_map:
                        day_map[day_key] = {
                            "iso": day_key,
                            "datum": f"{tag_str} (KW{kw})",
                            "tour": "",
                            "tag": tag_label,
                            "beginn": beginn,
                            "_lkw": set(),
                            "_starts": set(),
                        }
                    if beginn:
                        day_map[day_key]["_starts"].add(beginn)
                    if lkw and lkw.lower() not in ("nan", "none", "0"):
                        day_map[day_key]["_lkw"].add(lkw)

            sam_list = []
            for driver_key, driver in sam_by_name.items():
                entries = []
                for day_key in sorted(sam_by_day.get(driver_key, {})):
                    entry = sam_by_day[driver_key][day_key]
                    lkw_values = sorted(entry.pop("_lkw", set()))
                    start_values = sorted(entry.pop("_starts", set()))
                    entry["beginn"] = ", ".join(start_values) if start_values else entry.get("beginn", "")
                    entry["tour"] = ("LKW " + ", ".join(lkw_values)) if lkw_values else ""
                    entries.append(entry)
                driver["daten"] = entries
                driver["einsaetze"] = len(entries)
                driver["aktive_jahre"] = sorted(sam_active_years.get(driver_key, set()), reverse=True)
                sam_list.append(driver)

            sam_list.sort(key=lambda x: str(x.get("name", "")).casefold())
            sam_json = _j.dumps(sam_list, ensure_ascii=False)
    except Exception:
        sam_json = "[]"
    return sam_json


def _json_or_default(raw_value: str, default_json: str):
    try:
        return json.loads(raw_value if raw_value not in (None, "") else default_json)
    except Exception:
        return json.loads(default_json)


def _build_embedded_data_js(
    *,
    fahrzeugwaesche_json: str,
    tel_json: str,
    sam_json: str,
    fa_json: str,
    fahrerbewertung_json: str,
    zulage_json: str,
    drittkunden_json: str,
    verstoss_json: str,
    spesen_json: str,
    grosskunden_json: str,
    timerec_json: str,
    spediteure_json: str,
    versp_abfahrt_json: str,
) -> str:
    data = {
        "fahrzeugwaesche": _json_or_default(fahrzeugwaesche_json, "[]"),
        "telefon": _json_or_default(tel_json, "[]"),
        "samstag": _json_or_default(sam_json, "[]"),
        "fahrer": _json_or_default(fa_json, "[]"),
        "fahrerbewertung": _json_or_default(fahrerbewertung_json, '{"profile":"","event_types":[],"g_months":{},"g_ev":{},"drivers":[]}'),
        "zulagen": _json_or_default(zulage_json, "{}"),
        "drittkunden": _json_or_default(drittkunden_json, "[]"),
        "verstoesse": _json_or_default(verstoss_json, '{"drivers":[],"total_violations":0}'),
        "spesen": _json_or_default(spesen_json, '{"drivers":[],"months":[],"total_cost":0,"total_rows":0}'),
        "grosskunden": _json_or_default(grosskunden_json, "[]"),
        "timerecording": _json_or_default(timerec_json, "{}"),
        "spediteure": _json_or_default(spediteure_json, '{"katalog":[],"fahrten":[]}'),
        "verspaetung_abfahrt": _json_or_default(versp_abfahrt_json, "{}"),
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed_b64 = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    return _to_js_array(compressed_b64)


def _render_dashboard_html(
    *,
    logo_data_url: str,
    last_updated: str,
    generation_meta_json: str,
    instances_js: str,
    embedded_data_js: str,
    documents_js_code: str,
    knapp_js_code: str,
    spesen_js_code: str,
    fa_js_code: str,
    zulage_js_code: str,
    wash_js_code: str,
    wash_ranking_js_code: str,
    fw_graph_js_code: str,
    verstoss_js_code: str,
    sped_js_code: str,
    fabew_js_code: str,
    bus_js_code: str,
    arzt_js_code: str,
    versp_js_code: str,
    wa_js_code: str,
    zulage_xlsx_sonder: str,
    zulage_xlsx_fuengers: str,
    zulage_xlsx_drittkunden: str,
) -> str:
    """Setzt die vorbereiteten Daten und Skriptbausteine in das HTML ein."""
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Fuhrpark NFC</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<script>window.__XLSX_CE__ = window.XLSX;</script>
<script src="https://cdn.jsdelivr.net/npm/xlsx-js-style@1.2.0/dist/xlsx.bundle.js"></script>
<script>window.XLSXStyle = window.XLSX; if(window.__XLSX_CE__) window.XLSX = window.__XLSX_CE__;</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.8.2/jspdf.plugin.autotable.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;font-family:'Segoe UI',Arial,sans-serif}}
.topnav{{
  height:56px;
  background:linear-gradient(180deg,#eef2f6 0%,#dde4eb 100%);
  display:flex;align-items:center;padding:0 12px;gap:4px;
  box-shadow:0 2px 10px rgba(15,23,42,.08);
  border-bottom:1px solid #c5ced8;
  flex-shrink:0;
  overflow-x:auto;
  scrollbar-width:none;
}}
.topnav::-webkit-scrollbar{{display:none;}}
.topnav-logo-wrap{{
  display:flex;align-items:center;flex-shrink:0;
  padding-right:4px;
}}
.topnav-logo{{
  height:28px;
  width:auto;
  display:block;
  object-fit:contain;
}}
.nav-sep{{
  width:1px;height:22px;background:#c8d1db;flex-shrink:0;margin:0 4px;
}}
.nav-btn{{
  padding:6px 9px;border-radius:8px;
  border:1px solid #bcc8d6;
  cursor:pointer;font-weight:800;font-size:12px;
  transition:all .15s ease;background:linear-gradient(180deg,#f9fbfd 0%,#edf2f7 100%);color:#334155;
  white-space:nowrap;flex-shrink:0;
  position:relative;
  box-shadow:0 1px 2px rgba(15,23,42,.05);
}}
.nav-btn:hover:not(.active){{background:linear-gradient(180deg,#ffffff 0%,#eef3f8 100%);border-color:#aeb9c8}}
.nav-btn.active{{background:linear-gradient(180deg,#4f87e8 0%,#3d72d4 100%);border-color:#3f73cf;color:#fff;box-shadow:0 3px 10px rgba(61,114,212,.25)}}
.inst-btn{{border-color:#c5cfda;color:#5b6b80}}
.inst-btn:hover:not(.active){{background:linear-gradient(180deg,#ffffff 0%,#eef3f8 100%)}}
.inst-btn.active{{background:linear-gradient(180deg,#6b7f99 0%,#55677f 100%);border-color:#55677f;color:#fff}}
/* ── Dropdown ── */
.nav-dd{{position:relative;flex-shrink:0}}
.nav-dd-btn{{
  padding:6px 9px;border-radius:8px;
  border:1px solid #bcc8d6;
  cursor:pointer;font-weight:800;font-size:12px;
  transition:all .15s ease;background:linear-gradient(180deg,#f9fbfd 0%,#edf2f7 100%);color:#334155;
  white-space:nowrap;display:flex;align-items:center;gap:4px;
  box-shadow:0 1px 2px rgba(15,23,42,.05);
}}
.nav-dd-btn:hover{{background:linear-gradient(180deg,#ffffff 0%,#eef3f8 100%);border-color:#aeb9c8}}
.nav-dd-btn.active{{background:linear-gradient(180deg,#4f87e8 0%,#3d72d4 100%);border-color:#3f73cf;color:#fff;box-shadow:0 3px 10px rgba(61,114,212,.25)}}
#btn-suche.active{{background:linear-gradient(180deg,#f6dc67 0%,#e6be22 100%);border-color:#d5ac10;color:#334155;box-shadow:0 3px 10px rgba(214,172,16,.28)}}
#btn-suche.active .dd-arrow{{filter:none;opacity:.9}}
.dd-arrow{{font-size:9px;opacity:.75;transition:transform .15s}}
.inst-label{{font-size:9px;font-weight:700;opacity:1;background:#dbe5f0;border:1px solid #c0cad8;border-radius:5px;padding:1px 5px;margin:0 2px 0 4px;white-space:nowrap;color:#4b5d73}}
.nav-dd.open .dd-arrow{{transform:rotate(180deg)}}
.dd-menu{{
  display:none;
  position:fixed;
  background:#f7f9fc;border:1px solid #c5ced8;
  border-radius:8px;min-width:170px;
  box-shadow:0 10px 24px rgba(15,23,42,.12);
  z-index:99999;overflow:hidden;
}}
.nav-dd.open .dd-menu{{display:block}}
.dd-item{{
  padding:9px 14px;cursor:pointer;font-size:12px;font-weight:700;
  color:#334155;transition:background .12s;white-space:nowrap;
  border-bottom:1px solid #e2e8f0;
}}
.dd-item:last-child{{border-bottom:none}}
.dd-item:hover{{background:#eaf3fb}}
.dd-item.active{{background:linear-gradient(180deg,#2f80b7 0%,#1e6091 100%);color:#fff}}
.topnav-meta-btn{{
  margin-left:auto;flex-shrink:0;white-space:nowrap;
  padding:5px 8px;border-radius:7px;border:1px solid #b9c5d2;
  background:linear-gradient(180deg,#ffffff 0%,#edf2f7 100%);
  color:#415269;font-size:10px;font-weight:900;cursor:pointer;
  box-shadow:0 1px 2px rgba(15,23,42,.05);
}}
.topnav-meta-btn:hover{{background:#fff;border-color:#8fa1b4}}
.topnav-stamp{{
  font-size:10px;font-weight:800;color:#5b6b80;white-space:nowrap;
  position:sticky;right:0;flex-shrink:0;padding:0 2px 0 4px;background:#e4e9ef;
}}
.build-info-overlay{{display:none;position:fixed;inset:0;z-index:200000;background:rgba(15,23,42,.55);padding:24px;align-items:center;justify-content:center}}
.build-info-overlay.open{{display:flex}}
.build-info-dialog{{width:min(920px,96vw);max-height:88vh;overflow:auto;background:#f8fafc;border:1px solid #b9c6d5;border-radius:14px;box-shadow:0 24px 70px rgba(15,23,42,.30)}}
.build-info-head{{position:sticky;top:0;z-index:2;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 18px;background:linear-gradient(180deg,#ffffff 0%,#eef3f8 100%);border-bottom:1px solid #d5dde7}}
.build-info-title{{font-size:17px;font-weight:950;color:#0f172a}}
.build-info-sub{{font-size:11px;color:#64748b;font-weight:700;margin-top:2px}}
.build-info-close{{width:34px;height:34px;border-radius:8px;border:1px solid #c4ceda;background:#fff;color:#334155;font-size:19px;font-weight:900;cursor:pointer}}
.build-info-body{{padding:16px 18px 20px}}
.build-info-cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-bottom:16px}}
.build-info-card{{background:#fff;border:1px solid #d7e0ea;border-radius:10px;padding:11px 12px}}
.build-info-card-label{{font-size:9px;font-weight:900;text-transform:uppercase;letter-spacing:.45px;color:#64748b}}
.build-info-card-value{{font-size:16px;font-weight:950;color:#0f172a;margin-top:4px;overflow-wrap:anywhere}}
.build-info-section{{background:#fff;border:1px solid #d7e0ea;border-radius:10px;overflow:hidden;margin-top:10px}}
.build-info-section h3{{font-size:11px;text-transform:uppercase;letter-spacing:.45px;color:#475569;padding:10px 12px;background:#f1f5f9;border-bottom:1px solid #dde5ee}}
.build-info-table{{width:100%;border-collapse:collapse;font-size:11px}}
.build-info-table th,.build-info-table td{{padding:8px 10px;text-align:left;border-bottom:1px solid #edf1f5;vertical-align:top}}
.build-info-table th{{font-size:9px;text-transform:uppercase;letter-spacing:.35px;color:#64748b;background:#fbfcfe}}
.build-info-table tr:last-child td{{border-bottom:none}}
.build-info-pill{{display:inline-flex;padding:2px 6px;border-radius:999px;font-size:9px;font-weight:900;white-space:nowrap}}
.build-info-pill.ok{{background:#dcfce7;color:#166534}}
.build-info-pill.warning{{background:#fef3c7;color:#92400e}}
.build-info-pill.error{{background:#fee2e2;color:#991b1b}}
.build-info-pill.info{{background:#e2e8f0;color:#475569}}
@media(max-width:720px){{.build-info-cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.topnav-stamp{{display:none}}}}
.frame-wrap{{height:calc(100vh - 56px);display:flex;flex-direction:column}}
iframe{{flex:1;width:100%;border:none;display:none}}
iframe.active{{display:block}}
.vz-day-btn{{padding:7px 14px;border:1.5px solid #9db9d5;background:#fff;color:#1e6091;border-radius:7px;cursor:pointer;font-weight:800;font-size:12px;font-family:'Segoe UI',Arial,sans-serif;transition:all .15s;letter-spacing:.1px}}
.vz-day-btn:hover{{background:#eaf3fb;border-color:#1e6091}}
.vz-day-btn.active{{background:linear-gradient(180deg,#2f80b7 0%,#1e6091 100%);color:#fff;border-color:#164e75;box-shadow:0 2px 7px rgba(30,96,145,.28)}}
</style>
</head>
<body>

<nav class="topnav">
  <div class="topnav-logo-wrap">
    <img class="topnav-logo" src="{logo_data_url}" alt="Nordfrische Center Logo">
  </div>
  <div class="nav-sep"></div>
  <div class="nav-dd" id="dd-suche">
    <button class="nav-dd-btn active" id="btn-suche" onclick="ddToggle('suche',event)">
      &#128269; Suche <span id="inst-label-suche"></span><span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-suche"></div>
  </div>
  <div class="nav-dd" id="dd-vz">
    <button class="nav-dd-btn" id="btn-vz" onclick="ddToggle('vz',event)">
      &#128703; Fahrzeugw&#228;sche <span id="inst-label-vz"></span><span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-vz"></div>
  </div>
  <div class="nav-dd" id="dd-verstoss">
    <button class="nav-dd-btn" id="btn-verstoss" onclick="ddToggle('verstoss',event)">
      &#9888;&#65039; Versto&#223;auswertung <span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-verstoss"></div>
  </div>
  <div class="nav-dd" id="dd-sam">
    <button class="nav-dd-btn" id="btn-sam" onclick="ddToggle('sam',event)">
      &#128664; Sa + So Einsätze <span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-sam"></div>
  </div>
  <div class="nav-dd" id="dd-fa">
    <button class="nav-dd-btn" id="btn-fa" onclick="ddToggle('fa',event)">
      &#128101; Fahrerauswertung <span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-fa"></div>
  </div>
  <button class="nav-btn" id="btn-zulage" onclick="showArea('zulage')">&#128176; Zulagen</button>
  <button class="nav-btn" id="btn-spesen" onclick="showArea('spesen')">&#128181; Spesen</button>
  <button class="nav-btn" id="btn-gk" onclick="showArea('gk')">&#127970; Gro&#223;kunden</button>
  <div class="nav-dd" id="dd-sped">
    <button class="nav-dd-btn" id="btn-sped" onclick="ddToggle('sped',event)">
      &#128666; Spediteure <span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-sped"></div>
  </div>
  <div class="nav-dd" id="dd-wa">
    <button class="nav-dd-btn" id="btn-wa" onclick="ddToggle('wa',event)">
      &#128202; Wochenauslastung <span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-wa"></div>
  </div>
  <div class="nav-dd" id="dd-infos">
    <button class="nav-dd-btn" id="btn-infos" onclick="ddToggle('infos',event)">
      &#128203; Infos &amp; Aush&#228;nge <span class="dd-arrow">&#9660;</span>
    </button>
    <div class="dd-menu" id="ddmenu-infos"></div>
  </div>
  </div>
  <span class="topnav-stamp">v{APP_DISPLAY_VERSION} &middot; {last_updated}</span>
</nav>

<div class="build-info-overlay" id="buildInfoOverlay" onclick="buildInfoBackdrop(event)">
  <div class="build-info-dialog" role="dialog" aria-modal="true" aria-labelledby="buildInfoTitle">
    <div class="build-info-head">
      <div><div class="build-info-title" id="buildInfoTitle">Datenstand und Version</div><div class="build-info-sub">Technische Informationen zur aktuell geöffneten Einzeldatei</div></div>
      <button class="build-info-close" type="button" onclick="closeBuildInfo()" aria-label="Schliessen">&times;</button>
    </div>
    <div class="build-info-body" id="buildInfoContent"></div>
  </div>
</div>
<script>
const APP_GENERATION_META = {generation_meta_json};
function buildInfoEsc(value){{return String(value==null?"":value).replace(/[&<>"']/g,function(ch){{return {{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[ch];}});}}
function buildInfoBytes(value){{var n=Number(String(value||"0").replace(/^H/,""))||0;if(n<1024)return n+" B";if(n<1048576)return(n/1024).toFixed(1)+" KB";return(n/1048576).toFixed(1)+" MB";}}
function buildInfoDuration(value){{var n=Number(value)||0;return n<1000?n+" ms":(n/1000).toFixed(2)+" s";}}
function renderBuildInfo(){{
  var m=APP_GENERATION_META||{{}},stats=m.stats||{{}};
  var cards=[["Version","v"+(m.version||"-")],["Erstellt",m.created_at||"-"],["HTML-Datei",buildInfoBytes(m.html_bytes)],["Erzeugung",buildInfoDuration(m.generation_ms)]];
  var html='<div class="build-info-cards">'+cards.map(function(c){{return '<div class="build-info-card"><div class="build-info-card-label">'+buildInfoEsc(c[0])+'</div><div class="build-info-card-value">'+buildInfoEsc(c[1])+'</div></div>';}}).join('')+'</div>';
  var datasets=Array.isArray(m.datasets)?m.datasets:[];
  if(datasets.length)html+='<div class="build-info-section"><h3>Datenumfang</h3><table class="build-info-table"><thead><tr><th>Bereich</th><th>Umfang</th><th>Hinweis</th></tr></thead><tbody>'+datasets.map(function(d){{return '<tr><td><b>'+buildInfoEsc(d.label)+'</b></td><td>'+buildInfoEsc(d.value)+'</td><td>'+buildInfoEsc(d.detail||'')+'</td></tr>';}}).join('')+'</tbody></table></div>';
  var sources=Array.isArray(m.sources)?m.sources:[];
  if(sources.length)html+='<div class="build-info-section"><h3>Verarbeitete Quellen</h3><table class="build-info-table"><thead><tr><th>Status</th><th>Bereich</th><th>Datei</th><th>Details</th><th>Zeit</th></tr></thead><tbody>'+sources.map(function(r){{var st=r.status||'info';var label=st==='ok'?'OK':st==='warning'?'Hinweis':st==='error'?'Fehler':'Info';return '<tr><td><span class="build-info-pill '+buildInfoEsc(st)+'">'+buildInfoEsc(label)+'</span></td><td><b>'+buildInfoEsc(r.area||'')+'</b></td><td>'+buildInfoEsc(r.file||'')+'</td><td>'+buildInfoEsc(r.detail||'')+'</td><td>'+buildInfoEsc(r.time||'')+'</td></tr>';}}).join('')+'</tbody></table></div>';
  html+='<div class="build-info-section"><h3>Zusammenfassung</h3><table class="build-info-table"><tbody><tr><td><b>Wochen</b></td><td>'+buildInfoEsc((m.weeks||[]).join(', ')||'Keine')+'</td></tr><tr><td><b>Eingebettete PDFs</b></td><td>'+buildInfoEsc(stats.pdf_count||0)+' Dokumente, '+buildInfoEsc(buildInfoBytes(stats.pdf_compressed_bytes||0))+' komprimiert</td></tr><tr><td><b>Quellenstatus</b></td><td>'+buildInfoEsc(stats.ok||0)+' erfolgreich, '+buildInfoEsc(stats.warning||0)+' Hinweise, '+buildInfoEsc(stats.error||0)+' Fehler</td></tr></tbody></table></div>';
  var target=document.getElementById('buildInfoContent');if(target)target.innerHTML=html;
}}
function openBuildInfo(){{renderBuildInfo();var el=document.getElementById('buildInfoOverlay');if(el)el.classList.add('open');}}
function closeBuildInfo(){{var el=document.getElementById('buildInfoOverlay');if(el)el.classList.remove('open');}}
function buildInfoBackdrop(event){{if(event&&event.target&&event.target.id==='buildInfoOverlay')closeBuildInfo();}}
document.addEventListener('keydown',function(e){{if(e.key==='Escape')closeBuildInfo();}});
</script>

<div class="frame-wrap">
  <iframe id="frame-suche" class="active" title="Kunden-Suche"></iframe>
  <iframe id="frame-druck" title="Druckbereich" style="display:none!important;width:0;height:0;border:0"></iframe>
  <div id="panel-vz" style="display:none;flex:1;overflow-y:auto;padding:18px 18px 28px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div style="width:100%;max-width:1728px;margin:0 auto">

      <!-- ── Header-Karte mit Titel + Aktionen ─────────────────────────────── -->
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:18px 22px;box-shadow:0 2px 10px rgba(30,96,145,.08);margin-bottom:18px">
        <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px">
          <div style="display:flex;align-items:center;gap:12px;min-width:0;flex:1">
            <div style="width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#1e6091 0%,#2f80b7 100%);display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 2px 7px rgba(30,96,145,.25);flex-shrink:0">&#128703;</div>
            <div style="min-width:0">
              <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Fahrzeugw&#228;sche</h2>
              <p style="color:#64748b;font-size:12px;margin:2px 0 0 0;font-weight:500">Tag und Datum ausw&#228;hlen, dann PDF exportieren</p>
            </div>
          </div>
          <button onclick="fwExportPdf()" style="padding:10px 18px;background:linear-gradient(180deg,#ef4444 0%,#dc2626 100%);color:#fff;border:none;border-radius:8px;font-weight:800;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(220,38,38,.28);display:inline-flex;align-items:center;gap:7px;transition:all .15s" onmouseover="this.style.transform='translateY(-1px)';this.style.boxShadow='0 4px 10px rgba(220,38,38,.35)'" onmouseout="this.style.transform='';this.style.boxShadow='0 2px 6px rgba(220,38,38,.28)'">
            &#128196; Fahrzeugw&#228;schen PDF
          </button>
        </div>

        <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;padding-top:14px;border-top:1px solid #eef2f7">
          <div style="flex:1;min-width:280px">
            <div style="font-size:11px;font-weight:800;color:#1e6091;margin-bottom:8px;letter-spacing:.4px;text-transform:uppercase">Tag</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px" id="fw-day-btns">
              <button class="vz-day-btn" onclick="fwSelectDay('Montag')">Montag</button>
              <button class="vz-day-btn" onclick="fwSelectDay('Dienstag')">Dienstag</button>
              <button class="vz-day-btn" onclick="fwSelectDay('Mittwoch')">Mittwoch</button>
              <button class="vz-day-btn" onclick="fwSelectDay('Donnerstag')">Donnerstag</button>
              <button class="vz-day-btn" onclick="fwSelectDay('Freitag')">Freitag</button>
              <button class="vz-day-btn" onclick="fwSelectDay('Samstag')">Samstag</button>
            </div>
          </div>
          <div style="flex-shrink:0">
            <div style="font-size:11px;font-weight:800;color:#1e6091;margin-bottom:8px;letter-spacing:.4px;text-transform:uppercase">Datum</div>
            <input id="fw-date-picker" type="date" onchange="fwSetDate(this.value)"
              style="padding:9px 12px;border:1.5px solid #9db9d5;border-radius:7px;font-size:13px;font-weight:700;font-family:inherit;outline:none;background:#fff;color:#0b1220;min-width:180px;box-shadow:inset 0 1px 2px rgba(15,23,42,.04)">
          </div>
        </div>
      </div>

      <!-- ── Gesamt-Banner ──────────────────────────────────────────────── -->
      <div id="fw-total-banner" style="margin-bottom:14px"></div>

      <!-- ── Übersicht-Karte ───────────────────────────────────────────────── -->
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;box-shadow:0 2px 10px rgba(30,96,145,.07);overflow:hidden;margin-bottom:14px">

        <div style="display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 20px;border-bottom:1px solid #eef2f7;background:linear-gradient(180deg,#f8fbff 0%,#ffffff 100%)">
          <div style="display:flex;align-items:center;gap:10px">
            <div style="width:32px;height:32px;border-radius:8px;background:#e7f1fb;color:#1e6091;display:flex;align-items:center;justify-content:center;font-size:15px">&#128203;</div>
            <div>
              <div style="font-size:13px;font-weight:900;color:#0f172a;letter-spacing:-.1px">&#220;bersicht Fahrzeugw&#228;sche</div>
              <div style="font-size:11px;color:#64748b;margin-top:1px">Wer hat wann welchen LKW gewaschen</div>
            </div>
          </div>
          <div id="fw-overview-stats" style="display:flex;gap:6px;flex-wrap:wrap"></div>
        </div>

        <div style="padding:14px 20px;border-bottom:1px solid #eef2f7;background:#fbfcfd;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
          <select id="fw-overview-year" onchange="fwRenderOverview()"
            style="min-width:150px;padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:7px;font-size:12px;font-weight:600;font-family:inherit;background:#fff;color:#0f172a;outline:none;cursor:pointer;box-shadow:inset 0 1px 2px rgba(15,23,42,.03);transition:border-color .15s"
            onfocus="this.style.borderColor='#1e6091'" onblur="this.style.borderColor='#b9cce3'"></select>
          <select id="fw-overview-driver" onchange="fwRenderOverview()"
            style="min-width:260px;padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:7px;font-size:12px;font-weight:600;font-family:inherit;background:#fff;color:#0f172a;outline:none;cursor:pointer;box-shadow:inset 0 1px 2px rgba(15,23,42,.03);transition:border-color .15s"
            onfocus="this.style.borderColor='#1e6091'" onblur="this.style.borderColor='#b9cce3'"></select>
        </div>

        <div id="fw-overview-table"></div>
      </div>

      <!-- ── Rangliste ───────────────────────────────────────────────────── -->
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;box-shadow:0 2px 10px rgba(30,96,145,.07);overflow:hidden">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 20px;border-bottom:1px solid #eef2f7;background:linear-gradient(180deg,#f8fbff 0%,#ffffff 100%)">
          <div style="display:flex;align-items:center;gap:10px">
            <div style="width:32px;height:32px;border-radius:8px;background:#fff7e6;color:#9a5b00;display:flex;align-items:center;justify-content:center;font-size:17px">&#127942;</div>
            <div>
              <div style="font-size:13px;font-weight:900;color:#0f172a;letter-spacing:-.1px">Rangliste Fahrzeugw&#228;sche</div>
              <div style="font-size:11px;color:#64748b;margin-top:1px">Wer hat am meisten gewaschen &ndash; PDF-Export pro Fahrer</div>
            </div>
          </div>
        </div>
        <div id="fw-ranking-body"></div>
      </div>

      <!-- ── LKW-Rangliste ───────────────────────────────────────────────── -->
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;box-shadow:0 2px 10px rgba(30,96,145,.07);overflow:hidden;margin-top:14px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 20px;border-bottom:1px solid #eef2f7;background:linear-gradient(180deg,#f8fbff 0%,#ffffff 100%)">
          <div style="display:flex;align-items:center;gap:10px">
            <div style="width:32px;height:32px;border-radius:8px;background:#e7f1fb;color:#1e6091;display:flex;align-items:center;justify-content:center;font-size:17px">&#128666;</div>
            <div>
              <div style="font-size:13px;font-weight:900;color:#0f172a;letter-spacing:-.1px">Rangliste LKW</div>
              <div style="font-size:11px;color:#64748b;margin-top:1px">Welche Fahrzeuge am meisten gewaschen wurden &ndash; PDF-Export pro LKW</div>
            </div>
          </div>
        </div>
        <div id="fw-lkw-ranking-body"></div>
      </div>

    </div>
  </div>

  <!-- ── Fahrzeugwäsche Graph Panel ──────────────────────────────────────── -->
  <div id="panel-vz-graph" style="display:none;flex:1;flex-direction:column;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;overflow:hidden;">
    <div style="width:100%;max-width:1728px;margin:0 auto;display:flex;flex-direction:column;flex:1;overflow:hidden;">
      <div style="display:flex;align-items:center;gap:10px;padding:16px 18px;flex-wrap:wrap;flex-shrink:0;">
        <h2 style="margin:0;font-size:17px;font-weight:900;color:#0f172a;">&#128703; Fahrzeugw&#228;sche &ndash; Graph pro Jahr</h2>
        <select id="fw-graph-mode" onchange="fwGraphSetMode(this.value)" title="Ansicht"
          style="padding:8px 11px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12px;font-weight:900;font-family:inherit;background:#fff;color:#1f3347;outline:none;cursor:pointer;">
          <option value="single">Ein Jahr</option>
          <option value="compare">Jahre vergleichen</option>
        </select>
        <select id="fw-graph-year" onchange="fwGraphSetJahr(this.value)" title="Jahr 1"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#1e6091;cursor:pointer;"></select>
        <select id="fw-graph-year-2" onchange="fwGraphSetJahr2(this.value)" title="Jahr 2"
          style="display:none;padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#047857;cursor:pointer;"></select>
        <span id="fw-graph-stats" style="font-size:12px;font-weight:700;color:#64748b;margin-left:auto;"></span>
      </div>
      <div id="fw-graph-content" style="flex:1;overflow-y:auto;padding:4px 0 30px 18px;">
        <div style="color:#94a3b8;padding:60px;text-align:center;font-size:14px;">Keine Fahrzeugw&#228;sche-Daten &ndash; bitte Waschdatei in Streamlit hochladen.</div>
      </div>
    </div>
  </div>

  <div id="panel-tel" style="display:none;flex:1;overflow-y:auto;font-family:'Segoe UI',Arial,sans-serif;">
    <style>
      #panel-tel{{--tink:#0f1f33;--tacc:#1e6091;background:linear-gradient(180deg,#eef3f9 0%,#f5f8fc 100%);}}
      #panel-tel .tel-wrap{{max-width:1060px;margin:0 auto;padding:0 20px 40px;}}
      #panel-tel .tel-head{{position:sticky;top:0;z-index:5;background:linear-gradient(180deg,#f3f7fc 0%,rgba(243,247,252,.92) 100%);backdrop-filter:blur(6px);padding:18px 0 14px;margin-bottom:6px;}}
      #panel-tel .tel-title{{display:flex;align-items:center;gap:12px;margin-bottom:12px;}}
      #panel-tel .tel-ico{{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,#1e6091,#3aa0d8);color:#fff;display:flex;align-items:center;justify-content:center;font-size:19px;box-shadow:0 8px 18px rgba(30,96,145,.25);flex-shrink:0;}}
      #panel-tel .tel-h{{font-size:19px;font-weight:950;letter-spacing:-.4px;color:var(--tink);line-height:1.05;}}
      #panel-tel .tel-sub{{font-size:11.5px;font-weight:700;color:#64748b;margin-top:2px;}}
      #panel-tel .tel-count{{margin-left:auto;font-size:12px;font-weight:850;color:var(--tacc);background:#e8f1fb;border:1px solid #cfe0f1;border-radius:999px;padding:4px 12px;white-space:nowrap;font-variant-numeric:tabular-nums;}}
      #panel-tel .tel-controls{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}}
      #panel-tel .tel-searchwrap{{position:relative;flex:1;min-width:220px;max-width:460px;}}
      #panel-tel .tel-sicon{{position:absolute;left:13px;top:50%;transform:translateY(-50%);font-size:13px;opacity:.5;pointer-events:none;}}
      #panel-tel .tel-search{{width:100%;padding:10px 14px 10px 35px;border:1.5px solid #cdddee;border-radius:11px;font-size:13px;font-family:inherit;font-weight:600;outline:none;background:#fff;color:var(--tink);box-shadow:0 1px 3px rgba(30,96,145,.05);transition:border-color .14s,box-shadow .14s;}}
      #panel-tel .tel-search:focus{{border-color:var(--tacc);box-shadow:0 0 0 3px rgba(30,96,145,.13);}}
      #panel-tel .tel-pdf{{display:inline-flex;align-items:center;gap:7px;padding:10px 16px;border:1.5px solid #cdddee;border-radius:11px;background:#fff;color:var(--tacc);font-size:12.5px;font-weight:850;font-family:inherit;cursor:pointer;white-space:nowrap;transition:background .12s,border-color .12s;}}
      #panel-tel .tel-pdf:hover{{background:#eef5fc;border-color:#bcd4ec;}}
      #panel-tel .tel-group{{margin-bottom:20px;}}
      #panel-tel .tel-group-h{{display:flex;align-items:center;gap:9px;margin-bottom:9px;padding-bottom:7px;border-bottom:1.5px solid #dbe7f3;}}
      #panel-tel .tel-group-n{{font-size:12px;font-weight:950;text-transform:uppercase;letter-spacing:.6px;color:var(--tacc);}}
      #panel-tel .tel-group-c{{font-size:10.5px;font-weight:800;color:#64748b;background:#fff;border:1px solid #dbe7f3;border-radius:999px;padding:1px 8px;font-variant-numeric:tabular-nums;}}
      #panel-tel .tel-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(268px,1fr));gap:10px;}}
      #panel-tel .tel-card{{display:flex;align-items:center;gap:12px;background:#fff;border:1px solid #e2e8f0;border-radius:13px;padding:11px 12px;box-shadow:0 3px 10px rgba(15,31,51,.04);transition:transform .12s,box-shadow .12s,border-color .12s;}}
      #panel-tel .tel-card:hover{{transform:translateY(-1px);box-shadow:0 8px 20px rgba(15,31,51,.09);border-color:#cfe0f1;}}
      #panel-tel .tel-av{{flex-shrink:0;width:38px;height:38px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:900;letter-spacing:.3px;}}
      #panel-tel .tel-body{{min-width:0;flex:1;display:flex;flex-direction:column;gap:2px;}}
      #panel-tel .tel-name{{font-size:13.5px;font-weight:850;color:var(--tink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      #panel-tel .tel-num{{display:inline-flex;align-items:center;gap:6px;font-size:13.5px;font-weight:800;color:var(--tacc);text-decoration:none;font-variant-numeric:tabular-nums;letter-spacing:.2px;width:fit-content;}}
      #panel-tel .tel-num:hover{{text-decoration:underline;}}
      #panel-tel .tel-num-i{{color:#7aa0c4;font-weight:400;font-size:12px;}}
      #panel-tel .tel-role{{display:inline-block;width:fit-content;margin-top:3px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.4px;color:#92400e;background:#fef3c7;border:1px solid #fcd34d;border-radius:999px;padding:1px 8px;}}
      #panel-tel .tel-mail{{display:inline-block;width:fit-content;margin-top:2px;font-size:11px;font-weight:700;color:#64748b;text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;}}
      #panel-tel .tel-mail:hover{{color:var(--tacc);text-decoration:underline;}}
      #panel-tel .tel-copy{{flex-shrink:0;width:32px;height:32px;border-radius:9px;border:1px solid #e2e8f0;background:#f8fafc;color:#64748b;cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;transition:background .12s,color .12s,border-color .12s;}}
      #panel-tel .tel-copy:hover{{background:#eef5fc;color:var(--tacc);border-color:#cfe0f1;}}
      #panel-tel .tel-copy.copied{{background:#ecfdf5;color:#16a34a;border-color:#bbf7d0;}}
      #panel-tel .tel-empty{{color:#94a3b8;padding:60px;text-align:center;font-size:14px;}}
      @media (prefers-reduced-motion:reduce){{#panel-tel .tel-card{{transition:none;}}}}
    </style>
    <div class="tel-wrap">
      <div class="tel-head">
        <div class="tel-title">
          <span class="tel-ico">&#128222;</span>
          <div>
            <div class="tel-h">Telefonliste</div>
            <div class="tel-sub">Fachberater &amp; Kontakte &middot; klick auf die Nummer zum Anrufen</div>
          </div>
          <span id="tel-count" class="tel-count"></span>
        </div>
        <div class="tel-controls">
          <div class="tel-searchwrap">
            <span class="tel-sicon">&#128269;</span>
            <input id="tel-search" class="tel-search" placeholder="Name oder Nummer suchen..." oninput="telFilter(this.value)">
          </div>
          <button class="tel-pdf" onclick="telPDF()">&#128196; PDF</button>
        </div>
      </div>
      <div id="tel-content" class="tel-content"></div>
    </div>
  </div>

  <div id="panel-bus" style="display:none;flex:1;overflow-y:auto;padding:18px 18px 28px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div style="width:100%;max-width:1180px;margin:0 auto">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:18px 22px;box-shadow:0 2px 10px rgba(30,96,145,.08);margin-bottom:18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#1e6091 0%,#2f80b7 100%);display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 2px 7px rgba(30,96,145,.25);flex-shrink:0">&#128652;</div>
        <div style="min-width:0;flex:1">
          <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Busfahrplan Mitarbeiterbus</h2>
          <p style="color:#64748b;font-size:12px;margin:2px 0 0 0;font-weight:500">Fahrplan &amp; Notfallplan &middot; Becker Tours</p>
        </div>
        <span style="background:linear-gradient(180deg,#fde047 0%,#facc15 100%);color:#713f12;font-weight:900;font-size:12px;padding:7px 14px;border-radius:8px;border:1px solid #eab308;box-shadow:0 2px 6px rgba(234,179,8,.25);white-space:nowrap">g&uuml;ltig ab KW&nbsp;23</span>
        <button onclick="busPDF()" style="padding:10px 18px;background:linear-gradient(180deg,#ef4444 0%,#dc2626 100%);color:#fff;border:none;border-radius:8px;font-weight:800;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(220,38,38,.28);display:inline-flex;align-items:center;gap:7px;white-space:nowrap">&#128196; PDF / Drucken</button>
      </div>
      <div id="bus-content"></div>
    </div>
  </div>

  <div id="panel-arzt" style="display:none;flex:1;overflow-y:auto;padding:18px 18px 28px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div style="width:100%;max-width:880px;margin:0 auto">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:18px 22px;box-shadow:0 2px 10px rgba(30,96,145,.08);margin-bottom:18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#0e7490 0%,#0891b2 100%);display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 2px 7px rgba(8,145,178,.25);flex-shrink:0">&#129658;</div>
        <div style="min-width:0;flex:1">
          <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Betriebs&#228;rztin</h2>
          <p style="color:#64748b;font-size:12px;margin:2px 0 0 0;font-weight:500">Aushang &middot; ASIG / ArbSchG / DGUV Vorschrift 2</p>
        </div>
        <button onclick="arztPDF()" style="padding:10px 18px;background:linear-gradient(180deg,#ef4444 0%,#dc2626 100%);color:#fff;border:none;border-radius:8px;font-weight:800;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(220,38,38,.28);display:inline-flex;align-items:center;gap:7px;white-space:nowrap">&#128196; PDF / Drucken</button>
      </div>
      <div id="arzt-content"></div>
    </div>
  </div>

  <div id="panel-versp" style="display:none;flex:1;overflow-y:auto;padding:18px 18px 28px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div style="width:100%;max-width:880px;margin:0 auto">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:18px 22px;box-shadow:0 2px 10px rgba(30,96,145,.08);margin-bottom:18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#2f80b7 0%,#1e6091 100%);display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 2px 7px rgba(30,96,145,.25);flex-shrink:0">&#9201;&#65039;</div>
        <div style="min-width:0;flex:1">
          <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Verspätungstabelle</h2>
          <p style="color:#64748b;font-size:12px;margin:2px 0 0 0;font-weight:500">Tag wählen &middot; Excel mit Kunden &amp; Touren zum Eintragen der Abfahrtszeiten</p>
        </div>
      </div>
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:18px 22px;box-shadow:0 2px 10px rgba(30,96,145,.07)">
        <div id="versp-inst-wrap" style="display:none;margin-bottom:16px">
          <div style="font-size:12px;font-weight:900;color:#0f172a;text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px">Woche / Quelldatei</div>
          <select id="versp-inst-sel" onchange="verspSelectInst(this.value)"
            style="padding:8px 12px;border:2px solid #1e6091;border-radius:7px;font-size:13px;font-weight:700;color:#1e6091;cursor:pointer;font-family:inherit;outline:none;background:#fff;min-width:220px"></select>
        </div>
        <div style="font-size:12px;font-weight:900;color:#0f172a;text-transform:uppercase;letter-spacing:.4px;margin-bottom:10px">Liefertag</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px" id="versp-day-btns">
          <button class="vz-day-btn" onclick="verspSelectDay('Montag')">Montag</button>
          <button class="vz-day-btn" onclick="verspSelectDay('Dienstag')">Dienstag</button>
          <button class="vz-day-btn" onclick="verspSelectDay('Mittwoch')">Mittwoch</button>
          <button class="vz-day-btn" onclick="verspSelectDay('Donnerstag')">Donnerstag</button>
          <button class="vz-day-btn" onclick="verspSelectDay('Freitag')">Freitag</button>
          <button class="vz-day-btn" onclick="verspSelectDay('Samstag')">Samstag</button>
        </div>
        <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px">
          <button id="versp-dl-btn" onclick="verspDownload()" disabled
            style="padding:11px 20px;background:linear-gradient(180deg,#16a34a 0%,#15803d 100%);color:#fff;border:none;border-radius:8px;font-weight:800;font-size:13px;cursor:default;font-family:inherit;box-shadow:0 2px 6px rgba(21,128,61,.28);display:inline-flex;align-items:center;gap:8px;white-space:nowrap;opacity:.5">&#11015;&#65039; Excel herunterladen</button>
          <div id="versp-info" style="font-size:13px;color:#64748b">Bitte einen Tag auswählen.</div>
        </div>
        <div style="display:flex;align-items:flex-start;gap:9px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 13px;margin-bottom:16px;font-size:12.5px;color:#92400e;line-height:1.45">
          <span style="font-size:15px;line-height:1.2;flex-shrink:0">&#9888;&#65039;</span>
          <span>Die <b>reguläre Abfahrtszeit</b> wird automatisch aus den Tourenplänen ermittelt (Abendtouren über den Vortag). Bitte trotzdem einmal <b>logisch überprüfen</b>, bevor die Tabelle weiterverwendet wird.</span>
        </div>
        <div id="versp-preview"></div>
      </div>
    </div>
  </div>

  <div id="panel-knapp" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;height:auto;min-height:100%;max-width:1440px;margin:0 auto;display:flex;flex-direction:column">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);margin-bottom:14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;flex-shrink:0">
        <div style="width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#f28c28 0%,#d96712 100%);color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 2px 7px rgba(217,103,18,.28);flex-shrink:0">&#9881;&#65039;</div>
        <div style="min-width:0;flex:1">
          <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">KNAPP</h2>
          <p style="color:#64748b;font-size:12px;margin:2px 0 0 0;font-weight:500">Servicedesk-Eskalation P1/P2 &middot; Eskalationspyramide und Verantwortlichkeiten &middot; Stand 07.07.2026</p>
        </div>
        <button onclick="knappOpenPdf()" style="padding:10px 16px;background:linear-gradient(180deg,#f28c28 0%,#d96712 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(217,103,18,.28);display:inline-flex;align-items:center;gap:7px;white-space:nowrap">&#128196; PDF öffnen</button>
        <button onclick="knappDownloadPdf()" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px;white-space:nowrap">&#11015;&#65039; Herunterladen</button>
      </div>

      <div style="background:#fff;border:1px solid #cad7e8;border-left:5px solid #d96712;border-radius:12px;padding:14px 18px;box-shadow:0 2px 10px rgba(30,96,145,.07);margin-bottom:14px;flex-shrink:0;color:#334155">
        <div style="display:flex;align-items:center;gap:9px;margin-bottom:9px">
          <span style="font-size:17px">&#9888;&#65039;</span>
          <div style="font-size:13px;font-weight:900;color:#0f172a;text-transform:uppercase;letter-spacing:.35px">Ablauf bei kritischen Störungen</div>
        </div>
        <ul style="margin:0 0 10px 23px;padding:0;font-size:13px;line-height:1.55;font-weight:600">
          <li style="margin-bottom:4px">Jegliche Tickets in kritischen Bereichen müssen bei der Hotline mit <b>Prio&nbsp;1</b> angelegt werden.</li>
          <li style="margin-bottom:4px">Ab <b>2&nbsp;Stunden Lagerstillstand</b> muss Christian Henning telefonisch informiert werden.</li>
          <li style="margin-bottom:4px">Anschließend muss alle <b>30&nbsp;Minuten</b> ein Update über die Situation erfolgen.</li>
          <li>Ab <b>4&nbsp;Stunden Lagerstillstand</b> werden weitere Schritte situativ entschieden.</li>
        </ul>
        <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:9px 12px;font-size:13px;font-weight:800;color:#9a3412">
          Die Eskalation bei KNAPP an die nächsten Stufen übernimmt <b>Christian Henning</b>.
        </div>
      </div>

      <div style="position:relative;flex:none;height:clamp(760px,calc(100vh - 120px),1200px);min-height:760px;background:#fff;border:1px solid #cad7e8;border-radius:12px;box-shadow:0 2px 10px rgba(30,96,145,.08);overflow:hidden;margin-bottom:18px">
        <iframe id="knapp-pdf-frame" title="KNAPP Eskalationspyramide" style="display:none;width:100%;height:100%;min-height:760px;border:0;background:#eef2f7"></iframe>
        <div id="knapp-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="knappOpenPdf()" style="padding:10px 18px;background:#d96712;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF separat öffnen</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Schlüsselübergabe ───────────────────────────────────────────────── -->
  <div id="panel-schluessel" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#23906a 0%,#187454 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(24,116,84,.25);flex-shrink:0">&#128273;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Schl&#252;ssel&#252;bergabe</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">Protokoll direkt anzeigen, ausdrucken oder herunterladen</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('schluessel')" style="padding:10px 16px;background:linear-gradient(180deg,#23906a 0%,#187454 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(24,116,84,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('schluessel')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="schluessel-pdf-frame" title="Schlüsselübergabe Protokoll" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="schluessel-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('schluessel')" style="padding:10px 18px;background:#187454;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Entgeltportal ───────────────────────────────────────────────────── -->
  <div id="panel-entgelt" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#4f87e8 0%,#315fb7 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(49,95,183,.25);flex-shrink:0">&#128196;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Entgeltportal</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">Hinweis zu verlorenen Zugangsdaten direkt anzeigen und ausdrucken</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('entgelt')" style="padding:10px 16px;background:linear-gradient(180deg,#4f87e8 0%,#315fb7 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(49,95,183,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('entgelt')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="entgelt-pdf-frame" title="Entgeltportal Zugang" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="entgelt-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('entgelt')" style="padding:10px 18px;background:#315fb7;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Schadenmeldung Fuhrpark ──────────────────────────────────────────── -->
  <div id="panel-schaden" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#dc2626 0%,#991b1b 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(153,27,27,.25);flex-shrink:0">&#128663;&#65039;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Schadenmeldung Fuhrpark</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">Formular direkt anzeigen, ausdrucken oder herunterladen</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('schaden')" style="padding:10px 16px;background:linear-gradient(180deg,#dc2626 0%,#991b1b 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(153,27,27,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('schaden')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="schaden-pdf-frame" title="Schadenmeldung Fuhrpark" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="schaden-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('schaden')" style="padding:10px 18px;background:#991b1b;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Mängelanzeige Fuhrpark ──────────────────────────────────────────── -->
  <div id="panel-maengel" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#e59b16 0%,#b86b08 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(184,107,8,.25);flex-shrink:0">&#9888;&#65039;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">M&#228;ngelanzeige Fuhrpark</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">Fahrzeugm&#228;ngel dokumentieren, ausdrucken oder herunterladen</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('maengel')" style="padding:10px 16px;background:linear-gradient(180deg,#e59b16 0%,#b86b08 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(184,107,8,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('maengel')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="maengel-pdf-frame" title="Mängelanzeige Fuhrpark" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="maengel-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('maengel')" style="padding:10px 18px;background:#b86b08;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Übergabeprotokoll LKW ───────────────────────────────────────────── -->
  <div id="panel-lkw_uebergabe" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#d97706 0%,#92400e 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(146,64,14,.25);flex-shrink:0">&#128666;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">&#220;bergabeprotokoll LKW</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">LKW-&#220;bergabe dokumentieren, ausdrucken oder herunterladen</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('lkw_uebergabe')" style="padding:10px 16px;background:linear-gradient(180deg,#d97706 0%,#92400e 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(146,64,14,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('lkw_uebergabe')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="lkw_uebergabe-pdf-frame" title="Übergabeprotokoll LKW" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="lkw_uebergabe-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('lkw_uebergabe')" style="padding:10px 18px;background:#92400e;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Balzer Informationen ─────────────────────────────────────────────── -->
  <div id="panel-balzer" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#2563eb 0%,#1e3a8a 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(30,58,138,.25);flex-shrink:0">&#128230;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Balzer</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">Adresse, Anfahrt und Abladehinweise direkt anzeigen, ausdrucken oder herunterladen</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('balzer')" style="padding:10px 16px;background:linear-gradient(180deg,#2563eb 0%,#1e3a8a 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(30,58,138,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('balzer')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="balzer-pdf-frame" title="Balzer Informationen" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="balzer-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('balzer')" style="padding:10px 18px;background:#1e3a8a;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Termine Fahrer ─────────────────────────────────────────────── -->
  <div id="panel-termine" style="display:none;flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px;background:linear-gradient(180deg,#f3f7fb 0%,#e8f0f7 100%);font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;scrollbar-gutter:stable">
    <div style="width:100%;max-width:1280px;margin:0 auto;display:flex;flex-direction:column;gap:14px;min-height:100%">
      <div style="background:#fff;border:1px solid #cad7e8;border-radius:12px;padding:16px 20px;box-shadow:0 2px 10px rgba(30,96,145,.08);display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:12px;min-width:0">
          <div style="width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,#7c3aed 0%,#5b21b6 100%);display:flex;align-items:center;justify-content:center;font-size:21px;color:#fff;box-shadow:0 2px 7px rgba(91,33,182,.25);flex-shrink:0">&#128197;</div>
          <div style="min-width:0">
            <h2 style="color:#0f172a;font-size:18px;font-weight:900;margin:0;letter-spacing:-.2px">Termine Fahrer</h2>
            <p style="color:#64748b;font-size:12px;margin:3px 0 0 0;font-weight:500">Terminübersicht direkt anzeigen, ausdrucken oder herunterladen</p>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="documentPdfOpen('termine')" style="padding:10px 16px;background:linear-gradient(180deg,#7c3aed 0%,#5b21b6 100%);color:#fff;border:none;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;box-shadow:0 2px 6px rgba(91,33,182,.25);display:inline-flex;align-items:center;gap:7px">&#128424; PDF &#246;ffnen / drucken</button>
          <button onclick="documentPdfDownload('termine')" style="padding:10px 16px;background:#fff;color:#334155;border:1.5px solid #cbd5e1;border-radius:8px;font-weight:850;font-size:12px;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:7px">&#11015; PDF herunterladen</button>
        </div>
      </div>
      <div style="position:relative;flex:1;min-height:780px;background:#eef2f7;border:1px solid #cad7e8;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(15,23,42,.08)">
        <iframe id="termine-pdf-frame" title="Termine Fahrer" style="display:none;width:100%;height:calc(100vh - 170px);min-height:780px;border:0;background:#eef2f7"></iframe>
        <div id="termine-pdf-fallback" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;flex-direction:column;gap:12px;padding:30px;text-align:center;color:#64748b">
          <div style="font-size:42px">&#128196;</div>
          <div style="font-size:14px;font-weight:800;color:#334155">Die PDF-Vorschau wird von diesem Browser nicht unterstützt.</div>
          <button onclick="documentPdfOpen('termine')" style="padding:10px 18px;background:#5b21b6;color:#fff;border:none;border-radius:8px;font-weight:850;cursor:pointer">PDF &#246;ffnen / drucken</button>
        </div>
      </div>
    </div>
  </div>

  <div id="panel-sam" style="display:none;flex:1;overflow-y:auto;padding:14px;background:#e8ecf1;font-family:Segoe UI,Arial,sans-serif">
    <div style="width:100%;max-width:none;margin:0">
      <h2 id="sam-panel-title" style="color:#1b66b3;font-size:18px;font-weight:900;margin:0 0 4px 0">&#128664; Sa + So Einsätze</h2>
      <div style="display:inline-flex;align-items:flex-start;gap:6px;margin-bottom:9px;background:#fffbeb;border:1px solid #e2e8f0;border-radius:4px;padding:7px 12px;font-size:12px;color:#92400e;line-height:1.45;"><span>&#9888;&#65039;</span><span>Grundlage sind ausschließlich die tatsächlichen Schichten von der Fahrerkarte. Hofdienste sind NICHT berücksichtigt.<br><br>Die Zuordnung erfolgt primär über die MA-Nummer. Gezählt wird der Anfangstag: Samstag immer, Freitag ab 18&nbsp;Uhr als Fr&#8594;Sa und Sonntag bis einschließlich 15&nbsp;Uhr.</span></div>

      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
        <input id="sam-search" placeholder="Fahrer suchen..." oninput="samFilter(this.value)"
          style="flex:1;min-width:180px;max-width:280px;padding:7px 14px;border:2px solid #1b66b3;
                 border-radius:5px;font-size:13px;font-family:inherit;outline:none">
        <select id="sam-year-sel" onchange="samYearChange(this.value)"
          style="padding:7px 12px;border:2px solid #1b66b3;border-radius:5px;font-size:12px;font-weight:700;
                 color:#1b66b3;cursor:pointer;font-family:inherit;outline:none;background:#fff;">
        </select>
        <div id="sam-list-sort-buttons" style="display:flex;gap:5px;">
          <button onclick="samSort('status')" id="sam-sort-status"
            style="padding:6px 12px;border:2px solid #1b66b3;border-radius:5px;background:#fff;color:#1b66b3;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit;">
            Status &#8593;
          </button>
          <button onclick="samSort('name')" id="sam-sort-name"
            style="padding:6px 12px;border:2px solid #1b66b3;border-radius:5px;background:#fff;color:#1b66b3;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit;">
            Name
          </button>
          <button onclick="samSort('count')" id="sam-sort-count"
            style="padding:6px 12px;border:2px solid #1b66b3;border-radius:5px;background:#1b66b3;color:#fff;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit;">
            Eins&#228;tze &#8595;
          </button>
        </div>
        <button onclick="samExportExcel()" title="Aktuell angezeigte Einsätze als Excel herunterladen"
          style="padding:7px 13px;border:2px solid #15803d;border-radius:5px;background:#16a34a;color:#fff;font-size:11.5px;font-weight:850;cursor:pointer;font-family:inherit;white-space:nowrap;">&#11015;&#65039; Excel</button>
      </div>

      <div id="sam-chart-tabs" style="display:none;gap:5px;flex-wrap:wrap;margin:0 0 11px 0;">
        <button id="sam-chart-matrix" onclick="samSetChartMode('matrix')" style="padding:6px 11px;border:1px solid #1b66b3;border-radius:5px;background:#1b66b3;color:#fff;font-size:11px;font-weight:800;cursor:pointer;font-family:inherit;">Einsatzmatrix</button>
        <button id="sam-chart-drivers" onclick="samSetChartMode('drivers')" style="padding:6px 11px;border:1px solid #1b66b3;border-radius:5px;background:#fff;color:#1b66b3;font-size:11px;font-weight:800;cursor:pointer;font-family:inherit;">Einsätze je Fahrer</button>
        <button id="sam-chart-months" onclick="samSetChartMode('months')" style="padding:6px 11px;border:1px solid #1b66b3;border-radius:5px;background:#fff;color:#1b66b3;font-size:11px;font-weight:800;cursor:pointer;font-family:inherit;">Monatsverteilung</button>
      </div>

      <div id="sam-stats" style="margin-bottom:12px;"></div>
      <div id="sam-content"></div>
    </div>
  </div>
  <style>/* fahrerauswertung-neutral-schema */
    #panel-fa {{ background:#f8fafc !important; }}
    #panel-fa > div:first-child {{ background:#f1f5f9 !important; border-bottom-color:#cbd5e1 !important; }}
    #panel-fa h2 {{ color:#334155 !important; }}
    #fa-search, #fa-year-sel {{ border-color:#334155 !important; color:#334155 !important; }}
    #fa-sort-name {{ border-color:#94a3b8 !important; background:linear-gradient(180deg,#e2e8f0 0%,#cbd5e1 100%) !important; color:#334155 !important; box-shadow:0 1px 0 rgba(255,255,255,.55) inset !important; }}
    #fa-sort-name:hover {{ background:linear-gradient(180deg,#fef3c7 0%,#cbd5e1 100%) !important; color:#334155 !important; }}
    #fa-btn-10h {{ border-color:#dc2626 !important; background:#fff !important; color:#dc2626 !important; }}
    #fa-btn-10h:hover {{ background:#fee2e2 !important; color:#991b1b !important; }}
    #panel-fa > div:nth-child(2) > div:first-child {{ background:#f8fafc !important; border-right-color:#cbd5e1 !important; }}
    #fa-detail-panel {{ background:#f8fafc !important; }}
    #fa-detail-panel thead tr {{ background:linear-gradient(180deg,#f8fafc 0%,#cbd5e1 100%) !important; }}
    #fa-detail-panel .fa-tabs button {{ color:#334155; }}
    /* graue Texte in der Fahrerauswertung deutlich dunkler */
    #panel-fa, #panel-fa td, #panel-fa th, #panel-fa div, #panel-fa span {{ color:#111827; }}
    #panel-fa [style*="#94a3b8"],
    #panel-fa [style*="#64748b"],
    #panel-fa [style*="#475569"],
    #panel-fa [style*="#6b7280"],
    #panel-fa [style*="#9ca3af"],
    #panel-fa [style*="#8b94a7"],
    #panel-fa [style*="#718096"] {{ color:#1f2937 !important; }}
    #panel-fa small, #panel-fa .muted {{ color:#374151 !important; }}
    #panel-fa thead th, #panel-fa thead th * {{ color:#111827 !important; }}
    #panel-fa button[style*="background:#cbd5e1"],
    #panel-fa button[style*="background:linear-gradient"] {{ color:#111827 !important; }}
    #fa-stats, #fa-stats * {{ color:#1f2937 !important; }}
  </style>
  <!-- ── Fahrerauswertung Panel ───────────────────────────────────── -->
  <div id="panel-fa" style="display:none;flex:1;overflow:hidden;background:#f8fbff;font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;">
    <div style="display:flex;align-items:center;gap:10px;padding:10px 16px;background:#f1f5f9;border-bottom:1.5px solid #cbd5e1;flex-wrap:wrap;flex-shrink:0;">
      <h2 style="color:#334155;font-size:16px;font-weight:900;margin:0;">&#128101; Schichten / Tachograph</h2>
      <input id="fa-search" placeholder="Fahrer suchen..." oninput="faRender(this.value);"
        style="flex:1;min-width:130px;max-width:200px;padding:5px 12px;border:2px solid #cbd5e1;border-radius:5px;font-size:12px;font-family:inherit;outline:none">
      <select id="fa-year-sel" onchange="faYearChange(this.value)"
        style="padding:5px 10px;border:2px solid #cbd5e1;border-radius:5px;font-size:12px;font-weight:700;color:#334155;cursor:pointer;font-family:inherit;outline:none;background:#fff;">
        </select>
      <div style="display:flex;gap:4px;">
        <button onclick="faShowFahrerUebersicht()" id="fa-sort-name"
          style="padding:5px 12px;border:2px solid #94a3b8;border-radius:5px;background:linear-gradient(180deg,#e2e8f0 0%,#cbd5e1 100%);color:#334155;font-size:11px;font-weight:900;cursor:pointer;font-family:inherit;white-space:nowrap;">Fahrer Übersicht</button>
      </div>
      <button onclick="faShow10hTours('')" id="fa-btn-10h" title="Alle Schichten mit mehr als 10:00 Netto-Arbeitszeit anzeigen"
        style="padding:5px 12px;border:2px solid #dc2626;border-radius:5px;background:#fff;color:#dc2626;font-size:11px;font-weight:900;cursor:pointer;font-family:inherit;white-space:nowrap;">10H Touren</button>
      <div id="fa-stats" style="font-size:11px;color:#1f2937;margin-left:auto;font-weight:700;"></div>
    </div>
    <div style="display:flex;flex:1;overflow:hidden;">
      <div style="width:220px;flex-shrink:0;border-right:1.5px solid #cbd5e1;overflow-y:auto;background:#f1f5f9;">
        <div id="fa-sidebar-list"></div>
      </div>
      <div id="fa-detail-panel" style="flex:1;overflow-y:auto;padding:20px 24px;background:#f8fbff;"></div>
    </div>
  </div>

  <!-- ── Fahrerbewertung Panel (Dashboard) ───────────────────────────────── -->
  <div id="panel-fa-bewertung" style="display:none;flex:1;flex-direction:column;background:linear-gradient(180deg,#e8eef6 0%,#f3f7fb 100%);font-family:'Segoe UI',Arial,sans-serif;overflow:hidden;">
    <div style="width:100%;max-width:none;margin:0;display:flex;flex-direction:column;flex:1;overflow:hidden;">
      <div style="display:flex;align-items:center;gap:12px;padding:14px 18px;flex-wrap:wrap;flex-shrink:0;background:rgba(255,255,255,.78);border-bottom:1px solid #dbe4ef;box-shadow:0 8px 24px rgba(15,23,42,.055);backdrop-filter:blur(8px);">
        <div style="display:flex;align-items:center;gap:10px;margin-right:8px;">
          <div style="width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#1e3a5f,#2563eb);color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 8px 18px rgba(37,99,235,.25);">&#11088;</div>
          <div>
            <h2 style="margin:0;font-size:18px;font-weight:950;color:#0f172a;letter-spacing:-.35px;line-height:1.1;">Fahrerbewertung</h2>
            <div style="font-size:11px;font-weight:750;color:#64748b;margin-top:2px;">Dashboard · Note, Ereignisse und Verbrauch</div>
          </div>
        </div>
        <select id="fabew-month" onchange="faBewMonthChange(this.value)" title="Zeitraum auswählen (ganzes Jahr oder Monat)"
          style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:12.5px;font-weight:900;font-family:inherit;outline:none;background:#fff;color:#1d4ed8;cursor:pointer;box-shadow:0 2px 8px rgba(15,23,42,.04);"></select>
        <input id="fabew-search" placeholder="Fahrer suchen..." oninput="faBewFilter(this.value)"
          style="flex:1;min-width:220px;max-width:520px;padding:8px 14px;border:1px solid #cbd5e1;border-radius:10px;font-size:13px;font-family:inherit;font-weight:750;outline:none;background:#fff;color:#0f172a;box-shadow:0 2px 8px rgba(15,23,42,.04);">
        <button onclick="faBewExportExcel()" title="Bewertung als Excel exportieren"
          style="padding:8px 14px;border:1px solid #15803d;border-radius:10px;font-size:12px;font-weight:950;font-family:inherit;background:linear-gradient(180deg,#16a34a,#15803d);color:#fff;outline:none;cursor:pointer;box-shadow:0 8px 16px rgba(22,163,74,.18);">&#128190; Excel-Export</button>
        <span id="fabew-stats" style="font-size:12px;font-weight:750;color:#64748b;margin-left:auto;"></span>
      </div>
      <div id="fabew-content" style="flex:1;overflow-y:auto;padding:16px 18px 34px 18px;">
        <div style="color:#94a3b8;padding:60px;text-align:center;font-size:14px;">Keine Fahrerbewertungs-Daten &ndash; bitte d_rohdaten.json in Streamlit hochladen.</div>
      </div>
    </div>
  </div>

  <div id="panel-zulage" style="display:none;flex:1;flex-direction:column;background:#e8ecf1;font-family:'Segoe UI',Arial,sans-serif;overflow:hidden;">
    <div style="display:flex;align-items:center;gap:10px;padding:12px 20px;background:#f0f2f6;border-bottom:1.5px solid #c8cfd9;flex-wrap:wrap;flex-shrink:0;">
      <h2 style="margin:0;font-size:16px;font-weight:900;color:#1b66b3;">&#128176; Zulagen</h2>
      <div style="display:flex;gap:4px;">
        <button id="ztab-sonder" onclick="zulagenTab('sonder')" style="padding:3px 9px;border-radius:3px;border:1.5px solid #1b66b3;cursor:pointer;font-weight:700;font-size:12px;background:#1b66b3;color:#fff;">Sonderfahrzeuge</button>
        <button id="ztab-fuengers" onclick="zulagenTab('fuengers')" style="padding:3px 9px;border-radius:3px;border:1.5px solid #1b66b3;cursor:pointer;font-weight:700;font-size:12px;background:#fff;color:#1b66b3;">F&#252;ngers</button>
        <button id="ztab-drittkunden" onclick="zulagenTab('drittkunden')" style="padding:3px 9px;border-radius:3px;border:1.5px solid #1b66b3;cursor:pointer;font-weight:700;font-size:12px;background:#fff;color:#1b66b3;">Drittkunden</button>
      </div>
      <select id="zulage-month-sel" onchange="zulagenRender()" style="padding:5px 12px;border:2px solid #1b66b3;border-radius:5px;font-size:12px;outline:none;cursor:pointer;"></select>
      <button onclick="zulagenExportExcel()" style="padding:5px 12px;background:#1d6f42;color:#fff;border:none;border-radius:5px;font-weight:700;font-size:12px;cursor:pointer;">&#128196; Excel</button>
      <span id="zulage-stats" style="font-size:12px;color:#64748b;margin-left:auto;font-weight:600;"></span>
    </div>
    <div id="zulage-content" style="flex:1;overflow-y:auto;padding:20px;"></div>
  </div>




  <!-- ── Spesen Panel ───────────────────────────────────── -->
  <div id="panel-spesen" style="display:none;flex:1;overflow:hidden;background:#f8fbff;font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;">
    <div style="display:flex;align-items:center;gap:10px;padding:10px 16px;background:#f1f5f9;border-bottom:1.5px solid #cbd5e1;flex-wrap:wrap;flex-shrink:0;">
      <h2 style="color:#334155;font-size:16px;font-weight:900;margin:0;">&#128181; Spesen</h2>
      <input id="spesen-search" placeholder="Fahrer suchen..." oninput="spesenFilter(this.value)"
        style="flex:1;min-width:130px;max-width:220px;padding:5px 12px;border:2px solid #cbd5e1;border-radius:5px;font-size:12px;font-family:inherit;outline:none">
      <select id="spesen-month-sel" onchange="spesenMonthChange(this.value)"
        style="padding:5px 10px;border:2px solid #cbd5e1;border-radius:5px;font-size:12px;font-weight:700;color:#334155;cursor:pointer;font-family:inherit;outline:none;background:#fff;">
      </select>
      <button onclick="spesenResetView()"
        style="padding:5px 12px;border:2px solid #cbd5e1;border-radius:5px;background:#cbd5e1;color:#111827;font-size:11px;font-weight:900;cursor:pointer;font-family:inherit;white-space:nowrap;box-shadow:0 1px 3px rgba(15,23,42,.08);">Reset</button>
      <div id="spesen-stats" style="font-size:11px;color:#64748b;margin-left:auto;font-weight:700;"></div>
    </div>
    <div style="display:flex;flex:1;overflow:hidden;">
      <div style="width:240px;flex-shrink:0;border-right:1.5px solid #cbd5e1;overflow-y:auto;background:#f8fbff;">
        <div id="spesen-sidebar-list"></div>
      </div>
      <div id="spesen-detail-panel" style="flex:1;overflow-y:auto;padding:20px 24px;background:#f8fbff;">
        <div style="color:#94a3b8;padding:60px;text-align:center;font-size:14px;">Keine Spesendaten &ndash; bitte Reisekosten-CSV in Streamlit hochladen.</div>
      </div>
    </div>
  </div>

  <!-- ── Verstoßauswertung Panel ───────────────────────────────────── -->
  <div id="panel-verstoss" style="display:none;flex:1;flex-direction:column;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;overflow:hidden;">
    <div style="width:100%;margin:0 auto;display:flex;flex-direction:column;flex:1;overflow:hidden;">
      <div id="verstoss-toolbar" style="display:flex;align-items:center;gap:10px;padding:16px 18px;flex-wrap:wrap;flex-shrink:0;">
        <h2 style="margin:0;font-size:17px;font-weight:900;color:#0f172a;">&#9888;&#65039; Verstoßauswertung</h2>
        <input id="verstoss-search" placeholder="Fahrer suchen..." oninput="verstossFilter(this.value)"
          style="flex:1;min-width:160px;max-width:300px;padding:8px 14px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:13px;font-family:inherit;outline:none;background:#fff;transition:border .15s;color:#0f172a;"
          onfocus="this.style.borderColor='#1e3a5f'" onblur="this.style.borderColor='#cbd5e1'">
        <select id="verstoss-list-year" onchange="verstossListYearChange(this.value)"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:13px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#0f172a;cursor:pointer;min-width:116px;">
          <option value="all">Alle Jahre</option>
        </select>
        <button id="verstoss-search-all" onclick="verstossResetSearch()"
          style="padding:8px 14px;border:1.5px solid #1e3a5f;border-radius:8px;background:#1e3a5f;color:#fff;font-size:12px;font-weight:800;cursor:pointer;font-family:inherit;white-space:nowrap;">
          Alle anzeigen</button>
        <span id="verstoss-stats" style="font-size:12px;font-weight:700;color:#64748b;margin-left:auto;"></span>
      </div>
      <div id="verstoss-body" style="flex:1;overflow-y:auto;padding:8px 20px 30px 20px;">
        <div style="color:#94a3b8;padding:60px;text-align:center;font-size:14px;">Keine Verstoßdaten &ndash; bitte Verstoß-CSV in Streamlit hochladen.</div>
      </div>
    </div>
  </div>

  <!-- ── Verstoßauswertung Graph Panel ───────────────────────────────────── -->
  <div id="panel-verstoss-graph" style="display:none;flex:1;flex-direction:column;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;overflow:hidden;">
    <div style="width:100%;max-width:1728px;margin:0 auto;display:flex;flex-direction:column;flex:1;overflow:hidden;">
      <div style="display:flex;align-items:center;gap:10px;padding:16px 18px;flex-wrap:wrap;flex-shrink:0;">
        <h2 style="margin:0;font-size:17px;font-weight:900;color:#0f172a;">&#9888;&#65039; Versto&#223;auswertung &ndash; Graph pro Jahr</h2>
        <select id="verstoss-graph-mode" onchange="verstossGraphSetMode(this.value)" title="Ansicht"
          style="padding:8px 11px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12px;font-weight:900;font-family:inherit;background:#fff;color:#1f3347;outline:none;cursor:pointer;">
          <option value="single">Ein Jahr</option>
          <option value="compare">Jahre vergleichen</option>
        </select>
        <select id="verstoss-graph-year" onchange="verstossGraphYearChange(this.value)" title="Jahr 1"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#991b1b;cursor:pointer;"></select>
        <select id="verstoss-graph-year-2" onchange="verstossGraphYear2Change(this.value)" title="Jahr 2"
          style="display:none;padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#b45309;cursor:pointer;"></select>
        <span style="width:1px;height:24px;background:#cbd5e1;"></span>
        <select id="verstoss-graph-type" onchange="verstossGraphTypeChange(this.value)" title="Verstoßart filtern"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#0f172a;cursor:pointer;max-width:280px;">
          <option value="all">Alle Verstoßarten</option>
        </select>
        <select id="verstoss-graph-mon-from" onchange="verstossGraphMonFromChange(this.value)" title="Zeitraum: Von-Monat"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#1f3347;cursor:pointer;"></select>
        <select id="verstoss-graph-mon-to" onchange="verstossGraphMonToChange(this.value)" title="Zeitraum: Bis-Monat"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12.5px;font-weight:800;font-family:inherit;outline:none;background:#fff;color:#1f3347;cursor:pointer;"></select>
        <button onclick="verstossGraphResetFilters()" title="Verstoßart- und Zeitraum-Filter zurücksetzen"
          style="padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#475569;outline:none;cursor:pointer;">&#10005; Filter</button>
        <button onclick="verstossExportExcel()" title="Graph-Daten als Excel exportieren"
          style="padding:8px 14px;border:1.5px solid #16a34a;border-radius:8px;font-size:12px;font-weight:900;font-family:inherit;background:#16a34a;color:#fff;outline:none;cursor:pointer;">&#128190; Excel-Export</button>
        <span id="verstoss-graph-stats" style="font-size:12px;font-weight:700;color:#64748b;margin-left:auto;"></span>
      </div>
      <div id="verstoss-graph-content" style="flex:1;overflow-y:auto;padding:4px 0 30px 18px;">
        <div style="color:#94a3b8;padding:60px;text-align:center;font-size:14px;">Keine Versto&#223;daten &ndash; bitte Versto&#223;-CSV in Streamlit hochladen.</div>
      </div>
    </div>
  </div>

  <!-- ── Großkunden Panel ───────────────────────────────────────────────────── -->
  <div id="panel-wa-heat" style="display:none;flex:1;flex-direction:column;overflow:hidden;padding:16px 18px 18px;background:linear-gradient(180deg,#eef1f5 0%,#e5e9ef 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div id="wa-heat-body" style="flex:1;display:flex;flex-direction:column;min-height:0;"></div>
  </div>

  <div id="panel-wa-gruppe" style="display:none;flex:1;flex-direction:column;overflow:hidden;padding:16px 18px 18px;background:linear-gradient(180deg,#eef1f5 0%,#e5e9ef 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div id="wa-gruppe-body" style="flex:1;display:flex;flex-direction:column;min-height:0;"></div>
  </div>

  <div id="panel-wa-kurve" style="display:none;flex:1;flex-direction:column;overflow:hidden;padding:16px 18px 18px;background:linear-gradient(180deg,#eef1f5 0%,#e5e9ef 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div id="wa-kurve-body" style="flex:1;display:flex;flex-direction:column;min-height:0;"></div>
  </div>

  <div id="panel-wa-rhythm" style="display:none;flex:1;flex-direction:column;overflow:hidden;padding:16px 18px 18px;background:linear-gradient(180deg,#eef1f5 0%,#e5e9ef 100%);font-family:'Segoe UI',Arial,sans-serif">
    <div id="wa-rhythm-body" style="flex:1;display:flex;flex-direction:column;min-height:0;"></div>
  </div>

  <div id="panel-gk" style="display:none;flex:1;overflow:hidden;font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;">
    <style>
      #panel-gk{{--ink:#0f1f33;background:linear-gradient(180deg,#eef3f9 0%,#f5f8fc 100%);}}
      #panel-gk .gk-head{{flex-shrink:0;background:linear-gradient(180deg,#fbfdff 0%,#edf3fa 100%);border-bottom:1px solid #d4e0ee;padding:13px 18px 12px;}}
      #panel-gk .gk-titlerow{{display:flex;align-items:center;gap:13px;flex-wrap:wrap;margin-bottom:11px;}}
      #panel-gk .gk-title{{display:flex;align-items:center;gap:9px;font-size:16.5px;font-weight:900;letter-spacing:-.3px;color:var(--ink);white-space:nowrap;}}
      #panel-gk .gk-dot{{width:9px;height:9px;border-radius:50%;background:linear-gradient(135deg,#1e6091,#3aa0d8);box-shadow:0 0 0 4px rgba(30,96,145,.12);}}
      #panel-gk .gk-count{{font-size:11.5px;font-weight:800;color:#1e6091;background:#e8f1fb;border:1px solid #cfe0f1;border-radius:999px;padding:2px 9px;font-variant-numeric:tabular-nums;}}
      #panel-gk .gk-searchwrap{{position:relative;flex:1;min-width:200px;max-width:380px;}}
      #panel-gk .gk-sicon{{position:absolute;left:12px;top:50%;transform:translateY(-50%);font-size:13px;opacity:.5;pointer-events:none;}}
      #panel-gk .gk-search{{width:100%;padding:9px 13px 9px 33px;border:1.5px solid #cdddee;border-radius:10px;font-size:13px;font-family:inherit;font-weight:600;outline:none;background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(30,96,145,.05);transition:border-color .14s,box-shadow .14s;}}
      #panel-gk .gk-search:focus{{border-color:#1e6091;box-shadow:0 0 0 3px rgba(30,96,145,.13);}}
      #panel-gk .gk-stat{{margin-left:auto;font-size:11.5px;font-weight:700;color:#7c8ca0;white-space:nowrap;font-variant-numeric:tabular-nums;}}
      #panel-gk .gk-tiles{{display:flex;flex-wrap:wrap;gap:7px;max-height:146px;overflow-y:auto;padding:2px 4px 2px 0;}}
      #panel-gk .gk-tile{{display:flex;align-items:center;gap:9px;cursor:pointer;font-family:inherit;text-align:left;border:1.5px solid #dbe4ef;background:#fff;border-radius:11px;padding:7px 12px 7px 8px;min-width:150px;max-width:250px;user-select:none;transition:transform .12s,box-shadow .12s,border-color .12s;}}
      #panel-gk .gk-tile:hover{{transform:translateY(-1px);box-shadow:0 6px 16px rgba(15,31,51,.10);border-color:#c3d4e7;}}
      #panel-gk .gk-tile:focus-visible{{outline:none;border-color:var(--gk);box-shadow:0 0 0 3px color-mix(in srgb,var(--gk) 28%,transparent);}}
      #panel-gk .gk-tile.is-active{{border-color:var(--gk);box-shadow:0 8px 20px color-mix(in srgb,var(--gk) 22%,rgba(15,31,51,.10));}}
      #panel-gk .gk-tile-txt{{min-width:0;display:flex;flex-direction:column;gap:3px;}}
      #panel-gk .gk-tile-name{{font-size:12.5px;font-weight:800;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2;}}
      #panel-gk .gk-tile-meta{{font-size:10.5px;font-weight:700;color:#7c8ca0;}}
      #panel-gk .gk-mono{{flex-shrink:0;width:30px;height:30px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:11.5px;font-weight:900;letter-spacing:.3px;background:var(--gks);color:var(--gkd);}}
      #panel-gk .gk-detail{{flex:1;overflow-y:auto;padding:18px 20px 34px;}}
      #panel-gk .gk-fade{{animation:gkFade .22s ease-out;}}
      @keyframes gkFade{{from{{opacity:0;transform:translateY(6px);}}to{{opacity:1;transform:none;}}}}
      @media (prefers-reduced-motion:reduce){{#panel-gk .gk-fade{{animation:none;}}#panel-gk .gk-tile{{transition:none;}}}}
      #panel-gk .gk-hero{{display:flex;align-items:center;gap:16px;background:linear-gradient(135deg,var(--gks) 0%,#fff 72%);border:1px solid var(--gkb);border-left:5px solid var(--gk);border-radius:16px;padding:16px 20px;margin-bottom:14px;box-shadow:0 10px 26px rgba(15,31,51,.06);flex-wrap:wrap;}}
      #panel-gk .gk-hero-mono{{flex-shrink:0;width:54px;height:54px;border-radius:15px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:900;letter-spacing:.4px;background:var(--gk);color:#fff;box-shadow:0 6px 16px color-mix(in srgb,var(--gk) 40%,transparent);}}
      #panel-gk .gk-hero-txt{{min-width:0;flex:1;}}
      #panel-gk .gk-hero-name{{font-size:23px;font-weight:900;letter-spacing:-.5px;color:var(--ink);line-height:1.1;}}
      #panel-gk .gk-hero-sub{{font-size:12.5px;font-weight:700;color:var(--gkd);margin-top:3px;}}
      #panel-gk .gk-actions{{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;}}
      #panel-gk .gk-act{{display:inline-flex;align-items:center;gap:7px;background:var(--gk);color:#fff;border:none;border-radius:9px;padding:8px 14px;font-size:12.5px;font-weight:800;text-decoration:none;white-space:nowrap;cursor:pointer;box-shadow:0 4px 12px color-mix(in srgb,var(--gk) 32%,transparent);transition:filter .12s,transform .12s;}}
      #panel-gk .gk-act:hover{{filter:brightness(1.07);transform:translateY(-1px);}}
      #panel-gk .gk-act b{{font-weight:900;opacity:.85;}}
      #panel-gk .gk-act-sm{{background:#fff;color:var(--gkd);border:1.5px solid var(--gkb);box-shadow:none;}}
      #panel-gk .gk-act-sm:hover{{background:var(--gks);filter:none;}}
      #panel-gk .gk-card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;box-shadow:0 4px 14px rgba(15,31,51,.045);margin-bottom:14px;}}
      #panel-gk .gk-card-h{{display:flex;align-items:center;gap:10px;padding:11px 16px;border-bottom:1px solid #eef2f7;background:linear-gradient(180deg,#fbfdff,#fff);}}
      #panel-gk .gk-eyebrow{{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:900;text-transform:uppercase;letter-spacing:.8px;color:var(--gkd);}}
      #panel-gk .gk-eyebrow::before{{content:"";width:7px;height:7px;border-radius:50%;background:var(--gk);}}
      #panel-gk .gk-cnt{{margin-left:auto;font-size:11px;font-weight:800;color:#94a3b8;font-variant-numeric:tabular-nums;}}
      #panel-gk .gk-card-b{{padding:4px 16px 10px;}}
      #panel-gk .gk-loc{{display:flex;align-items:flex-start;gap:18px;flex-wrap:wrap;padding:12px 0;border-top:1px solid #f1f5f9;}}
      #panel-gk .gk-loc:first-child{{border-top:none;}}
      #panel-gk .gk-loc-name{{min-width:220px;flex:0 0 auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13.5px;font-weight:800;color:var(--ink);}}
      #panel-gk .gk-loc-addr{{flex:1;font-size:12.5px;color:#475569;line-height:1.6;}}
      #panel-gk .gk-loc-chips{{display:flex;flex-wrap:wrap;gap:8px;padding:10px 0 4px;}}
      #panel-gk .gk-loc-chip{{display:inline-flex;align-items:center;gap:9px;padding:7px 12px;background:#f7fafd;border:1px solid #e2e8f0;border-radius:10px;font-size:13px;font-weight:800;color:var(--ink);}}
      #panel-gk .gk-knr{{display:inline-flex;align-items:baseline;gap:5px;background:var(--gks);border:1px solid var(--gkb);border-radius:6px;padding:2px 8px;}}
      #panel-gk .gk-knr-l{{font-size:8.5px;font-weight:900;text-transform:uppercase;letter-spacing:.5px;color:var(--gkd);}}
      #panel-gk .gk-knr-v{{font-size:12.5px;font-weight:800;color:var(--ink);font-variant-numeric:tabular-nums;}}
      #panel-gk .gk-grid{{display:grid;grid-template-columns:minmax(340px,1.1fr) minmax(260px,.9fr);gap:14px;align-items:start;}}
      @media (max-width:880px){{#panel-gk .gk-grid{{grid-template-columns:1fr;}}}}
      #panel-gk .gk-c-sec{{margin:13px 0 6px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding-bottom:6px;border-bottom:1px solid #e8eef5;}}
      #panel-gk .gk-c-sec-l{{font-size:10.5px;font-weight:900;text-transform:uppercase;letter-spacing:.7px;color:var(--gkd);}}
      #panel-gk .gk-c-row{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f4f7fa;}}
      #panel-gk .gk-c-row:last-child{{border-bottom:none;}}
      #panel-gk .gk-c-left{{flex:1;min-width:0;}}
      #panel-gk .gk-mail{{color:var(--gkd);font-size:12.5px;font-weight:700;text-decoration:none;}}
      #panel-gk .gk-mail:hover{{text-decoration:underline;}}
      #panel-gk .gk-c-label{{font-size:12.5px;font-weight:700;color:var(--ink);}}
      #panel-gk .gk-c-dash{{color:#cbd5e1;font-size:12px;}}
      #panel-gk .gk-tel{{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:800;color:var(--gkd);font-variant-numeric:tabular-nums;text-decoration:none;white-space:nowrap;}}
      #panel-gk .gk-tel-i{{color:var(--gk);font-weight:400;}}
      #panel-gk .gk-c-other{{display:flex;gap:12px;align-items:baseline;padding:7px 0;border-bottom:1px solid #f4f7fa;}}
      #panel-gk .gk-c-other-l{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;color:#94a3b8;min-width:90px;flex-shrink:0;}}
      #panel-gk .gk-c-other-v{{font-size:12.5px;color:#334155;line-height:1.5;}}
      #panel-gk .gk-hint{{display:flex;align-items:flex-start;gap:12px;padding:10px 16px;border-top:1px solid #f1f5f9;}}
      #panel-gk .gk-hint:first-child{{border-top:none;}}
      #panel-gk .gk-hint-n{{flex-shrink:0;width:20px;height:20px;border-radius:6px;background:var(--gks);color:var(--gkd);font-size:11px;font-weight:900;display:flex;align-items:center;justify-content:center;font-variant-numeric:tabular-nums;margin-top:1px;}}
      #panel-gk .gk-hint-t{{font-size:12.5px;color:#334155;line-height:1.55;}}
      #panel-gk .gk-ff-row{{padding:9px 16px;border-top:1px solid #f4f7fa;font-size:12.5px;color:#334155;}}
      #panel-gk .gk-ff-row:first-child{{border-top:none;}}
      #panel-gk .gk-ff-long{{background:#f8fbff;line-height:1.6;}}
      #panel-gk .gk-empty{{color:#94a3b8;padding:70px;text-align:center;font-size:14px;}}
    </style>
    <!-- Kopf / Suche -->
    <div class="gk-head">
      <div class="gk-titlerow">
        <div class="gk-title"><span class="gk-dot"></span>Gro&#223;kunden <span id="gk-count" class="gk-count"></span></div>
        <div class="gk-searchwrap">
          <span class="gk-sicon">&#128269;</span>
          <input id="gk-search" class="gk-search" placeholder="Gro&#223;kunden suchen..." oninput="gkFilter(this.value)">
        </div>
        <span id="gk-stats" class="gk-stat"></span>
      </div>
      <div id="gk-tiles" class="gk-tiles"></div>
    </div>
    <!-- Detail unten -->
    <div id="gk-detail" class="gk-detail">
      <div class="gk-empty">Keine Gro&#223;kundendaten &ndash; bitte Excel in Streamlit hochladen.</div>
    </div>
  </div>

  <!-- ── Spediteure Panel ───────────────────────────────────────────────────── -->
  <div id="panel-sped" style="display:none;flex:1;overflow:hidden;background:#f3f7fb;font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;">
    <div style="flex-shrink:0;background:linear-gradient(180deg,#f8fbff 0%,#edf4fb 100%);border-bottom:1.5px solid #c6d6e8;padding:12px 18px;box-shadow:0 1px 5px rgba(30,96,145,.08);">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#1e6091 0%,#2f80b7 100%);display:flex;align-items:center;justify-content:center;font-size:17px;">&#128666;</div>
          <div>
            <div style="font-size:14px;font-weight:900;color:#0f172a;letter-spacing:-.2px;">Spediteure</div>
            <div style="font-size:11px;color:#64748b;">Tourenpl&auml;ne nach Spedition &middot; Jahr / Monat / Untername</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-left:8px;">
          <select id="sped-year" onchange="spedSetJahr(this.value)" title="Jahr" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;"></select>
          <select id="sped-month" onchange="spedSetMonat(this.value)" title="Monat" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;"></select>
          <select id="sped-filter" onchange="spedSetSped(this.value)" title="Spedition" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;max-width:240px;"></select>
        </div>
      </div>
      <div id="sped-stats" style="display:flex;gap:7px;flex-wrap:wrap;margin-top:10px;"></div>
    </div>
    <div id="sped-content" style="flex:1;overflow-y:auto;padding:16px 18px 28px;background:#f3f7fb;">
      <div style="color:#94a3b8;padding:70px;text-align:center;font-size:14px;">Keine Spediteur-Daten &ndash; bitte Touren-Dateien (Excel) in Streamlit hochladen.</div>
    </div>
  </div>

  <!-- ── Spediteure Graph Panel ─────────────────────────────────────────────── -->
  <div id="panel-sped-graph" style="display:none;flex:1;overflow:hidden;background:#f3f7fb;font-family:'Segoe UI',Arial,sans-serif;flex-direction:column;">
    <div style="flex-shrink:0;background:linear-gradient(180deg,#f8fbff 0%,#edf4fb 100%);border-bottom:1.5px solid #c6d6e8;padding:12px 18px;box-shadow:0 1px 5px rgba(30,96,145,.08);">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#1e6091 0%,#2f80b7 100%);display:flex;align-items:center;justify-content:center;font-size:17px;">&#128202;</div>
          <div>
            <div style="font-size:14px;font-weight:900;color:#0f172a;letter-spacing:-.2px;">Spediteure Graph</div>
            <div style="font-size:11px;color:#64748b;">Grafische Auswertung der Fahrten</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-left:8px;">
          <select id="sped-graph-mode" onchange="spedGraphSetMode(this.value)" title="Ansicht" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:900;font-family:inherit;background:#fff;color:#1f3347;outline:none;">
            <option value="single">Ein Jahr</option>
            <option value="compare">Jahre vergleichen</option>
          </select>
          <select id="sped-graph-year" onchange="spedGraphSetJahr(this.value)" title="Jahr 1" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;"></select>
          <select id="sped-graph-year-2" onchange="spedGraphSetJahr2(this.value)" title="Jahr 2" style="display:none;padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;"></select>
          <select id="sped-graph-month" onchange="spedGraphSetMonat(this.value)" title="Monat" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;"></select>
          <select id="sped-graph-filter" onchange="spedGraphSetSped(this.value)" title="Spedition" style="padding:8px 11px;border:1.5px solid #b9cce3;border-radius:8px;font-size:12px;font-weight:800;font-family:inherit;background:#fff;color:#1f3347;outline:none;max-width:240px;"></select>
          <button onclick="spedGraphExportExcel()" title="Aktuellen Spediteursgraphen als Excel exportieren" style="padding:8px 13px;border:1.5px solid #16a34a;border-radius:8px;font-size:12px;font-weight:900;font-family:inherit;background:linear-gradient(180deg,#ecfdf5 0%,#dcfce7 100%);color:#166534;cursor:pointer;box-shadow:0 2px 7px rgba(22,163,74,.12);">&#128190; Excel Export</button>
        </div>
      </div>
      <div id="sped-graph-stats" style="display:flex;gap:7px;flex-wrap:wrap;margin-top:10px;"></div>
    </div>
    <div id="sped-graph-content" style="flex:1;overflow-y:auto;padding:16px 18px 28px;background:#f3f7fb;">
      <div style="color:#94a3b8;padding:70px;text-align:center;font-size:14px;">Keine Spediteur-Daten &ndash; bitte Touren-Dateien (Excel) in Streamlit hochladen.</div>
    </div>
  </div>

</div>
</div>


<script>
// ── Base64-Chunks → UTF-8-String (zlib-compressed) ──────────────────────────
async function b64ChunksToString(chunks) {{
  var b64 = chunks.join("");
  var bin = atob(b64);
  var bytes = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) {{ bytes[i] = bin.charCodeAt(i); }}
  var ds = new DecompressionStream("deflate");
  var writer = ds.writable.getWriter();
  writer.write(bytes);
  writer.close();
  return new Response(ds.readable).text();
}}

// ── Komprimierte Zusatzdaten ────────────────────────────────────────────────
var EMBEDDED_DATA_B64 = [
{embedded_data_js}
];

// Sichere Startwerte, bis der komprimierte Datenblock entpackt ist.
var FAHRZEUGWAESCHE_DATA = [];
var TEL_DATA = [];
var SAM_DATA = [];
var FA_DATA = [];
var FAHRERBEWERTUNG_DATA = {{profile:"",event_types:[],g_months:{{}},g_ev:{{}},drivers:[]}};
var ZULAGE_DATA = {{}};
var DRITTKUNDEN_DATA = [];
var VERSTOSS_DATA = {{drivers:[],total_violations:0}};
var SPESEN_DATA = {{drivers:[],months:[],total_cost:0,total_rows:0}};
var GK_DATA = [];
var TIMEREC_DATA = {{}};
var SPED_DATA = {{katalog:[],fahrten:[]}};
var VERSP_ABFAHRT = {{}};
var _embeddedDataPromise = null;

function loadEmbeddedData() {{
  if(_embeddedDataPromise) return _embeddedDataPromise;
  _embeddedDataPromise = (async function() {{
    var raw = await b64ChunksToString(EMBEDDED_DATA_B64);
    var data = JSON.parse(raw);
    FAHRZEUGWAESCHE_DATA = data.fahrzeugwaesche || [];
    TEL_DATA = data.telefon || [];
    SAM_DATA = data.samstag || [];
    FA_DATA = data.fahrer || [];
    FAHRERBEWERTUNG_DATA = data.fahrerbewertung || {{profile:"",event_types:[],g_months:{{}},g_ev:{{}},drivers:[]}};
    ZULAGE_DATA = data.zulagen || {{}};
    DRITTKUNDEN_DATA = data.drittkunden || [];
    VERSTOSS_DATA = data.verstoesse || {{drivers:[],total_violations:0}};
    SPESEN_DATA = data.spesen || {{drivers:[],months:[],total_cost:0,total_rows:0}};
    GK_DATA = data.grosskunden || [];
    TIMEREC_DATA = data.timerecording || {{}};
    SPED_DATA = data.spediteure || {{katalog:[],fahrten:[]}};
    VERSP_ABFAHRT = data.verspaetung_abfahrt || {{}};
    EMBEDDED_DATA_B64 = [];
    return true;
  }})().catch(function(err) {{
    console.error("Zusatzdaten konnten nicht geladen werden:", err);
    _embeddedDataPromise = null;
    throw err;
  }});
  return _embeddedDataPromise;
}}

// ── Instanzen ─────────────────────────────────────────────────────────────────
var INSTANCES = [
{instances_js}
];
var currentInst = 0;

// switchInst: ersetzt durch ddSelect()

// ── Initiales Laden ───────────────────────────────────────────────────────────
var currentInst = 0;
var currentArea = "suche";

async function loadInst(i) {{
  await loadEmbeddedData();
  currentInst = i;
  var inst = INSTANCES[i];
  document.getElementById("frame-suche").srcdoc = await b64ChunksToString(inst.s);
  document.getElementById("frame-druck" ).srcdoc = await b64ChunksToString(inst.d);
  vzAllData = null;
  // Dropdown-Items aktualisieren
  ["suche","vz"].forEach(function(area) {{
    var menu = document.getElementById("ddmenu-"+area);
    if(!menu) return;
    if(area === "vz") {{ if(typeof buildVzDdMenu === "function") buildVzDdMenu(); }}
    else buildDdMenu(area);
  }});
  // Wochenauslastung neu berechnen lassen (Daten haengen an der Instanz)
  var _wh = document.getElementById("panel-wa-heat");   if(_wh) _wh.dataset.loaded = "";
  var _wg = document.getElementById("panel-wa-gruppe"); if(_wg) _wg.dataset.loaded = "";
  var _wk = document.getElementById("panel-wa-kurve");  if(_wk) _wk.dataset.loaded = "";
  var _wr = document.getElementById("panel-wa-rhythm"); if(_wr) _wr.dataset.loaded = "";
  if(currentArea === "wa_heat"   && typeof waInitHeat   === "function") {{ waInitHeat();   if(_wh) _wh.dataset.loaded="1"; }}
  if(currentArea === "wa_gruppe" && typeof waInitGruppe === "function") {{ waInitGruppe(); if(_wg) _wg.dataset.loaded="1"; }}
  if(currentArea === "wa_kurve"  && typeof waInitKurve  === "function") {{ waInitKurve();  if(_wk) _wk.dataset.loaded="1"; }}
  if(currentArea === "wa_rhythm" && typeof waInitRhythm === "function") {{ waInitRhythm(); if(_wr) _wr.dataset.loaded="1"; }}
}}

function buildSamDdMenu() {{
  var menu = document.getElementById("ddmenu-sam");
  if(!menu) return;
  var items = [
    {{ id: "sam", label: "Liste" }},
    {{ id: "sam_graph", label: "Graph" }}
  ];
  menu.innerHTML = items.map(function(it){{
    var active = (currentArea === it.id) ? " active" : "";
    return "<div class='dd-item" + active + "' data-area='" + it.id + "' onclick='ddSelectSam(this.dataset.area)'>" + it.label + "</div>";
  }}).join("");
}}

function ddSelectSam(area) {{
  showArea(area);
  document.querySelectorAll(".nav-dd").forEach(function(d){{ d.classList.remove("open"); }});
}}

function buildDdMenu(area) {{
  var menu = document.getElementById("ddmenu-"+area);
  if(!menu) return;
  var html = "";
  INSTANCES.forEach(function(inst, i) {{
    var cls = "dd-item" + (i===currentInst ? " active" : "");
    html += "<div class='" + cls + "'"
          + " data-area='" + area + "'"
          + " data-idx='" + i + "'"
          + " onclick='ddSelect(this.dataset.area,+this.dataset.idx)'>"
          + inst.name + "</div>";
  }});
  menu.innerHTML = html;
}}

function ddToggle(area, e) {{
  e.stopPropagation();
  var dd   = document.getElementById("dd-"+area);
  var menu = document.getElementById("ddmenu-"+area);
  var wasOpen = dd.classList.contains("open");
  document.querySelectorAll(".nav-dd").forEach(function(d){{d.classList.remove("open");}});
  if(!wasOpen) {{
    if(area === "verstoss") buildVerstossDdMenu();
    else if(area === "sam") buildSamDdMenu();
    else if(area === "sped") buildSpedDdMenu();
    else if(area === "infos") buildInfosDdMenu();
    else if(area === "vz") buildVzDdMenu();
    else if(area === "fa") buildFaDdMenu();
    else if(area === "wa") buildWaDdMenu();
    else buildDdMenu(area);
    dd.classList.add("open");
    // Position unter dem Button berechnen (fixed, ignoriert iframe)
    var btn  = document.getElementById("btn-"+area);
    var rect = btn.getBoundingClientRect();
    menu.style.top  = (rect.bottom + 4) + "px";
    menu.style.left = rect.left + "px";
  }}
}}

function updateInstLabels() {{
  if(INSTANCES.length <= 1) return;  // nur anzeigen wenn mehrere Instanzen
  var name = INSTANCES[currentInst].name;
  ["suche","vz"].forEach(function(area) {{
    var el = document.getElementById("inst-label-"+area);
    if(el) {{
      el.textContent = name;
      el.className = "inst-label";
    }}
  }});
}}

function ddSelect(area, instIdx) {{
  if(instIdx !== currentInst) loadInst(instIdx);
  updateInstLabels();
  showArea(area);
  document.querySelectorAll(".nav-dd").forEach(function(d){{d.classList.remove("open");}});
}}

// Klick außerhalb schließt Dropdown
document.addEventListener("click", function() {{
  document.querySelectorAll(".nav-dd").forEach(function(d){{d.classList.remove("open");}});
}});

// ── Navigation ────────────────────────────────────────────────────────────────
function showArea(s) {{
  currentArea = s;
  // iframes
  document.getElementById("frame-suche").className = (s==="suche")?"active":"";
  // Dropdown-Buttons aktiv/inaktiv
  ["suche","vz"].forEach(function(id) {{
    var btn = document.getElementById("btn-"+id);
    if(btn) btn.className = "nav-dd-btn" + ((id===s || (id==="vz" && s==="vz_graph"))?" active":"");
  }});
  // Telefonliste-Button
  var telBtn = document.getElementById("btn-tel");
  if(telBtn) telBtn.className = "nav-btn" + (s==="tel"?" active":"");
  // Sa + So Einsätze (Dropdown: Liste + Graph)
  var samBtn = document.getElementById("btn-sam");
  if(samBtn) samBtn.className = "nav-dd-btn" + ((s==="sam" || s==="sam_graph")?" active":"");
  if(typeof buildSamDdMenu === "function") buildSamDdMenu();
  // Fahrerauswertung-Button (Dropdown: Schichten + Fahrerbewertung)
  var faBtn = document.getElementById("btn-fa");
  if(faBtn) faBtn.className = "nav-dd-btn" + ((s==="fa" || s==="fa_bewertung")?" active":"");
  if(typeof buildFaDdMenu === "function") buildFaDdMenu();
  // Panels
  var vzPanel       = document.getElementById("panel-vz");
  var telPanel      = document.getElementById("panel-tel");
  var samPanel      = document.getElementById("panel-sam");
  vzPanel.style.display  = (s==="vz")  ? "block" : "none";
  var vzGraphPanel = document.getElementById("panel-vz-graph");
  if(vzGraphPanel) vzGraphPanel.style.display = (s==="vz_graph") ? "flex" : "none";
  telPanel.style.display = (s==="tel") ? "block" : "none";
  if(samPanel)      samPanel.style.display      = (s==="sam" || s==="sam_graph") ? "block" : "none";
  var faPanel = document.getElementById("panel-fa");
  if(faPanel) faPanel.style.display = (s==="fa") ? "flex" : "none";
  var zulagePanel = document.getElementById("panel-zulage");
  if(zulagePanel) zulagePanel.style.display = (s==="zulage") ? "flex" : "none";
  var zuBtn = document.getElementById("btn-zulage");
  if(zuBtn) zuBtn.className = "nav-btn" + (s==="zulage" ? " active" : "");
  var spesenBtn = document.getElementById("btn-spesen");
  if(spesenBtn) spesenBtn.className = "nav-btn" + (s==="spesen" ? " active" : "");
  var spesenPanel = document.getElementById("panel-spesen");
  if(spesenPanel) spesenPanel.style.display = (s==="spesen") ? "flex" : "none";
  var verstossPanel = document.getElementById("panel-verstoss");
  if(verstossPanel) verstossPanel.style.display = (s==="verstoss") ? "flex" : "none";
  var verstossGraphPanel = document.getElementById("panel-verstoss-graph");
  if(verstossGraphPanel) verstossGraphPanel.style.display = (s==="verstoss_graph") ? "flex" : "none";
  var verstossBtn = document.getElementById("btn-verstoss");
  if(verstossBtn) verstossBtn.className = "nav-dd-btn" + ((s==="verstoss" || s==="verstoss_graph") ? " active" : "");
  if(typeof buildVerstossDdMenu === "function") buildVerstossDdMenu();

  if(s==="vz") {{
    fwInitDatePicker();
    if(vzPanel && !vzPanel.dataset.loaded) {{ fwInitOverview(); vzPanel.dataset.loaded="1"; }}
    else {{ fwRenderOverview(); }}
  }}
  if(s==="vz_graph") {{
    if(vzGraphPanel && !vzGraphPanel.dataset.loaded) {{ fwInitGraph(); vzGraphPanel.dataset.loaded="1"; }}
    else {{ fwRenderGraph(); }}
  }}
  if(typeof buildVzDdMenu === "function") buildVzDdMenu();
  if(s==="tel" && !telPanel.dataset.loaded) {{ telRender(""); telPanel.dataset.loaded="1"; }}
  if((s==="sam" || s==="sam_graph") && samPanel) {{
    samViewMode = s === "sam_graph" ? "charts" : "list";
    var samTitle = document.getElementById("sam-panel-title");
    if(samTitle) samTitle.innerHTML = s === "sam_graph"
      ? "&#128202; Graph – Sa + So Einsätze"
      : "&#128664; Sa + So Einsätze";
    var samSortBtns = document.getElementById("sam-list-sort-buttons");
    var samChartTabs = document.getElementById("sam-chart-tabs");
    if(samSortBtns) samSortBtns.style.display = samViewMode === "list" ? "flex" : "none";
    if(samChartTabs) samChartTabs.style.display = samViewMode === "charts" ? "flex" : "none";
    var samQuery = (document.getElementById("sam-search") || {{value:""}}).value;
    samRender(samQuery);
    samPanel.dataset.loaded = "1";
  }}
  if(s==="zulage" && zulagePanel && !zulagePanel.dataset.loaded) {{ zulagenInit(); zulagePanel.dataset.loaded="1"; }}
  if(s==="spesen") {{
    if(spesenPanel && !spesenPanel.dataset.loaded) {{ spesenInit(); spesenPanel.dataset.loaded="1"; }}
    else {{ spesenUpdateSidebarHighlight(); }}
  }}
  if(s==="verstoss") {{
    if(verstossPanel && !verstossPanel.dataset.loaded) {{ verstossInit(); verstossPanel.dataset.loaded="1"; }}
    else {{ verstossRender(); }}
  }}
  if(s==="verstoss_graph") {{
    if(verstossGraphPanel && !verstossGraphPanel.dataset.loaded) {{ verstossInitGraph(); verstossGraphPanel.dataset.loaded="1"; }}
    else {{ verstossRenderGraph(); }}
  }}
  if(s==="fa") {{ if(faPanel) faPanel.scrollTop = 0; if(faPanel && !faPanel.dataset.loaded) {{ faRender(""); faPanel.dataset.loaded="1"; }} }}
  var faBewPanel = document.getElementById("panel-fa-bewertung");
  if(faBewPanel) faBewPanel.style.display = (s==="fa_bewertung") ? "flex" : "none";
  if(s==="fa_bewertung") {{
    if(faBewPanel && !faBewPanel.dataset.loaded) {{ faBewInit(); faBewPanel.dataset.loaded="1"; }}
    else {{ faBewRender(); }}
  }}
  var gkPanel = document.getElementById("panel-gk");
  if(gkPanel) gkPanel.style.display = (s==="gk") ? "flex" : "none";
  var gkBtn = document.getElementById("btn-gk");
  if(gkBtn) gkBtn.className = "nav-btn" + (s==="gk" ? " active" : "");
  if(s==="gk" && gkPanel && !gkPanel.dataset.loaded) {{ gkRender(); gkPanel.dataset.loaded="1"; }}
  var schluesselPanel = document.getElementById("panel-schluessel");
  if(schluesselPanel) schluesselPanel.style.display = (s==="schluessel") ? "flex" : "none";
  if(s==="schluessel" && schluesselPanel && !schluesselPanel.dataset.loaded) {{ documentPdfInit("schluessel"); schluesselPanel.dataset.loaded="1"; }}
  var entgeltPanel = document.getElementById("panel-entgelt");
  if(entgeltPanel) entgeltPanel.style.display = (s==="entgelt") ? "flex" : "none";
  if(s==="entgelt" && entgeltPanel && !entgeltPanel.dataset.loaded) {{ documentPdfInit("entgelt"); entgeltPanel.dataset.loaded="1"; }}
  var schadenPanel = document.getElementById("panel-schaden");
  if(schadenPanel) schadenPanel.style.display = (s==="schaden") ? "flex" : "none";
  if(s==="schaden" && schadenPanel && !schadenPanel.dataset.loaded) {{ documentPdfInit("schaden"); schadenPanel.dataset.loaded="1"; }}
  var maengelPanel = document.getElementById("panel-maengel");
  if(maengelPanel) maengelPanel.style.display = (s==="maengel") ? "flex" : "none";
  if(s==="maengel" && maengelPanel && !maengelPanel.dataset.loaded) {{ documentPdfInit("maengel"); maengelPanel.dataset.loaded="1"; }}
  var lkwUebergabePanel = document.getElementById("panel-lkw_uebergabe");
  if(lkwUebergabePanel) lkwUebergabePanel.style.display = (s==="lkw_uebergabe") ? "flex" : "none";
  if(s==="lkw_uebergabe" && lkwUebergabePanel && !lkwUebergabePanel.dataset.loaded) {{ documentPdfInit("lkw_uebergabe"); lkwUebergabePanel.dataset.loaded="1"; }}
  var balzerPanel = document.getElementById("panel-balzer");
  if(balzerPanel) balzerPanel.style.display = (s==="balzer") ? "flex" : "none";
  if(s==="balzer" && balzerPanel && !balzerPanel.dataset.loaded) {{ documentPdfInit("balzer"); balzerPanel.dataset.loaded="1"; }}
  var terminePanel = document.getElementById("panel-termine");
  if(terminePanel) terminePanel.style.display = (s==="termine") ? "flex" : "none";
  if(s==="termine" && terminePanel && !terminePanel.dataset.loaded) {{ documentPdfInit("termine"); terminePanel.dataset.loaded="1"; }}
  var busPanel = document.getElementById("panel-bus");
  if(busPanel) busPanel.style.display = (s==="bus") ? "block" : "none";
  var busBtn = document.getElementById("btn-bus");
  if(busBtn) busBtn.className = "nav-btn" + (s==="bus" ? " active" : "");
  if(s==="bus" && busPanel && !busPanel.dataset.loaded) {{ busRender(); busPanel.dataset.loaded="1"; }}
  var arztPanel = document.getElementById("panel-arzt");
  if(arztPanel) arztPanel.style.display = (s==="arzt") ? "block" : "none";
  if(s==="arzt" && arztPanel && !arztPanel.dataset.loaded) {{ arztRender(); arztPanel.dataset.loaded="1"; }}
  var verspPanel = document.getElementById("panel-versp");
  if(verspPanel) verspPanel.style.display = (s==="versp") ? "block" : "none";
  if(s==="versp" && verspPanel && !verspPanel.dataset.loaded) {{ verspInit(); verspPanel.dataset.loaded="1"; }}
  var knappPanel = document.getElementById("panel-knapp");
  if(knappPanel) knappPanel.style.display = (s==="knapp") ? "flex" : "none";
  if(s==="knapp" && knappPanel && !knappPanel.dataset.loaded) {{ knappInit(); knappPanel.dataset.loaded="1"; }}
  var infosBtn = document.getElementById("btn-infos");
  if(infosBtn) infosBtn.className = "nav-dd-btn" + ((s==="tel" || s==="bus" || s==="arzt" || s==="versp" || s==="knapp" || s==="schluessel" || s==="entgelt" || s==="schaden" || s==="maengel" || s==="lkw_uebergabe" || s==="balzer") ? " active" : "");
  if(typeof buildInfosDdMenu === "function") buildInfosDdMenu();
  var spedPanel = document.getElementById("panel-sped");
  if(spedPanel) spedPanel.style.display = (s==="sped") ? "flex" : "none";
  var spedGraphPanel = document.getElementById("panel-sped-graph");
  if(spedGraphPanel) spedGraphPanel.style.display = (s==="sped_graph") ? "flex" : "none";
  var spedBtn = document.getElementById("btn-sped");
  if(spedBtn) spedBtn.className = "nav-dd-btn" + ((s==="sped" || s==="sped_graph") ? " active" : "");
  if(typeof buildSpedDdMenu === "function") buildSpedDdMenu();
  if(s==="sped" && spedPanel && !spedPanel.dataset.loaded) {{ spedInit(); spedPanel.dataset.loaded="1"; }}
  if(s==="sped_graph") {{
    if(spedGraphPanel && !spedGraphPanel.dataset.loaded) {{ spedInitGraph(); spedGraphPanel.dataset.loaded="1"; }}
    else {{ spedRenderGraph(); }}
  }}
  // ── Wochenauslastung ──
  var waHeatPanel = document.getElementById("panel-wa-heat");
  if(waHeatPanel) waHeatPanel.style.display = (s==="wa_heat") ? "flex" : "none";
  var waGruppePanel = document.getElementById("panel-wa-gruppe");
  if(waGruppePanel) waGruppePanel.style.display = (s==="wa_gruppe") ? "flex" : "none";
  var waKurvePanel = document.getElementById("panel-wa-kurve");
  if(waKurvePanel) waKurvePanel.style.display = (s==="wa_kurve") ? "flex" : "none";
  var waRhythmPanel = document.getElementById("panel-wa-rhythm");
  if(waRhythmPanel) waRhythmPanel.style.display = (s==="wa_rhythm") ? "flex" : "none";
  var waBtn = document.getElementById("btn-wa");
  if(waBtn) waBtn.className = "nav-dd-btn" + ((s==="wa_heat" || s==="wa_gruppe" || s==="wa_kurve" || s==="wa_rhythm") ? " active" : "");
  if(typeof buildWaDdMenu === "function") buildWaDdMenu();
  if(s==="wa_heat") {{
    if(waHeatPanel && !waHeatPanel.dataset.loaded) {{ waInitHeat(); waHeatPanel.dataset.loaded="1"; }}
    else {{ waRenderHeat(); }}
  }}
  if(s==="wa_gruppe") {{
    if(waGruppePanel && !waGruppePanel.dataset.loaded) {{ waInitGruppe(); waGruppePanel.dataset.loaded="1"; }}
    else {{ waRenderGruppe(); }}
  }}
  if(s==="wa_kurve") {{
    if(waKurvePanel && !waKurvePanel.dataset.loaded) {{ waInitKurve(); waKurvePanel.dataset.loaded="1"; }}
    else {{ waRenderKurve(); }}
  }}
  if(s==="wa_rhythm") {{
    if(waRhythmPanel && !waRhythmPanel.dataset.loaded) {{ waInitRhythm(); waRhythmPanel.dataset.loaded="1"; }}
    else {{ waRenderRhythm(); }}
  }}
}}

// showKundenListeTop removed — Kunden Liste is now a standalone panel

if(INSTANCES.length > 0) {{
  (async function() {{
    await loadEmbeddedData();
    document.getElementById("frame-suche").srcdoc = await b64ChunksToString(INSTANCES[0].s);
    document.getElementById("frame-druck" ).srcdoc = await b64ChunksToString(INSTANCES[0].d);
    updateInstLabels();
    fwInitDatePicker();
  }})();
}}

var normalInstData = null;  // ALL_DATA der Normalwochen (Instanz 0)
window.addEventListener("message", function(e) {{
  if (e.data === "show-suche") showArea("suche");
  if (e.data && e.data.type === "vz-init-data") {{
    vzAllData = e.data.data;
    // Erste Instanz = Normalwochen → als Referenz speichern
    if(currentInst === 0) normalInstData = e.data.data;
    // Pro Woche/Instanz cachen, damit die Verspätungstabelle ohne Neuladen wechseln kann
    try {{ verspInstCache[currentInst] = e.data.data; }} catch(err) {{}}
    if(typeof currentArea !== "undefined" && currentArea === "versp" && typeof verspUpdateInfo === "function") verspUpdateInfo();
  }}
  if (e.data && e.data.type === "request-normal-data") {{
    // Druck-iframe fragt nach Normalwochen-Daten
    var fd = document.getElementById("frame-druck");
    if(normalInstData && fd && fd.contentWindow) {{
      try {{ fd.contentWindow.postMessage({{type:"normal-data",data:normalInstData}},"*"); }} catch(e) {{}}
    }}
  }}
}});

// ── Fahrzeugwäsche ────────────────────────────────────────────────────────────
var fwSelectedDay = null;
var fwSelectedDate = null;
var vzAllData = null;
var FW_EXCLUDED_SUFFIXES = ["998","999","2221","2222","2223","4444","7773","7778","7779"];

function fwSelectDay(day) {{
  fwSelectedDay = day;
  document.querySelectorAll("#fw-day-btns .vz-day-btn").forEach(function(b) {{
    b.classList.toggle("active", b.textContent.trim()===day);
  }});
}}

function fwSetDate(value) {{
  fwSelectedDate = value || null;
}}

function fwIsExcludedNumber(value) {{
  var s = (value == null ? "" : String(value)).replace(/\\D/g, "");
  if(!s) return false;
  return FW_EXCLUDED_SUFFIXES.some(function(suffix) {{
    if(s.endsWith(suffix)) return true;
    if(s.length >= suffix.length + 1 && /^[1-6]$/.test(s.charAt(0))) {{
      return s.slice(1).endsWith(suffix);
    }}
    return false;
  }});
}}

function vzCollectToursByDay(allData, day) {{
  var AREAS = ["direkt","mk","nms","malchow"];
  var seen = {{}};
  AREAS.forEach(function(area) {{
    var areaData = allData[area] || {{}};
    Object.keys(areaData).forEach(function(knr) {{
      var c = areaData[knr];
      var refNum = c && (c.sap_nummer || c.kunden_nr || knr);
      if(fwIsExcludedNumber(refNum)) return;
      if(!c || !c.tours || !c.tours[day]) return;
      var t = c.tours[day].toString().trim();
      if(!t || t === "\u2014" || t === "-" || fwIsExcludedNumber(t)) return;
      seen[t] = 1;
    }});
  }});
  return Object.keys(seen).sort(function(a,b) {{
    return (parseInt(a,10)||0) - (parseInt(b,10)||0) || a.localeCompare(b, "de");
  }});
}}

// ── Telefonliste ──────────────────────────────────────────────────────────────
function fwTodayLabel() {{
  return new Date().toLocaleDateString("de-DE", {{ day:"2-digit", month:"2-digit", year:"numeric" }});
}}

function fwInitDatePicker() {{
  var el = document.getElementById("fw-date-picker");
  if(!el) return;
  if(!fwSelectedDate) {{
    var now = new Date();
    var month = String(now.getMonth() + 1).padStart(2, "0");
    var day = String(now.getDate()).padStart(2, "0");
    fwSelectedDate = now.getFullYear() + "-" + month + "-" + day;
  }}
  el.value = fwSelectedDate;
}}

function fwDisplayDate() {{
  if(fwSelectedDate) {{
    var parts = fwSelectedDate.split("-");
    if(parts.length === 3) return parts[2] + "." + parts[1] + "." + parts[0];
  }}
  return fwTodayLabel();
}}

function fwGetSelectedDay() {{
  if(!fwSelectedDay) {{
    alert("Bitte zuerst einen Fahrzeugwaesche-Tag auswaehlen.");
    return null;
  }}
  return fwSelectedDay;
}}

function fwFilterTours(tours) {{
  return (tours || []).filter(function(t) {{
    return !fwIsExcludedNumber(t);
  }});
}}

function fwExportPdf() {{
  if(!vzAllData) {{
    alert("Die Wochendaten sind noch nicht bereit. Bitte kurz warten und erneut versuchen.");
    try {{ document.getElementById("frame-druck").contentWindow.postMessage({{type:"request-vz-data"}}, "*"); }} catch(e) {{}}
    return;
  }}
  var day = fwGetSelectedDay();
  if(!day) return;
  if(!window.jspdf || !window.jspdf.jsPDF || typeof window.jspdf.jsPDF !== "function") {{
    alert("PDF-Bibliothek ist nicht geladen.");
    return;
  }}

  var jsPDF = window.jspdf.jsPDF;
  var doc = new jsPDF({{ orientation:"portrait", unit:"mm", format:"a4" }});
  var label = (INSTANCES[currentInst] && INSTANCES[currentInst].name) ? INSTANCES[currentInst].name : "Woche";
  var tours = fwFilterTours(vzCollectToursByDay(vzAllData, day));

  doc.setFillColor(27, 102, 179);
  doc.roundedRect(12, 10, 186, 18, 3, 3, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(17);
  doc.text("Fahrzeugwaeschen", 16, 18);
  doc.setFontSize(10);
  doc.text(label + " - " + day, 16, 24);
  doc.text("Stand: " + fwDisplayDate(), 155, 24);

  var rows = tours.map(function(t) {{
    return ["", t, "", "", ""];
  }});
  if(!rows.length) rows = [["", "-", "", "", ""]];

  // ── Jeden-Tag-Touren (feste Fahrzeuge) ──
  rows.push([{{content:"Extra", colSpan:5, styles:{{fillColor:[232,240,251], textColor:[27,102,179], fontStyle:"bold", halign:"left"}}}}]);
  var jedenTag = [
    [10, "z.b.V."],
    [3,  "Popp"],
    [1,  "Fuengers"],
    [1,  "Pfeifer"],
    [1,  "Picnic"],
    [3,  "Umfuhr NMS"],
    [5,  "Umfuhr Malchow"],
    [1,  "Langbein"],
    [1,  "Balzer"]
  ];
  jedenTag.forEach(function(item) {{
    for(var i = 0; i < item[0]; i++) {{
      rows.push(["", item[1], "", "", ""]);
    }}
  }});

  doc.autoTable({{
    startY: 33,
    head: [["Fahrer Name", "Tour", "Reinigung ja/nein", "Grund warum nicht gewaschen wurde", "Uhrzeit / Feierabend"]],
    body: rows,
    theme: "grid",
    styles: {{
      font: "helvetica",
      fontSize: 10,
      cellPadding: 2.6,
      lineColor: [205, 213, 225],
      lineWidth: 0.2,
      minCellHeight: 10,
      valign: "middle",
      overflow: "linebreak"
    }},
    headStyles: {{
      fillColor: [232, 240, 251],
      textColor: [27, 102, 179],
      fontStyle: "bold",
      halign: "left",
      lineColor: [27, 102, 179],
      lineWidth: 0.25
    }},
    columnStyles: {{
      0: {{ cellWidth: 33 }},
      1: {{ cellWidth: 23, halign: "center" }},
      2: {{ cellWidth: 33 }},
      3: {{ cellWidth: 56 }},
      4: {{ cellWidth: 41 }}
    }},
    margin: {{ left: 12, right: 12, top: 10, bottom: 12 }},
    didDrawPage: function(data) {{
      var pageSize = doc.internal.pageSize;
      var pageHeight = pageSize.height || pageSize.getHeight();
      doc.setFontSize(8);
      doc.setTextColor(100, 116, 139);
      doc.text(day + "  |  " + label, 12, pageHeight - 6);
      doc.text("Seite " + doc.getCurrentPageInfo().pageNumber, pageSize.width - 24, pageHeight - 6);
    }}
  }});

  doc.save("Fahrzeugwaeschen_" + day + "_" + label.replace(/[\\\\/:*?\"<>|]+/g, "_") + ".pdf");
}}

{wash_js_code}
{wash_ranking_js_code}
{fw_graph_js_code}
// Zusatzdaten wurden oben komprimiert eingebettet und durch loadEmbeddedData() geladen.
var ZULAGE_XLSX_SONDER      = "{zulage_xlsx_sonder}";
var ZULAGE_XLSX_FUENGERS    = "{zulage_xlsx_fuengers}";
var ZULAGE_XLSX_DRITTKUNDEN = "{zulage_xlsx_drittkunden}";
var faActiveTab              = "schichten";

{spesen_js_code}

{sped_js_code}

{fabew_js_code}

// ── Großkunden ────────────────────────────────────────────────────────────────
var gkSelected = 0;
var gkSearchQ = "";

function gkEsc(v) {{
  return String(v == null ? "" : v)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

function gkAttr(v) {{
  return gkEsc(v).replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}}

// ── Deterministische Akzentfarbe + Monogramm pro Kunde ──────────────────────
function gkHue(name) {{
  var s = String(name || ""), h = 0, i;
  for (i = 0; i < s.length; i++) {{ h = (h * 31 + s.charCodeAt(i)) >>> 0; }}
  return h % 360;
}}

function gkAccentStyle(name) {{
  var h = gkHue(name);
  return "--gk:hsl(" + h + ",54%,42%);--gks:hsl(" + h + ",62%,95%);"
       + "--gkb:hsl(" + h + ",46%,86%);--gkd:hsl(" + h + ",55%,30%);";
}}

function gkInitials(name) {{
  var raw = String(name || "");
  var words = raw.replace(/[^0-9A-Za-zÄÖÜäöüß ]/g, " ").split(/\\s+/).filter(Boolean)
    .filter(function(w){{ return !/^(gmbh|mbh|kg|co|ag|ohg|ek|und|der|die|das)$/i.test(w); }});
  if (!words.length) words = raw.split(/\\s+/).filter(Boolean);
  var a = words[0] ? words[0].charAt(0) : "?";
  var b = words.length > 1 ? words[1].charAt(0) : "";
  return ((a + b).toUpperCase()) || "?";
}}

function gkMonoHtml(name, cls) {{
  return "<span class='" + (cls || "gk-mono") + "'>" + gkEsc(gkInitials(name)) + "</span>";
}}

function gkExtractEmails(v) {{
  var s = String(v == null ? "" : v);
  return s.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{{2,}}/ig) || [];
}}

function gkUniqEmails(list) {{
  var out = [];
  (list || []).forEach(function(e) {{
    var mail = String(e || "").trim();
    if (mail && out.indexOf(mail) === -1) out.push(mail);
  }});
  return out;
}}

function gkMailto(emails, subject) {{
  var list = gkUniqEmails(emails);
  var href = "mailto:" + list.join(";");
  if (subject) href += "?subject=" + encodeURIComponent(subject);
  return href;
}}

function gkRastingNfcEmailsFromRows(customer, rows) {{
  if (!customer || !/rasting/i.test(customer.name || "")) return [];
  var mails = [];
  var section = "";
  (rows || []).forEach(function(cr) {{
    if (cr && cr.isLabel) {{
      section = String(cr.text || "").replace(/:$/, "").toLowerCase().trim();
      return;
    }}
    var belongsToNfc = section.indexOf("nfc") >= 0;
    if (!belongsToNfc && cr && cr.labelText && /(^|\b)nfc(\b|$)/i.test(cr.labelText)) belongsToNfc = true;
    if (!belongsToNfc) return;
    (cr.emails || []).forEach(function(em) {{
      gkExtractEmails(em).forEach(function(addr) {{ mails.push(addr); }});
    }});
  }});
  return gkUniqEmails(mails);
}}

function gkRastingNfcEmailsFromLines(customer) {{
  if (!customer || !/rasting/i.test(customer.name || "")) return [];
  var mails = [];
  var section = "";
  (customer.lines || []).forEach(function(line) {{
    var t = String(line || "").trim();
    if (!t) return;
    if (gkIsSection(t)) section = t.replace(/:$/, "").toLowerCase().trim();
    var belongsToNfc = section.indexOf("nfc") >= 0 || /(^|\b)nfc(\b|$)/i.test(t);
    if (belongsToNfc) {{
      gkExtractEmails(t).forEach(function(addr) {{ mails.push(addr); }});
    }}
  }});
  return gkUniqEmails(mails);
}}

function gkDistributorLinkHtml(emails, label, subject) {{
  emails = gkUniqEmails(emails);
  if (!emails.length) return "";
  return "<a class='gk-act gk-act-sm' href='" + gkAttr(gkMailto(emails, subject || label)) + "'>"
       + "&#9993; " + gkEsc(label) + " <b>" + emails.length + "</b></a>";
}}

var GK_EDEKA_LAGER_NMS_EMAILS = [
  "Daniel.Kriegel@edeka.de",
  "Eugen.Kifer@edeka.de",
  "Jerome.Gitzel@EDEKA.de",
  "Maike.Linde@edeka.de",
  "thomas.manzke@edeka.de",
  "marco.doerfert@edeka.de",
  "stephan.bruhn@edeka.de",
  "alexa.offertaler@edeka.de",
  "alona.tymoshevska@edeka.de"
];

function gkIsEdekaLager(customer) {{
  var n = String(customer && customer.name ? customer.name : "").toLowerCase();
  return n.indexOf("edeka") >= 0 && n.indexOf("lager") >= 0;
}}

function gkEdekaLagerNmsLink(customer) {{
  if (!gkIsEdekaLager(customer)) return "";
  return gkDistributorLinkHtml(GK_EDEKA_LAGER_NMS_EMAILS, "NMS", "Edeka Lager / NMS");
}}

// Spaltentyp aus Header-Name ermitteln
function gkColType(header) {{
  var h = (header || "").toLowerCase();
  if (/mail|e-mail|email/.test(h))              return "email";
  if (/tel|telefon|mobil|fax|phone/.test(h))    return "tel";
  if (/adress|strasse|street|plz|ort|city/.test(h)) return "addr";
  if (/hinweis|info|bemerkung|anmerkung|notiz|note/.test(h)) return "hint";
  if (/ansprechpartner|kontakt|contact/.test(h)) return "contact";
  return "text";
}}

// Ist ein Zellwert ein Abschnitts-Header? (endet mit ":" und kurz)
function gkIsSection(v) {{
  var t = (v || "").trim();
  return t.endsWith(":") && t.length < 60 && !t.includes("@");
}}

function gkCustomerText(k) {{
  var parts = [k && k.name ? k.name : ""];
  if (k && k.type === "structured") {{
    (k.entries || []).forEach(function(e) {{
      parts.push(e.name || "");
      parts.push(e.kundennummer || "");
    }});
  }} else if (k && k.lines) {{
    parts = parts.concat(k.lines.slice(0, 60));
  }}
  return parts.join(" ").toLowerCase();
}}

function gkVisibleIndexes() {{
  var q = (gkSearchQ || "").toLowerCase().trim();
  var out = [];
  (GK_DATA || []).forEach(function(k, i) {{
    if (!q || gkCustomerText(k).indexOf(q) >= 0) out.push(i);
  }});
  return out;
}}

function gkFilter(q) {{
  gkSearchQ = q || "";
  var visible = gkVisibleIndexes();
  if (visible.indexOf(gkSelected) < 0) gkSelected = visible.length ? visible[0] : -1;
  gkBuildTiles(gkSelected);
  if (gkSelected >= 0) gkShow(gkSelected, true);
  else {{
    var detail = document.getElementById("gk-detail");
    if (detail) detail.innerHTML = "<div class='gk-empty'>Kein Gro&#223;kunde f&#252;r diese Suche.</div>";
  }}
}}

function gkBuildTiles(activeIdx) {{
  var tilesEl = document.getElementById("gk-tiles");
  var countEl = document.getElementById("gk-count");
  var statsEl = document.getElementById("gk-stats");
  if (!tilesEl) return;
  var total = (GK_DATA || []).length;
  var visible = gkVisibleIndexes();
  if (countEl) countEl.textContent = "(" + total + ")";
  if (statsEl) statsEl.textContent = visible.length + " angezeigt";
  var html = "";
  visible.forEach(function(realIdx) {{
    var k = GK_DATA[realIdx];
    var active = realIdx === activeIdx;
    var singleKnr = "";
    var multiCount = 0;
    if (k.type === "structured" && k.entries && k.entries.length) {{
      if (k.entries.length === 1) singleKnr = k.entries[0].kundennummer || "";
      else multiCount = k.entries.length;
    }}
    var meta = singleKnr
      ? "<span class='gk-knr'><span class='gk-knr-l'>KNr</span><span class='gk-knr-v'>" + gkEsc(singleKnr) + "</span></span>"
      : (multiCount ? "<span class='gk-tile-meta'>" + multiCount + " Standorte</span>" : "");
    html += "<button type='button' onclick='gkShow(" + realIdx + ")'"
          + " class='gk-tile" + (active ? " is-active" : "") + "'"
          + " style='" + gkAccentStyle(k.name) + "'>"
          + gkMonoHtml(k.name, "gk-mono")
          + "<span class='gk-tile-txt'>"
          + "<span class='gk-tile-name'>" + gkEsc(k.name) + "</span>"
          + (meta ? meta : "")
          + "</span>"
          + "</button>";
  }});
  tilesEl.innerHTML = html || "<div class='gk-tile-meta' style='padding:8px 4px;'>Keine Treffer.</div>";
}}

function gkRender() {{
  var detail = document.getElementById("gk-detail");
  if (!detail) return;
  if (!GK_DATA || !GK_DATA.length) {{
    gkBuildTiles(-1);
    detail.innerHTML = "<div class='gk-empty'>Keine Gro\u00dfkundendaten \u2013 bitte Excel in Streamlit hochladen.</div>";
    return;
  }}
  var visible = gkVisibleIndexes();
  gkShow(visible.length ? visible[0] : 0);
}}

function gkShow(idx, skipTiles) {{
  gkSelected = idx;
  if (!skipTiles) gkBuildTiles(idx);
  var detail = document.getElementById("gk-detail");
  if (!detail) return;
  var customer = GK_DATA[idx];
  if (!customer) {{
    detail.innerHTML = "<div class='gk-empty'>Kein Gro&#223;kunde ausgew&#228;hlt.</div>";
    return;
  }}
  if (customer.type === "structured") gkRenderStructured(customer, detail);
  else gkRenderFreeform(customer, detail);
  detail.scrollTop = 0;
}}

// ── Structured Renderer ────────────────────────────────────────────────────────
function gkRenderStructured(customer, detail) {{
  var headers = customer.content_headers || [];
  var colTypes = headers.map(gkColType);

  function idxOf(type) {{
    return headers.map(function(h,i){{ return colTypes[i]===type?i:-1; }}).filter(function(i){{return i>=0;}});
  }}
  var addrIdx  = idxOf("addr");
  var emailIdx = idxOf("email");
  var telIdx   = idxOf("tel");
  var hintIdx  = idxOf("hint");
  var otherIdx = headers.map(function(h,i){{
    return ["addr","email","tel","hint"].indexOf(colTypes[i])===-1 ? i : -1;
  }}).filter(function(i){{return i>=0;}});

  function isLabel(v) {{ return v && !v.includes("@") && v.trim().length > 0; }}
  function isPhone(v) {{ return v && /[\\d]{{4,}}/.test(v.trim()); }}

  // Sheet-weite Hinweise über alle Entries
  var allHints = [];
  customer.entries.forEach(function(e) {{
    (e.rows||[]).forEach(function(row) {{
      hintIdx.forEach(function(ci) {{
        var v = (row[ci]||"").trim();
        if (v && allHints.indexOf(v)===-1) allHints.push(v);
      }});
    }});
  }});

  var entryCount = customer.entries.length;
  var subline = entryCount === 1 ? "1 Standort" : entryCount + " Standorte";

  // Alle E-Mail-Adressen des Kunden für "Mail an alle"
  var gkAllEmails = [];
  customer.entries.forEach(function(entry) {{
    (entry.rows||[]).forEach(function(row) {{
      emailIdx.forEach(function(ci) {{
        gkExtractEmails(row[ci]||"").forEach(function(a){{ gkAllEmails.push(a); }});
      }});
    }});
  }});
  gkAllEmails = gkUniqEmails(gkAllEmails);

  var html = "<div class='gk-fade' style='" + gkAccentStyle(customer.name) + "width:100%;'>";

  // ── Hero ─────────────────────────────────────────────────────────────────────
  html += "<div class='gk-hero'>"
        + gkMonoHtml(customer.name, "gk-hero-mono")
        + "<div class='gk-hero-txt'>"
        + "<div class='gk-hero-name'>" + gkEsc(customer.name) + "</div>"
        + "<div class='gk-hero-sub'>" + subline + "</div>"
        + "</div>";
  var heroActs = "";
  if (gkAllEmails.length) heroActs += "<a class='gk-act' href='" + gkAttr(gkMailto(gkAllEmails, customer.name)) + "'>&#9993; Mail an alle <b>" + gkAllEmails.length + "</b></a>";
  heroActs += gkEdekaLagerNmsLink(customer);
  if (heroActs) html += "<div class='gk-actions'>" + heroActs + "</div>";
  html += "</div>";

  // ════════════════════════════════════════════════════════════════════════════
  // BLOCK 1: Standorte
  // ════════════════════════════════════════════════════════════════════════════
  // Daten vorab sammeln, dann Layout entscheiden
  var standorte = customer.entries.map(function(entry) {{
    var addrLines = [];
    if (addrIdx.length) {{
      (entry.rows||[]).forEach(function(row) {{
        addrIdx.forEach(function(ci) {{
          var v = (row[ci]||"").trim();
          if (v && addrLines.indexOf(v)===-1) addrLines.push(v);
        }});
      }});
    }}
    return {{ name: entry.name, kundennummer: entry.kundennummer, addrLines: addrLines }};
  }});
  var anyHasAddr = standorte.some(function(s) {{ return s.addrLines.length > 0; }});

  // KNr-Badge-Helper (kundenfarben getönt)
  function gkKnrBadge(knr) {{
    if (!knr) return "";
    return "<span class='gk-knr'><span class='gk-knr-l'>KNr</span>"
         + "<span class='gk-knr-v'>" + gkEsc(knr) + "</span></span>";
  }}

  html += "<div class='gk-card'>";
  html += "<div class='gk-card-h'><span class='gk-eyebrow'>Standorte</span>"
        + "<span class='gk-cnt'>" + entryCount + "</span></div>";

  if (anyHasAddr) {{
    html += "<div class='gk-card-b'>";
    standorte.forEach(function(s) {{
      html += "<div class='gk-loc'>";
      html += "<div class='gk-loc-name'>" + gkEsc(s.name) + gkKnrBadge(s.kundennummer) + "</div>";
      if (s.addrLines.length) {{
        html += "<div class='gk-loc-addr'>" + s.addrLines.map(gkEsc).join("<br>") + "</div>";
      }}
      html += "</div>";
    }});
    html += "</div>";
  }} else {{
    html += "<div class='gk-loc-chips' style='padding-left:16px;padding-right:16px;'>";
    standorte.forEach(function(s) {{
      html += "<div class='gk-loc-chip'>" + gkEsc(s.name) + gkKnrBadge(s.kundennummer) + "</div>";
    }});
    html += "</div>";
  }}
  html += "</div>";

  // ════════════════════════════════════════════════════════════════════════════
  // BLOCK 2+3: Kontakte links, Hinweise rechts — zweispaltiges Layout
  // ════════════════════════════════════════════════════════════════════════════
  var hasAnyContact = emailIdx.length || telIdx.length || otherIdx.length;

  // Alle Kontaktzeilen über alle Entries sammeln
  var allContactRows = [];
  customer.entries.forEach(function(entry) {{
    (entry.rows||[]).forEach(function(row) {{
      var labelVal = null;
      emailIdx.forEach(function(ci) {{
        var v = (row[ci]||"").trim();
        if (v && isLabel(v)) labelVal = v;
      }});
      if (!labelVal) {{
        otherIdx.forEach(function(ci) {{
          var v = (row[ci]||"").trim();
          if (v && isLabel(v) && !isPhone(v)) labelVal = labelVal || v;
        }});
      }}
      // Tel dieser Zeile immer zuerst bestimmen
      var rowTels = [];
      telIdx.forEach(function(ci) {{
        var v = (row[ci]||"").trim();
        if (v && isPhone(v)) rowTels.push(v);
      }});

      if (labelVal) {{
        if (rowTels.length) {{
          // Text + Tel auf gleicher Zeile → Kontaktzeile mit Beschriftung
          allContactRows.push({{isLabel:false, labelText:labelVal, emails:[], tels:rowTels}});
        }} else {{
          // Reiner Text ohne Tel → Section-Chip
          allContactRows.push({{isLabel:true, text:labelVal}});
        }}
        return;
      }}
      var rowEmails = [];
      emailIdx.forEach(function(ci) {{
        var v = (row[ci]||"").trim();
        if (v && !isLabel(v)) rowEmails.push(v);
      }});
      if (rowEmails.length || rowTels.length) {{
        // Tel-only-Zeile ohne Email → zur letzten Kontaktzeile ohne Tel mergen
        if (!rowEmails.length && rowTels.length) {{
          var last = allContactRows.length ? allContactRows[allContactRows.length-1] : null;
          if (last && !last.isLabel && !last.isOther && (!last.tels || !last.tels.length)) {{
            last.tels = rowTels;
            return;
          }}
        }}
        allContactRows.push({{isLabel:false, emails:rowEmails, tels:rowTels}});
      }}
    }});
    // Sonstige Spalten dieser Entry
    otherIdx.forEach(function(ci) {{
      var vals = [];
      (entry.rows||[]).forEach(function(row) {{
        var v = (row[ci]||"").trim();
        if (v && !isLabel(v) && vals.indexOf(v)===-1) vals.push(v);
      }});
      if (vals.length) {{
        allContactRows.push({{isOther:true, header:headers[ci], vals:vals}});
      }}
    }});
  }});

  var rastingNfcEmails = gkRastingNfcEmailsFromRows(customer, allContactRows);

  // Zwei-Spalten-Layout: Kontakte links, Hinweise rechts
  html += "<div class='gk-grid'>";

  // ── LINKE SPALTE: Kontakte ─────────────────────────────────────────────────
  html += "<div style='min-width:0;'>";
  if (hasAnyContact && allContactRows.length) {{
    html += "<div class='gk-card'>";
    html += "<div class='gk-card-h'><span class='gk-eyebrow'>Kontakte</span></div>";
    html += "<div class='gk-card-b'>";
    allContactRows.forEach(function(cr, ri) {{
      if (cr.isLabel) {{
        var sectionName = cr.text.replace(/:$/, "");
        var sKey = sectionName.toLowerCase().trim();
        html += "<div class='gk-c-sec'><span class='gk-c-sec-l'>" + gkEsc(sectionName) + "</span>";
        if (sKey === "nfc" && rastingNfcEmails.length) {{
          html += gkDistributorLinkHtml(rastingNfcEmails, "Mail an den Verteiler", "Rasting / NFC");
        }}
        html += "</div>";
      }} else if (cr.isOther) {{
        html += "<div class='gk-c-other'>"
              + "<span class='gk-c-other-l'>" + gkEsc(cr.header) + "</span>"
              + "<span class='gk-c-other-v'>" + cr.vals.map(gkEsc).join("<br>") + "</span>"
              + "</div>";
      }} else {{
        var hasEmail     = cr.emails && cr.emails.length;
        var hasLabelText = cr.labelText && cr.labelText.length;
        var hasTel       = cr.tels && cr.tels.length;
        html += "<div class='gk-c-row'><span class='gk-c-left'>";
        if (hasEmail) {{
          html += cr.emails.map(function(em) {{
            var isMailAddr = em.match(/^[^\\s@]+@[^\\s@]+/) !== null;
            return isMailAddr
              ? "<a class='gk-mail' href='mailto:" + gkEsc(em) + "'>" + gkEsc(em) + "</a>"
              : "<span class='gk-c-label'>" + gkEsc(em) + "</span>";
          }}).join(" ");
        }} else if (hasLabelText) {{
          html += "<span class='gk-c-label'>" + gkEsc(cr.labelText) + "</span>";
        }} else {{
          html += "<span class='gk-c-dash'>&ndash;</span>";
        }}
        html += "</span>";
        if (hasTel) {{
          html += cr.tels.map(function(t) {{
              var href = "tel:" + t.replace(/[^\\d\\+]/g,"");
              return "<a class='gk-tel' href='" + href + "'>"
                   + "<span class='gk-tel-i'>&#9742;</span>" + gkEsc(t) + "</a>";
            }}).join("");
        }}
        html += "</div>";
      }}
    }});
    html += "</div></div>"; // body + card
  }}
  html += "</div>"; // linke Spalte

  // ── RECHTE SPALTE: Hinweise ────────────────────────────────────────────────
  html += "<div style='min-width:0;'>";
  if (allHints.length) {{
    html += "<div class='gk-card'>";
    html += "<div class='gk-card-h'><span class='gk-eyebrow'>Hinweise</span>"
          + "<span class='gk-cnt'>" + allHints.length + "</span></div>";
    html += "<div>";
    allHints.forEach(function(h, i) {{
      html += "<div class='gk-hint'><span class='gk-hint-n'>" + (i+1) + "</span>"
            + "<span class='gk-hint-t'>" + gkEsc(h) + "</span></div>";
    }});
    html += "</div></div>";
  }}
  html += "</div>"; // rechte Spalte

  html += "</div>"; // grid

  html += "</div>"; // root
  detail.innerHTML = html;
}}

// ── Freeform Renderer ─────────────────────────────────────────────────────────
function gkRenderFreeform(customer, detail) {{
  var emailRe = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
  var rastingNfcEmails = gkRastingNfcEmailsFromLines(customer);
  var allMails = gkUniqEmails((customer.lines || []).reduce(function(acc, l){{ return acc.concat(gkExtractEmails(l)); }}, []));
  var html = "<div class='gk-fade' style='" + gkAccentStyle(customer.name) + "max-width:780px;'>";

  html += "<div class='gk-hero'>"
        + gkMonoHtml(customer.name, "gk-hero-mono")
        + "<div class='gk-hero-txt'><div class='gk-hero-name'>" + gkEsc(customer.name) + "</div></div>";
  var ffActs = "";
  if (allMails.length) ffActs += "<a class='gk-act' href='" + gkAttr(gkMailto(allMails, customer.name)) + "'>&#9993; Mail an alle <b>" + allMails.length + "</b></a>";
  ffActs += gkEdekaLagerNmsLink(customer);
  if (ffActs) html += "<div class='gk-actions'>" + ffActs + "</div>";
  html += "</div>";

  html += "<div class='gk-card'>";

  (customer.lines || []).forEach(function(line) {{
    var t = line.trim();
    if (!t) return;
    var isEmail = emailRe.test(t);
    var isSection = gkIsSection(t);
    var hasTelNum = !isEmail && /\\d{{5,}}/.test(t) && t.length < 60;
    var isLong = t.length > 90;

    if (isSection) {{
      var sectionName = t.replace(/:$/, "");
      var sectionKey = sectionName.toLowerCase().trim();
      html += "<div class='gk-c-sec' style='margin:0;padding:11px 16px;border-bottom:1px solid #eef2f7;background:linear-gradient(180deg,#fbfdff,#fff);'>"
            + "<span class='gk-c-sec-l'>" + gkEsc(sectionName) + "</span>";
      if (sectionKey === "nfc" && rastingNfcEmails.length) {{
        html += gkDistributorLinkHtml(rastingNfcEmails, "Mail an den Verteiler", "Rasting / NFC");
      }}
      html += "</div>";
    }} else if (isEmail) {{
      html += "<div class='gk-ff-row'><a class='gk-mail' href='mailto:" + gkEsc(t) + "'>" + gkEsc(t) + "</a></div>";
    }} else if (hasTelNum) {{
      html += "<div class='gk-ff-row'><a class='gk-tel' href='tel:" + gkEsc(t.replace(/[^\\d\\+]/g,"")) + "'>"
            + "<span class='gk-tel-i'>&#9742;</span>" + gkEsc(t) + "</a></div>";
    }} else if (isLong) {{
      html += "<div class='gk-ff-row gk-ff-long'>" + gkEsc(t) + "</div>";
    }} else {{
      html += "<div class='gk-ff-row'>" + gkEsc(t) + "</div>";
    }}
  }});

  html += "</div></div>";
  detail.innerHTML = html;
}}


function telPDF() {{
  var w = window.open("","_blank","width=900,height=700");
  var css = [
    "body{{font-family:'Segoe UI',Arial,sans-serif;font-size:8pt;margin:10mm;color:#000}}",
    "h1{{font-size:13pt;color:#1b66b3;margin:0 0 4mm 0;border-bottom:2px solid #1b66b3;padding-bottom:2mm}}",
    ".gruppe{{font-size:8pt;font-weight:900;text-transform:uppercase;color:#1b66b3;margin:4mm 0 1.5mm 0;letter-spacing:.3px;border-bottom:1px solid #1b66b3;padding-bottom:0.5mm}}",
    ".grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:1mm 3mm}}",
    ".item{{padding:0.8mm 0;border-bottom:1px solid #eee;line-height:1.3}}",
    ".iname{{font-weight:800;font-size:7.5pt}}",
    ".itel{{color:#1b66b3;font-size:7.5pt}}",
    ".imail{{color:#888;font-size:6.5pt;font-style:italic}}",
    ".irole{{font-size:6.5pt;color:#dc2626;font-weight:700}}",
    "@media print{{@page{{size:A4 portrait;margin:10mm}}body{{margin:0}}}}"
  ].join("");
  var body = "<h1>&#128222; Telefonliste</h1>";
  TEL_DATA.forEach(function(g) {{
    if(!g.personen.length) return;
    body += "<div class='gruppe'>" + g.gruppe + " (" + g.personen.length + ")</div>";
    body += "<div class='grid'>";
    g.personen.forEach(function(p) {{
      body += "<div class='item'>";
      body += "<div class='iname'>" + p.name + "</div>";
      body += "<div class='itel'>&#128222; " + p.tel + "</div>";
      if(p.mail) {{
        var isRole = (p.mail==="Disponent"||p.mail==="Chef");
        body += isRole
          ? "<div class='irole'>" + p.mail + "</div>"
          : "<div class='imail'>&#9993; " + p.mail + "</div>";
      }}
      body += "</div>";
    }});
    body += "</div>";
  }});
  w.document.write("<!DOCTYPE html><html><head><meta charset='utf-8'>");
  w.document.write("<title>Telefonliste NordFrischeCenter</title>");
  w.document.write("<style>" + css + "</style></head><body>");
  w.document.write(body);
  w.document.write("</body></html>");
  w.document.close();
  w.focus();
  setTimeout(function(){{ w.print(); }}, 400);
}}

function telCopy(tel) {{
  if(navigator.clipboard) navigator.clipboard.writeText(tel);
}}
function telCopyBtn(btn) {{
  var t = btn.getAttribute("data-tel") || "";
  if(navigator.clipboard) navigator.clipboard.writeText(t);
  btn.classList.add("copied");
  btn.innerHTML = "&#10003;";
  setTimeout(function(){{ btn.classList.remove("copied"); btn.innerHTML = "&#128203;"; }}, 1100);
}}
function telAvatarHtml(name) {{
  var h = gkHue(name);
  return "<span class='tel-av' style='background:hsl(" + h + ",58%,93%);color:hsl(" + h + ",48%,32%);'>"
       + gkEsc(gkInitials(name)) + "</span>";
}}
function telHover(el, on) {{
  el.style.background = on ? "#eff6ff" : "#fff";
}}
function telFilter(q) {{ telRender(q); }}

function telRender(q) {{
  q = (q || "").toLowerCase().trim();
  var html = "";
  var totalShown = 0;
  TEL_DATA.forEach(function(g) {{
    var hits = g.personen.filter(function(p) {{
      return !q || p.name.toLowerCase().includes(q) || p.tel.includes(q);
    }});
    if(!hits.length) return;
    totalShown += hits.length;
    html += "<div class='tel-group'>";
    html += "<div class='tel-group-h'><span class='tel-group-n'>" + gkEsc(g.gruppe) + "</span>"
          + "<span class='tel-group-c'>" + hits.length + "</span></div>";
    html += "<div class='tel-grid'>";
    hits.forEach(function(p) {{
      var safetel = String(p.tel).replace(/'/g,"&#39;");
      var telClean = String(p.tel).replace(/[^\\d+]/g,"");
      var isRole = (p.mail === "Disponent" || p.mail === "Chef");
      html += "<div class='tel-card'>";
      html += telAvatarHtml(p.name);
      html += "<div class='tel-body'>";
      html += "<div class='tel-name' title='" + gkAttr(p.name) + "'>" + gkEsc(p.name) + "</div>";
      html += "<a class='tel-num' href='tel:" + telClean + "' title='Anrufen: " + gkAttr(p.tel) + "'>"
            + "<span class='tel-num-i'>&#128222;</span>" + gkEsc(p.tel) + "</a>";
      if(p.mail) {{
        html += isRole
          ? "<span class='tel-role'>" + gkEsc(p.mail) + "</span>"
          : "<a class='tel-mail' href='mailto:" + gkAttr(p.mail) + "' title='" + gkAttr(p.mail) + "'>&#9993; " + gkEsc(p.mail) + "</a>";
      }}
      html += "</div>";
      html += "<button class='tel-copy' type='button' title='Nummer kopieren' data-tel='" + safetel + "' onclick='telCopyBtn(this)'>&#128203;</button>";
      html += "</div>";
    }});
    html += "</div></div>";
  }});
  if(!html) html = "<div class='tel-empty'>Keine Treffer.</div>";
  document.getElementById("tel-content").innerHTML = html;
  var cnt = document.getElementById("tel-count");
  if(cnt) cnt.textContent = totalShown ? (totalShown + (totalShown === 1 ? " Kontakt" : " Kontakte")) : "";
}}
// ── Samstags Fahrer ───────────────────────────────────────────────────────────
var samCurrentSort  = "count";
var samYearFilter   = String(new Date().getFullYear());
var samStatusFilter = "all";
var samViewMode     = "list";
var samChartMode    = "matrix";
var samLastFiltered = [];
var SAM_ZIEL        = 12;

function samSort(mode) {{
  samCurrentSort = mode;
  ["name","count","status"].forEach(function(m) {{
    var btn = document.getElementById("sam-sort-"+m);
    if(!btn) return;
    btn.style.background = mode===m ? "#1b66b3" : "#fff";
    btn.style.color      = mode===m ? "#fff"    : "#1b66b3";
  }});
  samRender((document.getElementById("sam-search")||{{value:""}}).value);
}}

function samFilter(q) {{ samRender(q); }}

function samYearChange(yr) {{
  samYearFilter = String(yr || new Date().getFullYear());
  samStatusFilter = "all";
  samRender((document.getElementById("sam-search")||{{value:""}}).value);
}}

function samSetStatusFilter(status) {{
  samStatusFilter = status || "all";
  samRender((document.getElementById("sam-search")||{{value:""}}).value);
}}

function samSetView(mode) {{
  samViewMode = mode === "charts" ? "charts" : "list";
  var listBtn = document.getElementById("sam-view-list");
  var chartBtn = document.getElementById("sam-view-charts");
  if(listBtn) {{
    listBtn.style.background = samViewMode === "list" ? "#1b66b3" : "#fff";
    listBtn.style.color = samViewMode === "list" ? "#fff" : "#1b66b3";
  }}
  if(chartBtn) {{
    chartBtn.style.background = samViewMode === "charts" ? "#1b66b3" : "#fff";
    chartBtn.style.color = samViewMode === "charts" ? "#fff" : "#1b66b3";
  }}
  var sortBtns = document.getElementById("sam-list-sort-buttons");
  var chartTabs = document.getElementById("sam-chart-tabs");
  if(sortBtns) sortBtns.style.display = samViewMode === "list" ? "flex" : "none";
  if(chartTabs) chartTabs.style.display = samViewMode === "charts" ? "flex" : "none";
  samRender((document.getElementById("sam-search")||{{value:""}}).value);
}}

function samSetChartMode(mode) {{
  samChartMode = ["matrix","drivers","months"].indexOf(mode) >= 0 ? mode : "matrix";
  ["matrix","drivers","months"].forEach(function(m) {{
    var btn = document.getElementById("sam-chart-"+m);
    if(!btn) return;
    btn.style.background = samChartMode === m ? "#1b66b3" : "#fff";
    btn.style.color = samChartMode === m ? "#fff" : "#1b66b3";
  }});
  samRender((document.getElementById("sam-search")||{{value:""}}).value);
}}

function samStatBtn(status, html, bg, color, title) {{
  var active = samStatusFilter === status;
  return "<button type='button' onclick='samSetStatusFilter(&quot;"+status+"&quot;)' title='"+samEsc(title||"")+"' " +
    "style='display:inline-flex;align-items:center;gap:3px;border-radius:4px;padding:2px 8px;" +
    "font-family:inherit;font-size:11px;font-weight:800;line-height:1.4;cursor:pointer;" +
    "background:"+bg+";color:"+color+";border:1px solid "+(active?color:"transparent")+";" +
    "box-shadow:"+(active?"0 0 0 2px rgba(15,23,42,.10) inset":"none")+";'>" + html + "</button>";
}}

function samSaturdaysElapsed(year) {{
  var today = new Date();
  var d = new Date(year, 0, 1);
  while(d.getDay() !== 6) d.setDate(d.getDate()+1);
  var count = 0;
  while(d.getFullYear() === year && d <= today) {{
    count++;
    d.setDate(d.getDate()+7);
  }}
  return count;
}}

function samTotalSaturdays(year) {{
  var count = 0;
  var d = new Date(year, 0, 1);
  while(d.getDay() !== 6) d.setDate(d.getDate()+1);
  while(d.getFullYear() === year) {{ count++; d.setDate(d.getDate()+7); }}
  return count;
}}

function samParseYear(datum) {{
  var m = String(datum||"").match(/(\\d{{2}}\\.\\d{{2}}\\.(\\d{{4}}))/);
  return m ? parseInt(m[2],10) : null;
}}

function samEsc(v) {{
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}}

function samAttr(v) {{
  return samEsc(v).replace(/'/g, "&#39;");
}}

function samNameKey(v) {{
  var s = String(v||"").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"");
  s = s.replace(/ß/g,"ss").replace(/[^a-z0-9]+/g," ").trim();
  return s.split(/\\s+/).filter(Boolean).sort().join("|");
}}

function samMergeText(a, b) {{
  var parts = [];
  String(a||"").split(/\\s*,\\s*/).concat(String(b||"").split(/\\s*,\\s*/)).forEach(function(x) {{
    x = x.trim();
    if(x && parts.indexOf(x) === -1) parts.push(x);
  }});
  return parts.join(", ");
}}

function samPrepareDrivers() {{
  var merged = {{}};
  (SAM_DATA||[]).forEach(function(d) {{
    var key = String(d.person_key||"").trim();
    if(!key) key = "name:" + samNameKey(d.name||"");
    if(!key || key === "name:") return;
    if(!merged[key]) {{
      merged[key] = {{
        person_key:key,
        name:String(d.name||"").trim(),
        nachname:d.nachname||"",
        vorname:d.vorname||"",
        daten:[],
        aktive_jahre:[]
      }};
    }}
    var out = merged[key];
    if(String(d.name||"").trim().length > String(out.name||"").trim().length) out.name = String(d.name||"").trim();
    (d.aktive_jahre||[]).forEach(function(y) {{
      y = String(y);
      if(out.aktive_jahre.indexOf(y) === -1) out.aktive_jahre.push(y);
    }});
    var eventMap = out._eventMap || (out._eventMap = {{}});
    (d.daten||[]).forEach(function(e) {{
      var eventKey = String(e.iso||"") + "|" + String(e.tag||"") + "|" + String(e.datum||"");
      if(!eventMap[eventKey]) {{
        eventMap[eventKey] = Object.assign({{}}, e);
        out.daten.push(eventMap[eventKey]);
      }} else {{
        eventMap[eventKey].tour = samMergeText(eventMap[eventKey].tour, e.tour);
        eventMap[eventKey].beginn = samMergeText(eventMap[eventKey].beginn, e.beginn);
      }}
    }});
  }});
  return Object.keys(merged).map(function(k) {{
    var d = merged[k];
    delete d._eventMap;
    d.aktive_jahre.sort().reverse();
    d.daten.sort(function(a,b) {{ return String(a.iso||a.datum||"").localeCompare(String(b.iso||b.datum||"")); }});
    return d;
  }});
}}

function samDateFromEntry(e) {{
  var iso = String((e||{{}}).iso||"");
  var m = iso.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
  if(m) return new Date(parseInt(m[1],10),parseInt(m[2],10)-1,parseInt(m[3],10));
  m = String((e||{{}}).datum||"").match(/(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}})/);
  return m ? new Date(parseInt(m[3],10),parseInt(m[2],10)-1,parseInt(m[1],10)) : null;
}}

function samISODate(d) {{
  if(!d) return "";
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");
}}

function samShortDate(d) {{
  return String(d.getDate()).padStart(2,"0")+"."+String(d.getMonth()+1).padStart(2,"0")+".";
}}

function samISOWeek(d) {{
  var x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  var day = x.getUTCDay() || 7;
  x.setUTCDate(x.getUTCDate() + 4 - day);
  var y0 = new Date(Date.UTC(x.getUTCFullYear(),0,1));
  return Math.ceil((((x-y0)/86400000)+1)/7);
}}

function samWeekendSaturday(e) {{
  var d = samDateFromEntry(e);
  if(!d) return null;
  var tag = String(e.tag||"");
  if(tag === "Fr→Sa" || tag === "Fr->Sa") d.setDate(d.getDate()+1);
  else if(tag === "So") d.setDate(d.getDate()-1);
  return d;
}}

function samRender(q) {{
  q = (q||"").toLowerCase().trim();
  var content = document.getElementById("sam-content");
  var statsEl = document.getElementById("sam-stats");
  if(!content) return;

  var prepared = samPrepareDrivers();
  if(!prepared.length) {{
    samLastFiltered = [];
    if(statsEl) statsEl.innerHTML = "";
    content.innerHTML =
      "<div style='color:#64748b;background:#fff;border:1px solid #cbd5e1;border-radius:6px;padding:34px;text-align:center;font-size:14px;line-height:1.55;'>" +
      "Keine Tachograph-Schichten vorhanden.<br><b>Bitte unter Zusatzdateien die Datei timerecording_v3*.csv hochladen.</b></div>";
    return;
  }}

  var today   = new Date();
  var curYear = today.getFullYear();

  var driverData = prepared.map(function(d) {{
    var byYear = {{}};
    (d.daten||[]).forEach(function(e) {{
      var yr = samParseYear(e.datum||"") || (samDateFromEntry(e)||{{getFullYear:function(){{return null;}}}}).getFullYear();
      if(!yr) return;
      var key = String(yr);
      if(!byYear[key]) byYear[key] = [];
      byYear[key].push(e);
    }});
    var activeYears = (d.aktive_jahre||[]).map(function(y){{ return String(y); }});
    return Object.assign({{}}, d, {{_byYear:byYear, _activeYears:activeYears}});
  }});

  var allYears = [String(curYear)];
  driverData.forEach(function(d) {{
    Object.keys(d._byYear).concat(d._activeYears||[]).forEach(function(yr) {{
      if(allYears.indexOf(String(yr)) === -1) allYears.push(String(yr));
    }});
  }});
  allYears.sort(function(a,b){{ return parseInt(b,10)-parseInt(a,10); }});

  if(allYears.indexOf(String(samYearFilter)) === -1) {{
    samYearFilter = allYears.indexOf(String(curYear)) !== -1 ? String(curYear) : allYears[0];
  }}
  var yrSel = document.getElementById("sam-year-sel");
  if(yrSel) {{
    yrSel.innerHTML = allYears.map(function(y){{
      return "<option value='"+samEsc(y)+"'>"+samEsc(y)+"</option>";
    }}).join("");
    yrSel.value = String(samYearFilter);
  }}

  var selectedYear = parseInt(samYearFilter,10) || curYear;
  var satTotal = samTotalSaturdays(selectedYear);
  var satElapsed;
  if(selectedYear < curYear) satElapsed = satTotal;
  else if(selectedYear > curYear) satElapsed = 0;
  else satElapsed = samSaturdaysElapsed(selectedYear);

  var soll = selectedYear < curYear
    ? SAM_ZIEL
    : (selectedYear > curYear ? 0 : Math.round(SAM_ZIEL * satElapsed / Math.max(1,satTotal)));
  var sollText = selectedYear === curYear ? "Soll heute" : "Jahressoll";

  driverData.forEach(function(d) {{
    d._selectedCount = (d._byYear[String(selectedYear)]||[]).length;
    var diff = d._selectedCount - soll;
    if(d._selectedCount >= SAM_ZIEL) d._status = "done";
    else if(diff >= 0)               d._status = "ok";
    else if(diff >= -1)              d._status = "warn";
    else                             d._status = "crit";
    d._diff = diff;
  }});

  var baseFiltered = driverData.filter(function(d) {{
    if(d._activeYears.length && d._activeYears.indexOf(String(selectedYear)) === -1) return false;
    return !q || String(d.name||"").toLowerCase().includes(q);
  }});
  var filtered = baseFiltered.filter(function(d) {{
    return samStatusFilter === "all" || d._status === samStatusFilter;
  }});

  var statusOrder = {{crit:0, warn:1, ok:2, done:3}};
  if(samCurrentSort === "status") {{
    filtered.sort(function(a,b) {{
      var sd = statusOrder[a._status] - statusOrder[b._status];
      return sd !== 0 ? sd : String(a.name||"").localeCompare(String(b.name||""),"de");
    }});
  }} else if(samCurrentSort === "count") {{
    filtered.sort(function(a,b) {{
      return b._selectedCount !== a._selectedCount
        ? b._selectedCount - a._selectedCount
        : String(a.name||"").localeCompare(String(b.name||""),"de");
    }});
  }} else {{
    filtered.sort(function(a,b) {{ return String(a.name||"").localeCompare(String(b.name||""),"de"); }});
  }}

  // Für den Excel-Export genau die aktuell angezeigte Auswahl merken.
  samLastFiltered = filtered.slice();

  var nDone = baseFiltered.filter(function(d){{return d._status==="done";}}).length;
  var nOk   = baseFiltered.filter(function(d){{return d._status==="ok";}}).length;
  var nWarn = baseFiltered.filter(function(d){{return d._status==="warn";}}).length;
  var nCrit = baseFiltered.filter(function(d){{return d._status==="crit";}}).length;

  if(statsEl) statsEl.innerHTML =
    "<div style='display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;font-size:11px;'>" +
    samStatBtn("all", baseFiltered.length+" Fahrer", "#eef2f7", "#64748b", "Alle Fahrer anzeigen") +
    samStatBtn("done", "&#10003; Ziel erreicht: "+nDone, "#dcfce7", "#16a34a", "Nur Fahrer mit erreichtem Jahresziel anzeigen") +
    samStatBtn("ok", "&#8679; Im Soll: "+nOk, "#dbeafe", "#1b66b3", "Nur Fahrer im Soll anzeigen") +
    samStatBtn("warn", "&#9888; Leicht hinter: "+nWarn, "#fef3c7", "#d97706", "Nur Fahrer leicht hinter Soll anzeigen") +
    samStatBtn("crit", "&#8595; Rückstand: "+nCrit, "#fee2e2", "#dc2626", "Nur Fahrer mit Rückstand anzeigen") +
    "<span style='margin-left:auto;color:#64748b;font-size:10px;'>"+sollText+": <b style='color:#1b66b3;'>"+soll+"</b> / "+SAM_ZIEL+
      " &nbsp;("+satElapsed+" von "+satTotal+" Samstagen)</span></div>";

  if(samViewMode === "charts") {{
    samRenderCharts(filtered, selectedYear, soll, satTotal, satElapsed);
    return;
  }}

  var statusCfg = {{
    done: {{border:"#16a34a",bg:"#f0fdf4",badge:"#16a34a",icon:"✓",label:"Jahresziel erreicht"}},
    ok:   {{border:"#1b66b3",bg:"#eff6ff",badge:"#1b66b3",icon:"↑",label:"Im Soll"}},
    warn: {{border:"#d97706",bg:"#fffbeb",badge:"#d97706",icon:"⚠",label:"Leicht hinter Soll"}},
    crit: {{border:"#dc2626",bg:"#fff1f2",badge:"#dc2626",icon:"↓",label:"Rückstand"}}
  }};

  var html = "<div style='display:flex;flex-direction:column;gap:8px;'>";
  filtered.forEach(function(d, idx) {{
    var cfg = statusCfg[d._status] || statusCfg.crit;
    var einsaetzeThisYear = d._selectedCount;
    var pct = Math.min(100, Math.round(einsaetzeThisYear / SAM_ZIEL * 100));
    var sollPct = Math.min(100, Math.round(soll / SAM_ZIEL * 100));

    var sortedDaten = (d._byYear[String(selectedYear)]||[]).slice().sort(function(a,b){{
      return String(a.iso||a.datum||"").localeCompare(String(b.iso||b.datum||""));
    }});

    var datesHtml = sortedDaten.length ? sortedDaten.map(function(e) {{
      var infoTxt = String(e.tour||"").trim();
      var startTxt = String(e.beginn||"").trim();
      var infoHtml = infoTxt ? " <b style='color:#1b66b3;'>"+samEsc(infoTxt)+"</b>" : "";
      if(startTxt) infoHtml += " <span style='color:#64748b;'>"+samEsc(startTxt)+" Uhr</span>";
      return "<span style='display:inline-flex;align-items:center;gap:5px;background:#fff;border:1px solid #d8dee8;border-radius:5px;padding:4px 8px;margin:3px;font-size:10.5px;color:#334155;'>" +
        "<b style='color:"+cfg.badge+";'>"+samEsc(e.tag||"Sa")+"</b> "+samEsc(e.datum||"")+infoHtml+"</span>";
    }}).join("") : "<span style='display:inline-block;color:#64748b;font-size:11px;padding:4px 0;'>Keine Einsätze im gewählten Jahr.</span>";

    var otherYears = Object.keys(d._byYear).filter(function(y){{ return y !== String(selectedYear); }}).sort().reverse();
    var prevHtml = "";
    if(otherYears.length) {{
      prevHtml = "<div style='margin-top:7px;display:flex;gap:4px;flex-wrap:wrap;'>";
      otherYears.forEach(function(y) {{
        var n = d._byYear[y].length;
        var metTarget = n >= SAM_ZIEL;
        prevHtml += "<span style='font-size:9px;padding:2px 7px;border-radius:5px;font-weight:800;background:"+
          (metTarget?"#dcfce7":"#fee2e2")+";color:"+(metTarget?"#16a34a":"#dc2626")+";'>"+
          samEsc(y)+": "+n+(metTarget?" ✓":" ✗")+"</span>";
      }});
      prevHtml += "</div>";
    }}

    html +=
      "<div onclick='samToggle(this)' style='background:"+cfg.bg+";border:2px solid "+cfg.border+";border-left-width:8px;border-radius:6px;cursor:pointer;overflow:hidden;box-shadow:0 1px 4px rgba(15,23,42,.06);'>" +
        "<div style='display:grid;grid-template-columns:42px minmax(180px,1fr) 150px 130px 34px;gap:10px;align-items:center;padding:10px 12px;'>" +
          "<div style='font-size:12px;font-weight:900;color:"+cfg.badge+";'>#"+(idx+1)+"</div>" +
          "<div style='min-width:0;'><div style='font-weight:900;font-size:14px;color:#0b1220;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"+samEsc(d.name)+"</div>" +
            "<div style='display:inline-flex;align-items:center;gap:5px;margin-top:4px;background:"+cfg.badge+";color:#fff;border-radius:5px;padding:2px 8px;font-size:10px;font-weight:900;'>"+cfg.icon+" "+cfg.label+"</div></div>" +
          "<div style='min-width:120px;'><div style='height:12px;background:#e4e9f0;border-radius:999px;position:relative;overflow:hidden;'>" +
            "<div style='position:absolute;left:0;top:0;height:100%;width:"+pct+"%;background:"+cfg.badge+";border-radius:999px;'></div>" +
            "<div style='position:absolute;left:"+sollPct+"%;top:0;width:2px;height:100%;background:#334155;opacity:.45;'></div></div>" +
            "<div style='margin-top:3px;font-size:9px;color:#64748b;font-weight:700;'>"+sollText+": "+soll+" / Ziel: "+SAM_ZIEL+"</div></div>" +
          "<div style='text-align:right;'><div style='font-size:26px;font-weight:950;color:"+cfg.badge+";line-height:1;'>"+einsaetzeThisYear+"</div>" +
            "<div style='font-size:9px;color:#64748b;font-weight:800;'>Einsätze</div></div>" +
          "<div class='sam-arrow' style='font-size:18px;font-weight:900;color:"+cfg.badge+";text-align:right;'>⌄</div></div>" +
        "<div class='sam-dates' style='display:none;background:rgba(255,255,255,.72);border-top:1px solid rgba(148,163,184,.35);padding:9px 12px 10px 64px;'>" +
          "<div style='font-size:10px;text-transform:uppercase;letter-spacing:.35px;font-weight:900;color:#64748b;margin-bottom:5px;'>Einsätze im Jahr "+samEsc(selectedYear)+"</div>" +
          datesHtml + prevHtml + "</div></div>";
  }});

  if(!filtered.length) {{
    html += "<div style='background:#fff;border:1px solid #cbd5e1;border-radius:6px;padding:28px;text-align:center;color:#64748b;font-size:13px;'>Keine Fahrer für diesen Filter.</div>";
  }}
  html += "</div>";
  content.innerHTML = html;
}}

function samExportExcel() {{
  if(typeof XLSX === "undefined") {{
    alert("Excel-Bibliothek nicht geladen. Bitte die Seite neu laden.");
    return;
  }}

  var year = String(samYearFilter || new Date().getFullYear());
  var rows = [];
  (samLastFiltered || []).forEach(function(driver) {{
    var entries = ((driver._byYear || {{}})[year] || []).slice();
    entries.forEach(function(e) {{
      var datumText = String(e.datum || "");
      var dateMatch = datumText.match(/\\d{{2}}\\.\\d{{2}}\\.\\d{{4}}/);
      var kwMatch = datumText.match(/KW\\s*(\\d{{1,2}})/i);
      var tag = String(e.tag || "Sa");
      var art = tag === "So" ? "Sonntag bis 15 Uhr" :
                (tag.indexOf("Fr") === 0 ? "Freitag ab 18 Uhr" : "Samstag");
      var lkw = String(e.tour || "").replace(/^LKW\\s*/i, "").trim();
      rows.push({{
        _iso: String(e.iso || ""),
        Datum: dateMatch ? dateMatch[0] : datumText,
        KW: kwMatch ? parseInt(kwMatch[1], 10) : "",
        Einsatzart: art,
        Fahrer: String(driver.name || ""),
        Beginn: String(e.beginn || ""),
        LKW: lkw
      }});
    }});
  }});

  rows.sort(function(a,b) {{
    var d = String(a._iso).localeCompare(String(b._iso));
    return d !== 0 ? d : String(a.Fahrer).localeCompare(String(b.Fahrer), "de");
  }});

  if(!rows.length) {{
    alert("Für die aktuelle Auswahl sind keine Einsätze vorhanden.");
    return;
  }}

  var headers = ["Datum", "KW", "Einsatzart", "Fahrer", "Beginn", "LKW"];
  var data = [headers].concat(rows.map(function(r) {{
    return [r.Datum, r.KW, r.Einsatzart, r.Fahrer, r.Beginn, r.LKW];
  }}));
  var ws = XLSX.utils.aoa_to_sheet(data);
  ws["!cols"] = [
    {{wch:12}}, {{wch:6}}, {{wch:21}}, {{wch:28}}, {{wch:14}}, {{wch:18}}
  ];
  ws["!autofilter"] = {{ref:"A1:F" + data.length}};

  headers.forEach(function(_, idx) {{
    var ref = XLSX.utils.encode_cell({{r:0,c:idx}});
    if(ws[ref]) ws[ref].s = {{
      font: {{bold:true,color:{{rgb:"FFFFFF"}}}},
      fill: {{fgColor:{{rgb:"1B66B3"}}}},
      alignment: {{horizontal:"center",vertical:"center"}}
    }};
  }});

  for(var r=1; r<data.length; r++) {{
    var kwRef = XLSX.utils.encode_cell({{r:r,c:1}});
    if(ws[kwRef]) ws[kwRef].s = {{alignment:{{horizontal:"center"}}}};
  }}

  var wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Samstagseinsätze");
  var suffix = samStatusFilter !== "all" ? "_" + samStatusFilter : "";
  XLSX.writeFile(wb, "Samstagseinsaetze_" + year + suffix + ".xlsx");
}}

function samRenderCharts(drivers, selectedYear, soll, satTotal, satElapsed) {{
  if(samChartMode === "drivers") return samRenderDriverBars(drivers, selectedYear, soll);
  if(samChartMode === "months") return samRenderMonths(drivers, selectedYear);
  return samRenderMatrix(drivers, selectedYear, satTotal, satElapsed);
}}

function samLegend() {{
  return "<div style='display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:10px;font-weight:800;color:#475569;'>"+
    "<span><i style='display:inline-block;width:11px;height:11px;border-radius:3px;background:#7c3aed;margin-right:4px;vertical-align:-1px;'></i>Fr→Sa</span>"+
    "<span><i style='display:inline-block;width:11px;height:11px;border-radius:3px;background:#1b66b3;margin-right:4px;vertical-align:-1px;'></i>Samstag</span>"+
    "<span><i style='display:inline-block;width:11px;height:11px;border-radius:3px;background:#d97706;margin-right:4px;vertical-align:-1px;'></i>Sonntag</span>"+
    "<span><i style='display:inline-block;width:11px;height:11px;border-radius:3px;background:#0f766e;margin-right:4px;vertical-align:-1px;'></i>mehrere Einsätze</span>"+
    "</div>";
}}

// Klickbare Arbeitstage in der Einsatzmatrix
var samWorkdayPopupData = {{}};
var samWorkdayBodyOverflow = "";

function samWorkdayTypeLabel(tag) {{
  tag = String(tag || "Sa");
  if(tag === "So") return "Sonntag bis 15 Uhr";
  if(tag.indexOf("Fr") === 0) return "Freitag ab 18 Uhr";
  return "Samstag";
}}

function samWorkdayBadgeColor(tag) {{
  tag = String(tag || "Sa");
  if(tag === "So") return "#d97706";
  if(tag.indexOf("Fr") === 0) return "#7c3aed";
  return "#1b66b3";
}}

function samCloseWorkdayPopup() {{
  var overlay = document.getElementById("sam-workday-popup");
  if(overlay) overlay.style.display = "none";
  document.body.style.overflow = samWorkdayBodyOverflow || "";
}}

function samOpenWorkdayPopup(key) {{
  var data = samWorkdayPopupData[key];
  if(!data) return;

  var overlay = document.getElementById("sam-workday-popup");
  if(!overlay) {{
    overlay = document.createElement("div");
    overlay.id = "sam-workday-popup";
    overlay.setAttribute("role", "presentation");
    overlay.onclick = function(ev) {{
      if(ev.target === overlay) samCloseWorkdayPopup();
    }};
    document.body.appendChild(overlay);
  }}

  var entries = Array.isArray(data.entries) ? data.entries : [];
  var cards = entries.map(function(e, idx) {{
    var tag = String(e.tag || "Sa");
    var badgeColor = samWorkdayBadgeColor(tag);
    var tour = String(e.tour || "").trim() || "—";
    var beginn = String(e.beginn || "").trim() || "—";
    var datum = String(e.datum || "").trim() || "—";
    return "<div style='border:1px solid #d8e0ea;border-left:5px solid "+badgeColor+";border-radius:8px;background:#fff;padding:12px 14px;box-shadow:0 1px 3px rgba(15,23,42,.06);'>" +
      "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;'>" +
        "<span style='display:inline-flex;align-items:center;border-radius:5px;background:"+badgeColor+";color:#fff;padding:4px 9px;font-size:11px;font-weight:900;'>" +
          samEsc(samWorkdayTypeLabel(tag)) + "</span>" +
        (entries.length > 1 ? "<span style='font-size:10px;font-weight:800;color:#64748b;'>Eintrag "+(idx+1)+" von "+entries.length+"</span>" : "") +
      "</div>" +
      "<div style='display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px 12px;font-size:12px;line-height:1.4;'>" +
        "<div style='font-weight:800;color:#64748b;'>Fahrer</div><div style='font-weight:900;color:#0f172a;'>"+samEsc(data.driver)+"</div>" +
        "<div style='font-weight:800;color:#64748b;'>Arbeitstag</div><div style='font-weight:800;color:#0f172a;'>"+samEsc(datum)+"</div>" +
        "<div style='font-weight:800;color:#64748b;'>Beginn</div><div style='font-weight:800;color:#0f172a;'>"+samEsc(beginn)+(beginn !== "—" ? " Uhr" : "")+"</div>" +
        "<div style='font-weight:800;color:#64748b;'>LKW</div><div style='font-weight:900;color:#1b66b3;'>"+samEsc(tour)+"</div>" +
      "</div>" +
    "</div>";
  }}).join("");

  if(!cards) {{
    cards = "<div style='padding:24px;text-align:center;color:#64748b;'>Für diesen Arbeitstag sind keine Detaildaten vorhanden.</div>";
  }}

  overlay.style.cssText = "display:flex;position:fixed;inset:0;z-index:99999;align-items:center;justify-content:center;padding:22px;background:rgba(15,23,42,.58);backdrop-filter:blur(2px);";
  overlay.innerHTML =
    "<div role='dialog' aria-modal='true' aria-label='Arbeitstag Details' onclick='event.stopPropagation()' " +
      "style='width:min(660px,96vw);max-height:88vh;display:flex;flex-direction:column;background:#f8fafc;border:1px solid #cbd5e1;border-radius:12px;box-shadow:0 24px 70px rgba(15,23,42,.35);overflow:hidden;font-family:Segoe UI,Arial,sans-serif;'>" +
      "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:16px 18px;background:linear-gradient(180deg,#eef6ff 0%,#fff 100%);border-bottom:1px solid #dbe3ed;'>" +
        "<div style='min-width:0;'>" +
          "<div style='font-size:18px;font-weight:950;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>Arbeitstag – "+samEsc(data.driver)+"</div>" +
          "<div style='margin-top:4px;font-size:11px;font-weight:750;color:#64748b;'>Wochenende KW "+samEsc(data.kw)+" · Samstag, "+samEsc(data.weekendDate)+"</div>" +
        "</div>" +
        "<button type='button' onclick='samCloseWorkdayPopup()' aria-label='Fenster schließen' " +
          "style='flex:0 0 auto;width:34px;height:34px;border:1px solid #cbd5e1;border-radius:7px;background:#fff;color:#334155;font-size:21px;line-height:1;cursor:pointer;font-weight:700;'>&times;</button>" +
      "</div>" +
      "<div style='overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;'>" + cards + "</div>" +
      "<div style='padding:10px 14px;border-top:1px solid #dbe3ed;background:#fff;display:flex;justify-content:flex-end;'>" +
        "<button type='button' onclick='samCloseWorkdayPopup()' style='padding:8px 16px;border:1px solid #1b66b3;border-radius:6px;background:#1b66b3;color:#fff;font-size:11px;font-weight:900;cursor:pointer;font-family:inherit;'>Schließen</button>" +
      "</div>" +
    "</div>";

  samWorkdayBodyOverflow = document.body.style.overflow || "";
  document.body.style.overflow = "hidden";
}}

document.addEventListener("keydown", function(ev) {{
  if(ev.key === "Escape") samCloseWorkdayPopup();
}});

function samRenderMatrix(drivers, year, satTotal, satElapsed) {{
  var content = document.getElementById("sam-content");
  if(!content) return;
  samWorkdayPopupData = {{}};
  var sats = [];
  var d = new Date(year,0,1);
  while(d.getDay() !== 6) d.setDate(d.getDate()+1);
  while(d.getFullYear() === year) {{ sats.push(new Date(d)); d.setDate(d.getDate()+7); }}

  var html = "<div style='background:#fff;border:1px solid #cbd5e1;border-radius:7px;box-shadow:0 1px 5px rgba(15,23,42,.06);overflow:hidden;'>"+
    "<div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;padding:11px 13px;border-bottom:1px solid #e2e8f0;background:#f8fafc;'>"+
      "<div style='font-size:14px;font-weight:900;color:#0f172a;'>Einsatzmatrix "+samEsc(year)+"</div>"+
      samLegend()+"</div>";

  if(!drivers.length) {{
    content.innerHTML = html + "<div style='padding:30px;text-align:center;color:#64748b;'>Keine Fahrer für diesen Filter.</div></div>";
    return;
  }}

  html += "<div style='overflow-x:auto;overflow-y:hidden;width:100%;max-height:none;'>"+
    "<table style='border-collapse:separate;border-spacing:0;table-layout:fixed;min-width:"+(210+sats.length*39)+"px;width:max-content;font-size:9px;'>"+
    "<thead><tr><th style='position:sticky;left:0;top:0;z-index:5;width:210px;min-width:210px;background:#e2e8f0;border-right:2px solid #94a3b8;border-bottom:1px solid #94a3b8;padding:7px 9px;text-align:left;font-size:10px;color:#334155;'>Fahrer</th>";
  sats.forEach(function(s, idx) {{
    var future = year === new Date().getFullYear() && idx >= satElapsed;
    html += "<th title='Samstag, "+samAttr(samShortDate(s)+year)+"' style='position:sticky;top:0;z-index:3;width:39px;min-width:39px;max-width:39px;background:"+(future?"#f8fafc":"#e2e8f0")+";border-right:1px solid #cbd5e1;border-bottom:1px solid #94a3b8;padding:4px 1px;text-align:center;color:"+(future?"#94a3b8":"#334155")+";'>"+
      "<div style='font-size:8px;font-weight:900;'>KW"+samISOWeek(s)+"</div><div style='font-size:7.5px;'>"+samShortDate(s)+"</div></th>";
  }});
  html += "<th style='position:sticky;right:0;top:0;z-index:5;width:54px;min-width:54px;background:#e2e8f0;border-left:2px solid #94a3b8;border-bottom:1px solid #94a3b8;text-align:center;color:#334155;'>Σ</th></tr></thead><tbody>";

  drivers.slice().sort(function(a,b){{return String(a.name||"").localeCompare(String(b.name||""),"de");}}).forEach(function(driver, rowIdx) {{
    var weekendMap = {{}};
    var entries = driver._byYear[String(year)]||[];
    entries.forEach(function(e) {{
      var sat = samWeekendSaturday(e);
      if(!sat || sat.getFullYear() !== year) return;
      var key = samISODate(sat);
      if(!weekendMap[key]) weekendMap[key] = [];
      weekendMap[key].push(e);
    }});
    var rowBg = rowIdx%2 ? "#f8fafc" : "#fff";
    html += "<tr><td style='position:sticky;left:0;z-index:2;width:210px;max-width:210px;background:"+rowBg+";border-right:2px solid #94a3b8;border-bottom:1px solid #e2e8f0;padding:6px 9px;font-size:10.5px;font-weight:800;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' title='"+samAttr(driver.name)+"'>"+samEsc(driver.name)+"</td>";
    sats.forEach(function(s) {{
      var list = weekendMap[samISODate(s)]||[];
      if(!list.length) {{
        html += "<td style='width:39px;height:30px;background:"+rowBg+";border-right:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;text-align:center;color:#cbd5e1;'>·</td>";
        return;
      }}
      var tags = list.map(function(e){{return String(e.tag||"Sa");}});
      var color = list.length > 1 ? "#0f766e" : (tags[0] === "So" ? "#d97706" : (tags[0].indexOf("Fr") === 0 ? "#7c3aed" : "#1b66b3"));
      var label = list.length > 1 ? String(list.length)+"×" : (tags[0] === "So" ? "So" : (tags[0].indexOf("Fr") === 0 ? "Fr" : "Sa"));
      var title = driver.name + "\\n" + list.map(function(e){{
        return String(e.tag||"Sa")+" · "+String(e.datum||"")+(e.beginn?" · "+e.beginn+" Uhr":"")+(e.tour?" · "+e.tour:"");
      }}).join("\\n");
      var workdayKey = "sam-workday-"+rowIdx+"-"+samISODate(s);
      samWorkdayPopupData[workdayKey] = {{
        driver: String(driver.name || ""),
        weekendDate: samShortDate(s) + year,
        kw: samISOWeek(s),
        entries: list.map(function(e) {{
          return {{
            tag: String(e.tag || "Sa"),
            datum: String(e.datum || ""),
            beginn: String(e.beginn || ""),
            tour: String(e.tour || "")
          }};
        }})
      }};
      html += "<td title='"+samAttr(title)+"\\nKlicken für Details' onclick='samOpenWorkdayPopup(&quot;"+workdayKey+"&quot;);event.stopPropagation();' " +
        "style='width:39px;height:30px;background:"+rowBg+";border-right:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;text-align:center;padding:2px;cursor:pointer;'>"+
        "<span style='display:inline-flex;align-items:center;justify-content:center;width:28px;height:22px;border-radius:4px;background:"+color+";color:#fff;font-size:8px;font-weight:950;box-shadow:0 1px 2px rgba(15,23,42,.15);transition:transform .12s,box-shadow .12s;'>"+label+"</span></td>";
    }});
    html += "<td style='position:sticky;right:0;z-index:2;width:54px;background:"+rowBg+";border-left:2px solid #94a3b8;border-bottom:1px solid #e2e8f0;text-align:center;font-size:12px;font-weight:950;color:#1b66b3;'>"+entries.length+"</td></tr>";
  }});
  html += "</tbody></table></div></div>";
  content.innerHTML = html;
}}

function samRenderDriverBars(drivers, year, soll) {{
  var content = document.getElementById("sam-content");
  if(!content) return;
  if(!drivers.length) {{
    content.innerHTML = "<div style='background:#fff;border:1px solid #cbd5e1;border-radius:7px;padding:30px;text-align:center;color:#64748b;'>Keine Fahrer für diesen Filter.</div>";
    return;
  }}
  var list = drivers.slice().sort(function(a,b){{return b._selectedCount-a._selectedCount || String(a.name).localeCompare(String(b.name),"de");}});
  var maxVal = Math.max.apply(null, list.map(function(d){{return d._selectedCount;}}).concat([SAM_ZIEL,1]));
  var html = "<div style='background:#fff;border:1px solid #cbd5e1;border-radius:7px;padding:13px;box-shadow:0 1px 5px rgba(15,23,42,.06);'>"+
    "<div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:13px;'>"+
    "<div><div style='font-size:14px;font-weight:900;color:#0f172a;'>Einsätze je Fahrer "+samEsc(year)+"</div><div style='font-size:10.5px;color:#64748b;margin-top:2px;'>Gestapelt nach Einsatzart; die dunkle Markierung zeigt das Jahresziel "+SAM_ZIEL+".</div></div>"+samLegend()+"</div>";
  list.forEach(function(d) {{
    var entries = d._byYear[String(year)]||[];
    var fr=0,sa=0,so=0;
    entries.forEach(function(e){{var t=String(e.tag||"Sa"); if(t==="So")so++; else if(t.indexOf("Fr")===0)fr++; else sa++;}});
    var frPct=fr/maxVal*100, saPct=sa/maxVal*100, soPct=so/maxVal*100, zielPct=SAM_ZIEL/maxVal*100;
    html += "<div style='display:grid;grid-template-columns:minmax(170px,260px) minmax(280px,1fr) 52px;gap:10px;align-items:center;padding:7px 4px;border-top:1px solid #eef2f7;'>"+
      "<div style='font-size:11.5px;font-weight:850;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' title='"+samAttr(d.name)+"'>"+samEsc(d.name)+"</div>"+
      "<div><div style='height:18px;background:#eef2f7;border-radius:4px;position:relative;overflow:hidden;display:flex;'>"+
        "<span title='Fr→Sa: "+fr+"' style='height:100%;width:"+frPct+"%;background:#7c3aed;'></span>"+
        "<span title='Samstag: "+sa+"' style='height:100%;width:"+saPct+"%;background:#1b66b3;'></span>"+
        "<span title='Sonntag: "+so+"' style='height:100%;width:"+soPct+"%;background:#d97706;'></span>"+
        "<i style='position:absolute;left:"+zielPct+"%;top:0;width:2px;height:100%;background:#0f172a;opacity:.65;'></i>"+
      "</div><div style='display:flex;gap:8px;margin-top:2px;font-size:8.5px;color:#64748b;font-weight:700;'><span>Fr "+fr+"</span><span>Sa "+sa+"</span><span>So "+so+"</span><span style='margin-left:auto;'>Soll aktuell "+soll+"</span></div></div>"+
      "<div style='font-size:20px;font-weight:950;text-align:right;color:"+(entries.length>=SAM_ZIEL?"#16a34a":"#1b66b3")+";'>"+entries.length+"</div></div>";
  }});
  html += "</div>";
  content.innerHTML = html;
}}

function samRenderMonths(drivers, year) {{
  var content = document.getElementById("sam-content");
  if(!content) return;
  var months = Array.from({{length:12}},function(){{return {{fr:0,sa:0,so:0,total:0,drivers:{{}}}};}});
  drivers.forEach(function(d) {{
    (d._byYear[String(year)]||[]).forEach(function(e) {{
      var dt = samDateFromEntry(e); if(!dt) return;
      var m = months[dt.getMonth()];
      var t = String(e.tag||"Sa");
      if(t === "So") m.so++; else if(t.indexOf("Fr")===0) m.fr++; else m.sa++;
      m.total++;
      m.drivers[d.person_key||d.name] = 1;
    }});
  }});
  var maxVal = Math.max.apply(null, months.map(function(m){{return m.total;}}).concat([1]));
  var names = ["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"];
  var html = "<div style='background:#fff;border:1px solid #cbd5e1;border-radius:7px;padding:13px;box-shadow:0 1px 5px rgba(15,23,42,.06);'>"+
    "<div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px;'>"+
    "<div><div style='font-size:14px;font-weight:900;color:#0f172a;'>Monatsverteilung "+samEsc(year)+"</div><div style='font-size:10.5px;color:#64748b;margin-top:2px;'>Anzahl aller Einsätze der aktuell gefilterten Fahrer pro Monat.</div></div>"+samLegend()+"</div>"+
    "<div style='height:310px;display:flex;align-items:flex-end;gap:8px;border-left:1px solid #cbd5e1;border-bottom:1px solid #cbd5e1;padding:14px 10px 0 10px;background:linear-gradient(to top,#f8fafc,#fff);'>";
  months.forEach(function(m,idx) {{
    var h = Math.round(m.total/maxVal*245);
    var hFr = m.total ? Math.round(h*m.fr/m.total) : 0;
    var hSa = m.total ? Math.round(h*m.sa/m.total) : 0;
    var hSo = Math.max(0,h-hFr-hSa);
    var title = names[idx]+" "+year+": "+m.total+" Einsätze · "+Object.keys(m.drivers).length+" Fahrer · Fr "+m.fr+" · Sa "+m.sa+" · So "+m.so;
    html += "<div title='"+samAttr(title)+"' style='flex:1;min-width:48px;height:285px;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;'>"+
      "<div style='font-size:11px;font-weight:950;color:#0f172a;margin-bottom:4px;'>"+m.total+"</div>"+
      "<div style='width:min(42px,80%);height:"+h+"px;min-height:"+(m.total?4:0)+"px;display:flex;flex-direction:column-reverse;border-radius:5px 5px 0 0;overflow:hidden;box-shadow:"+(m.total?"0 1px 3px rgba(15,23,42,.18)":"none")+";'>"+
        "<span style='height:"+hFr+"px;background:#7c3aed;'></span>"+
        "<span style='height:"+hSa+"px;background:#1b66b3;'></span>"+
        "<span style='height:"+hSo+"px;background:#d97706;'></span></div>"+
      "<div style='font-size:10px;font-weight:850;color:#475569;margin-top:5px;'>"+names[idx]+"</div>"+
      "<div style='font-size:8px;color:#94a3b8;'>"+Object.keys(m.drivers).length+" F.</div></div>";
  }});
  html += "</div></div>";
  content.innerHTML = html;
}}

function samToggle(el) {{
  var dates = el.querySelector(".sam-dates");
  if(!dates) return;
  var open = dates.style.display !== "none";
  dates.style.display = open ? "none" : "block";
  var arrow = el.querySelector(".sam-arrow");
  if(arrow) arrow.textContent = open ? "⌄" : "⌃";
}}


{fa_js_code}

{wa_js_code}

{bus_js_code}

{arzt_js_code}

{knapp_js_code}

{documents_js_code}

// VERSP_ABFAHRT wird aus dem komprimierten Datenblock geladen.
{versp_js_code}

// ── Fahrerauswertung: Schichten-Tab (Tachograph-Daten) ─────────────────────────
(function() {{
  var _faShowDetailOrig = window.faShowDetail;
  if (typeof _faShowDetailOrig !== "function") return;

  var faShiftMonthFilterByDriver = {{}};

  function faHasShifts(name) {{
    return TIMEREC_DATA && TIMEREC_DATA[name] && TIMEREC_DATA[name].length > 0;
  }}

  function faTabBar(driverName, hasShifts, hasUebersicht) {{
    // Immer beide Reiter anzeigen, damit der Umschalter an derselben Stelle bleibt.
    // Fehlende Daten werden nur deaktiviert dargestellt.
    var tabs = [
      ["uebersicht", "Übersicht", !!hasUebersicht, "Keine Übersichtsdaten für diesen Fahrer"],
      ["schichten",  "Schichten",  !!hasShifts,     "Keine Schichten / Tachograph-Daten für diesen Fahrer"]
    ];
    var html = "<div class='fa-tabs' style='display:flex;gap:2px;margin-bottom:14px;border-bottom:1px solid #e2e8f0;'>";
    tabs.forEach(function(t) {{
      var active = faActiveTab === t[0] && t[2];
      var enabled = t[2];
      html += "<button type='button' "
            + (enabled ? "onclick=\\\"faSwitchTab('" + t[0] + "')\\\"" : "disabled title='" + faEsc(t[3]) + "'")
            + " style='padding:9px 18px;border:none;background:none;font-family:inherit;"
            + "cursor:" + (enabled ? "pointer" : "not-allowed") + ";"
            + "font-size:13px;font-weight:" + (active ? "800" : "600") + ";"
            + "opacity:" + (enabled ? "1" : ".42") + ";"
            + "color:" + (active ? "#334155" : "#64748b") + ";"
            + "border-bottom:2px solid " + (active ? "#334155" : "transparent") + ";"
            + "margin-bottom:-1px;'>" + t[1] + "</button>";
    }});
    html += "</div>";
    return html;
  }}

  function faEsc(v) {{
    return String(v == null ? "" : v)
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
  }}

  function _toMin(t) {{
    if (t == null) return 0;
    var s = String(t).trim();
    if (!s || s === "-" || s === "—") return 0;

    var m = s.match(/(-?\\d{{1,4}})\\s*:\\s*(\\d{{1,2}})/);
    if (m) {{
      var h = parseInt(m[1], 10) || 0;
      var mi = parseInt(m[2], 10) || 0;
      return h * 60 + mi;
    }}

    var h2 = 0, m2 = 0;
    var hm = s.match(/(\\d+(?:[\\.,]\\d+)?)\\s*(?:std|stunde|stunden|h)\b/i);
    var mm = s.match(/(\\d+)\\s*(?:min|minute|minuten|m)\b/i);
    if (hm || mm) {{
      if (hm) h2 = parseFloat(hm[1].replace(",", ".")) || 0;
      if (mm) m2 = parseInt(mm[1], 10) || 0;
      return Math.round(h2 * 60 + m2);
    }}

    var dec = parseFloat(s.replace(",", "."));
    if (!isNaN(dec)) return Math.round(dec * 60);
    return 0;
  }}

  function _fmtMin(m) {{
    m = Math.round(Number(m) || 0);
    var neg = m < 0;
    if (neg) m = Math.abs(m);
    var h = Math.floor(m / 60);
    var mm = m % 60;
    return (neg ? "-" : "") + h + ":" + (mm < 10 ? "0" + mm : mm);
  }}

  // ── Download-Cutoff-Erkennung ────────────────────────────────────────────
  // Tachograph-Downloads erzeugen für noch laufende Schichten einen
  // künstlichen Schichtende-Zeitpunkt (= Auslesezeitpunkt).  Wenn ≥10
  // Schichten denselben Ende-Zeitpunkt (Datum+Uhrzeit) teilen, handelt es
  // sich mit Sicherheit um einen solchen Cutoff.
  var _faCutoffSet = null;

  function _faComputeEndDate(s) {{
    var d = s.tag || "";
    if (!s.ende_naechster_tag) return d;
    var m = d.match(/^(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}})$/);
    if (!m) return d;
    var dt = new Date(parseInt(m[3],10), parseInt(m[2],10)-1, parseInt(m[1],10)+1);
    return (dt.getDate()<10?"0":"") + dt.getDate() + "."
         + ((dt.getMonth()+1)<10?"0":"") + (dt.getMonth()+1) + "."
         + dt.getFullYear();
  }}

  function _faGetCutoffSet() {{
    if (_faCutoffSet) return _faCutoffSet;
    var freq = {{}};
    Object.keys(TIMEREC_DATA || {{}}).forEach(function(name) {{
      (TIMEREC_DATA[name] || []).forEach(function(s) {{
        if (!s.ende) return;
        var key = _faComputeEndDate(s) + " " + s.ende;
        freq[key] = (freq[key] || 0) + 1;
      }});
    }});
    _faCutoffSet = {{}};
    Object.keys(freq).forEach(function(k) {{
      if (freq[k] >= 10) _faCutoffSet[k] = true;
    }});
    return _faCutoffSet;
  }}

  function _faIsShiftCutoff(s) {{
    if (!s.ende) return false;
    var key = _faComputeEndDate(s) + " " + s.ende;
    return !!_faGetCutoffSet()[key];
  }}

  function _faIsShiftInvalid(s) {{
    // 1) Noch laufend: Schichtende = Tachograph-Download-Zeitpunkt
    if (_faIsShiftCutoff(s)) return true;
    // 2) Tachograph-Fehler: Schichtdauer > 24 Stunden
    if (_toMin(s.schichtdauer) > 1440) return true;
    return false;
  }}

  function _vacationCreditMin(rows) {{
    return (rows || []).length * 480;
  }}

  function _monthInfo(s) {{
    var MONATE = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
    var m = (s.tag || "").match(/^(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}})$/);
    if (!m) return {{ key: "0000-00", label: "Unbekannt" }};
    return {{ key: m[3] + "-" + m[2], label: (MONATE[parseInt(m[2],10)-1] || m[2]) + " " + m[3] }};
  }}


  function _monthLabelFromKey(key) {{
    var MONATE = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
    var m = String(key || "").match(/^(\\d{{4}})-(\\d{{2}})$/);
    if (!m) return "Unbekannt";
    return (MONATE[parseInt(m[2],10)-1] || m[2]) + " " + m[1];
  }}

  var _faPlanDriverIndex = null;
  // Manuelle Ausnahmezuordnung, falls Schreibweisen trotz Normalisierung nicht eindeutig passen.
  // Links: Name aus Tachograph-CSV, rechts: Name aus Tourenplanung/Fahrerauswertung.
  var FA_NAME_ALIASES = {{
    // Beispiel: "Mustermann, H." : "Mustermann, Hans"
  }};

  function _faNameNorm(value) {{
    var s = String(value == null ? "" : value).toLowerCase();
    try {{ s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, ""); }} catch(e) {{}}
    s = s.replace(/ß/g, "ss").replace(/æ/g, "ae").replace(/œ/g, "oe");
    s = s.replace(/\\([^)]*\\)/g, " ");
    s = s.replace(/[_.\\/,;:|]+/g, " ");
    s = s.replace(/[-–—]+/g, " ");
    s = s.replace(/\\s+/g, " ").trim();
    return s;
  }}

  function _faNameTokens(value) {{
    return _faNameNorm(value).split(" ").filter(function(t) {{
      return t && t !== "fahrer" && t !== "fahrerin" && t !== "herr" && t !== "frau";
    }});
  }}

  function _faNameCombos(value) {{
    var raw = String(value == null ? "" : value);
    var combos = [];
    if (raw.indexOf(",") >= 0) {{
      var parts = raw.split(",");
      var last = _faNameNorm(parts.shift() || "");
      var first = _faNameNorm(parts.join(" ") || "");
      if (last || first) combos.push({{ last:last, first:first }});
    }}
    var t = _faNameTokens(raw);
    if (t.length >= 2) {{
      // Variante Vorname Nachname
      combos.push({{ last:t[t.length-1], first:t.slice(0, -1).join(" ") }});
      // Variante Nachname Vorname
      combos.push({{ last:t[0], first:t.slice(1).join(" ") }});
    }} else if (t.length === 1) {{
      combos.push({{ last:t[0], first:"" }});
    }}
    var seen = {{}};
    return combos.filter(function(c) {{
      var k = c.last + "|" + c.first;
      if (seen[k]) return false;
      seen[k] = true;
      return !!(c.last || c.first);
    }});
  }}

  function _faNameKeys(value, initialOnly) {{
    var out = {{}};
    var norm = _faNameNorm(value);
    if (norm && !initialOnly) out[norm] = true;
    var tokens = _faNameTokens(value);
    if (tokens.length > 1 && !initialOnly) {{
      out[tokens.join(" ")] = true;
      out[tokens.slice().reverse().join(" ")] = true;
    }}
    _faNameCombos(value).forEach(function(c) {{
      if (!c.last) return;
      if (!initialOnly) {{
        out[c.last + "|" + c.first] = true;
        if (c.first) {{
          out[c.last + " " + c.first] = true;
          out[c.first + " " + c.last] = true;
        }}
      }}
      var firstInitial = (c.first || "").charAt(0);
      if (firstInitial) out[c.last + "|" + firstInitial] = true;
    }});
    return Object.keys(out);
  }}

  function _faIndexAdd(bucket, key, driver) {{
    if (!key) return;
    if (!bucket[key]) bucket[key] = [];
    if (bucket[key].indexOf(driver) < 0) bucket[key].push(driver);
  }}

  function _faBuildPlanDriverIndex() {{
    var idx = {{ exact:{{}}, initial:{{}}, last:{{}} }};
    (FA_DATA || []).forEach(function(d) {{
      if (!d || !d.name) return;
      _faNameKeys(d.name, false).forEach(function(k) {{ _faIndexAdd(idx.exact, k, d); }});
      _faNameKeys(d.name, true).forEach(function(k) {{ _faIndexAdd(idx.initial, k, d); }});
      _faNameCombos(d.name).forEach(function(c) {{ _faIndexAdd(idx.last, c.last, d); }});
    }});
    return idx;
  }}

  function _faPickUniqueFromIndex(bucket, keys) {{
    for (var i=0; i<keys.length; i++) {{
      var arr = bucket[keys[i]] || [];
      if (arr.length === 1) return arr[0];
    }}
    return null;
  }}

  function _faPlanDriver(name) {{
    if (!Array.isArray(FA_DATA) || !FA_DATA.length) return null;

    // 1) Direkter Treffer bleibt bevorzugt.
    var direct = FA_DATA.find(function(d) {{ return d && d.name === name; }});
    if (direct) return direct;

    // 1b) Manuelle Ausnahmen, wenn ein Name in den Dateien wirklich unterschiedlich geschrieben ist.
    var alias = FA_NAME_ALIASES[name] || FA_NAME_ALIASES[_faNameNorm(name)] || "";
    if (alias) {{
      var aliasNorm = _faNameNorm(alias);
      var aliasDriver = FA_DATA.find(function(d) {{
        return d && (d.name === alias || _faNameNorm(d.name) === aliasNorm);
      }});
      if (aliasDriver) return aliasDriver;
    }}

    // 2) Robuster Namensabgleich: Komma egal, Reihenfolge egal, Umlaute egal, Bindestriche egal.
    if (!_faPlanDriverIndex) _faPlanDriverIndex = _faBuildPlanDriverIndex();

    var exact = _faPickUniqueFromIndex(_faPlanDriverIndex.exact, _faNameKeys(name, false));
    if (exact) return exact;

    // 3) Fallback: gleicher Nachname + eindeutiger Vorname-Anfangsbuchstabe.
    var initial = _faPickUniqueFromIndex(_faPlanDriverIndex.initial, _faNameKeys(name, true));
    if (initial) return initial;

    // 4) Sehr vorsichtiger Fallback: gleicher Nachname und eindeutiger Kandidat.
    var combos = _faNameCombos(name);
    for (var c=0; c<combos.length; c++) {{
      var candidates = _faPlanDriverIndex.last[combos[c].last] || [];
      if (candidates.length === 1) return candidates[0];
    }}

    return null;
  }}

  function _faPlanDateKey(entry) {{
    var raw = String((entry && entry.datum) || "");
    var m = raw.match(/(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}})/);
    if (!m) return "";
    return m[3] + "-" + m[2] + "-" + m[1];
  }}

  function _faPlanMonthKey(entry) {{
    var dk = _faPlanDateKey(entry);
    return dk ? dk.slice(0, 7) : "";
  }}

  function _faAbsenceEntries(name, yr, monthFilter, kind) {{
    var driver = _faPlanDriver(name);
    if (!driver || !driver.years) return [];
    var needle = String(kind || "krank").toLowerCase();
    var label = needle === "urlaub" ? "Urlaub" : "Krank";
    var years = yr === "all" ? Object.keys(driver.years || {{}}) : [yr];
    var seen = {{}};
    var out = [];
    years.forEach(function(y) {{
      var data = driver.years[y];
      if (!data) return;
      (data.eintraege || []).forEach(function(e) {{
        var tour = String(e.tour || "").toLowerCase();
        if (tour.indexOf(needle) < 0) return;
        var dk = _faPlanDateKey(e);
        var mk = _faPlanMonthKey(e);
        if (!dk || !mk) return;
        if (monthFilter && monthFilter !== "all" && mk !== monthFilter) return;
        if (seen[dk]) return;
        seen[dk] = true;
        out.push({{ dateKey: dk, monthKey: mk, datum: e.datum || dk, tour: e.tour || label }});
      }});
    }});
    out.sort(function(a,b) {{ return a.dateKey.localeCompare(b.dateKey); }});
    return out;
  }}

  function _faSickEntries(name, yr, monthFilter) {{
    return _faAbsenceEntries(name, yr, monthFilter, "krank");
  }}

  function _faVacationEntries(name, yr, monthFilter) {{
    return _faAbsenceEntries(name, yr, monthFilter, "urlaub");
  }}

  function _faAddAbsenceMonths(name, yr, byMonth) {{
    _faSickEntries(name, yr, "all").concat(_faVacationEntries(name, yr, "all")).forEach(function(e) {{
      if (!byMonth[e.monthKey]) byMonth[e.monthKey] = {{ label: _monthLabelFromKey(e.monthKey), shifts: [] }};
    }});
  }}

  function _renderAbsenceBox(rows, cfg) {{
    if (!rows || !rows.length) return "";
    cfg = cfg || {{}};
    var title = cfg.title || "Abwesenheit";
    var border = cfg.border || "#cbd5e1";
    var bg = cfg.bg || "#f8fafc";
    var text = cfg.text || "#334155";
    var badgeBg = cfg.badgeBg || bg;
    var badgeText = cfg.badgeText || text;
    var html = "<details style='background:#fff;border:1px solid " + border + ";border-radius:5px;margin-bottom:10px;overflow:hidden;'>";
    html += "<summary style='cursor:pointer;padding:7px 10px;display:flex;align-items:center;gap:8px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.45px;color:" + text + ";'>"
          + "<span>" + title + "</span>"
          + "<span style='display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:18px;border-radius:4px;background:" + badgeBg + ";border:1px solid " + border + ";color:" + badgeText + ";font-size:10px;font-weight:900;'>" + rows.length + "</span>"
          + "<span style='margin-left:auto;color:#64748b;font-size:10px;font-weight:700;text-transform:none;letter-spacing:0;'>anklicken zum Anzeigen</span>"
          + "</summary>";
    html += "<div style='border-top:1px solid " + border + ";padding:7px 10px;display:grid;grid-template-columns:repeat(auto-fill,132px);gap:4px;max-height:88px;overflow:auto;'>";
    rows.forEach(function(e) {{
      html += "<span style='width:132px;min-height:22px;display:inline-flex;align-items:center;justify-content:center;background:" + bg + ";border:1px solid " + border + ";border-radius:4px;padding:2px 5px;font-size:10.5px;font-weight:800;color:" + text + ";font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + faEsc(e.datum || e.dateKey) + "</span>";
    }});
    html += "</div></details>";
    return html;
  }}

  function _renderSickBox(sickRows) {{
    return _renderAbsenceBox(sickRows, {{ title:"Kranktage", border:"#fecaca", bg:"#fee2e2", text:"#991b1b", badgeBg:"#fee2e2", badgeText:"#991b1b" }});
  }}

  function _renderVacationBox(vacationRows) {{
    return _renderAbsenceBox(vacationRows, {{ title:"Urlaubstage", border:"#bae6fd", bg:"#f1f5f9", text:"#075985", badgeBg:"#f1f5f9", badgeText:"#075985" }});
  }}

  function _renderLkwBox(lkwEntries) {{
    if (!lkwEntries || !lkwEntries.length) return "";
    var total = lkwEntries.reduce(function(sum, e) {{ return sum + (parseInt(e[1], 10) || 0); }}, 0);
    var html = "<details style='background:#fff;border:1px solid #dbe4ef;border-radius:5px;margin-bottom:10px;overflow:hidden;'>";
    html += "<summary style='cursor:pointer;padding:7px 10px;display:flex;align-items:center;gap:8px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.45px;color:#1e3a5f;'>"
          + "<span>LKW in Auswahl</span>"
          + "<span style='display:inline-flex;align-items:center;justify-content:center;min-width:22px;height:18px;border-radius:4px;background:#f1f5f9;border:1px solid #dbe4ef;color:#1e3a5f;font-size:10px;font-weight:900;'>" + lkwEntries.length + "</span>"
          + "<span style='color:#94a3b8;font-size:10px;font-weight:700;text-transform:none;letter-spacing:0;'>" + total + " Einsätze</span>"
          + "<span style='margin-left:auto;color:#64748b;font-size:10px;font-weight:700;text-transform:none;letter-spacing:0;'>anklicken zum Anzeigen</span>"
          + "</summary>";
    html += "<div style='border-top:1px solid #dbe4ef;padding:7px 10px;display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:4px;max-height:92px;overflow:auto;'>";
    lkwEntries.forEach(function(e) {{
      html += "<span style='min-height:22px;display:inline-flex;align-items:center;justify-content:space-between;gap:6px;background:#f8fbff;border:1px solid #dbe4ef;border-radius:4px;padding:2px 7px;font-size:10.5px;font-weight:800;color:#1e3a5f;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;'>"
            + "<span style='overflow:hidden;text-overflow:ellipsis;'>" + faEsc(e[0]) + "</span>"
            + "<span style='font-size:10px;color:#94a3b8;font-weight:700;flex:0 0 auto;'>" + e[1] + "x</span></span>";
    }});
    html += "</div></details>";
    return html;
  }}

  // Freitag ab 18:00 zählt als Samstags-Einsatz
  function _isFrAbend(s) {{
    return s.wochentag === "Fr" && s.beginn && s.beginn >= "18:00";
  }}

  function _summarizeShifts(shifts) {{
    var out = {{ count: shifts.length, dauer: 0, netto: 0, over10: 0, samstage: 0, sonntage: 0, lkwSet: {{}} }};
    shifts.forEach(function(s) {{
      var invalid = _faIsShiftInvalid(s);
      if (!invalid) {{
        var netto = _toMin(s.profil);
        out.dauer += _toMin(s.schichtdauer);
        out.netto += netto;
        if (netto > 600) out.over10 += 1;
      }}
      if (s.wochentag === "Sa" || _isFrAbend(s)) out.samstage += 1;
      if (s.wochentag === "So") out.sonntage += 1;
      (s.lkw || "").split(",").forEach(function(l) {{
        l = l.trim();
        if (l) out.lkwSet[l] = (out.lkwSet[l] || 0) + 1;
      }});
    }});
    return out;
  }}

  function _printShiftStyles() {{
    return "<style>"
      + "@page{{size:A4 landscape;margin:9mm;}}"
      + "*{{box-sizing:border-box;}}body{{font-family:Arial,Helvetica,sans-serif;margin:0;color:#111827;font-size:11px;}}"
      + "h1{{font-size:20px;margin:0 0 2px 0;}}.sub{{font-size:11px;color:#475569;margin-bottom:10px;}}"
      + ".cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:10px 0;}}"
      + ".card{{border:1px solid #cbd5e1;border-radius:4px;padding:7px 8px;break-inside:avoid;}}"
      + ".label{{font-size:9px;color:#64748b;text-transform:uppercase;font-weight:700;letter-spacing:.4px;}}"
      + ".value{{font-size:17px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.15;margin-top:2px;}}"
      + "table{{width:100%;border-collapse:collapse;font-size:10.5px;}}th,td{{border:1px solid #cbd5e1;padding:4px 5px;text-align:left;vertical-align:top;}}"
      + "th{{background:#f1f5f9;font-size:9px;text-transform:uppercase;letter-spacing:.3px;}}.num{{text-align:right;font-variant-numeric:tabular-nums;}}.over{{font-weight:800;color:#991b1b;}}"
      + ".month-block{{break-inside:avoid;margin-top:10px;border:1px solid #cbd5e1;border-radius:5px;overflow:hidden;}}"
      + ".month-head{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:#f8fbff;border-bottom:1px solid #cbd5e1;padding:6px 8px;}}"
      + ".month-title{{font-size:12px;font-weight:900;text-transform:uppercase;letter-spacing:.45px;color:#1e3a5f;min-width:110px;}}"
      + ".month-meta{{font-size:9.5px;color:#475569;font-weight:700;display:flex;gap:8px;flex-wrap:wrap;}}"
      + ".mini{{border-bottom:1px solid #e2e8f0;padding:5px 8px;font-size:9.5px;line-height:1.35;}}"
      + ".chip{{display:inline-block;border:1px solid #cbd5e1;border-radius:3px;padding:1px 4px;margin:1px 2px;font-weight:700;white-space:nowrap;}}"
      + ".empty{{padding:7px 8px;color:#94a3b8;font-weight:700;text-align:center;}}"
      + "</style>";
  }}

  function _renderShiftRows(rows) {{
    var html = "";
    rows.forEach(function(s, i) {{
      var netto = _toMin(s.profil);
      var invalid = _faIsShiftInvalid(s);
      var over = !invalid && netto > 600;
      var weekend = s.wochentag === "Sa" || s.wochentag === "So" || _isFrAbend(s);
      var rowBg = invalid ? "#f9fafb" : (over ? "#fff1f2" : (weekend ? "#fff7ed" : (i % 2 === 0 ? "#fff" : "#fafbfc")));
      var tagColor = invalid ? "#b0b8c4" : (weekend ? (s.wochentag === "So" ? "#dc2626" : "#b45309") : "#0f172a");
      var cellDim = invalid ? "color:#b0b8c4;text-decoration:line-through;" : "";
      var endeStr = faEsc(s.ende || "");
      if (endeStr && s.ende_naechster_tag) endeStr += " <span style='color:#94a3b8;font-size:9.5px;font-weight:600;'>+1</span>";
      var tagDisplay = (s.wochentag ? "<span style='display:inline-block;width:22px;color:" + (invalid ? "#cbd5e1" : "#94a3b8") + ";font-weight:700;font-size:10.5px;'>" + faEsc(s.wochentag) + "</span> " : "") + faEsc(s.tag);
      var cutoffLabel = "";
      if (invalid) {{
        var reason = _faIsShiftCutoff(s) ? "noch laufend" : "Tachofehler";
        cutoffLabel = " <span style='font-size:8.5px;font-weight:800;background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:3px;padding:0 4px;vertical-align:middle;text-decoration:none;display:inline-block;'>" + reason + "</span>";
      }}

      html += "<tr style='background:" + rowBg + ";border-bottom:1px solid #f1f5f9;" + (invalid ? "opacity:.7;" : "") + "'>";
      html += "<td style='padding:5px 8px;color:" + tagColor + ";font-weight:" + (weekend ? "700" : "600") + ";font-variant-numeric:tabular-nums;white-space:nowrap;'>" + tagDisplay + cutoffLabel + "</td>";
      html += "<td style='padding:5px 6px;font-variant-numeric:tabular-nums;white-space:nowrap;" + (invalid ? cellDim : "color:#475569;") + "'>" + faEsc(s.beginn || "") + "</td>";
      html += "<td style='padding:5px 6px;font-variant-numeric:tabular-nums;white-space:nowrap;" + (invalid ? cellDim : "color:#475569;") + "'>" + endeStr + "</td>";
      html += "<td style='padding:5px 7px;text-align:right;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;" + (invalid ? cellDim : "color:#1e3a5f;") + "'>" + faEsc(s.schichtdauer || "") + "</td>";
      html += "<td style='padding:5px 7px;text-align:right;font-weight:" + (over ? "900" : "700") + ";font-variant-numeric:tabular-nums;white-space:nowrap;" + (invalid ? cellDim : ("color:" + (over ? "#be123c" : "#0f172a") + ";")) + "'>" + faEsc(s.profil || "") + "</td>";
      html += "<td style='padding:5px 8px;font-weight:600;font-size:11px;white-space:normal;" + (invalid ? cellDim : "color:#166534;") + "'>" + faEsc(s.lkw || "") + "</td>";
      html += "</tr>";
    }});
    return html;
  }}

  function _renderShiftTable(rows) {{
    var thBase = "padding:6px 8px;font-weight:800;font-size:9.5px;text-transform:uppercase;letter-spacing:.35px;border-bottom:1px solid #e2e8f0;white-space:nowrap;";
    var html = "<table style='width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed;'>";
    html += "<colgroup>"
          + "<col style='width:128px;'>"
          + "<col style='width:68px;'>"
          + "<col style='width:68px;'>"
          + "<col style='width:94px;'>"
          + "<col style='width:108px;'>"
          + "<col>"
          + "</colgroup>";
    html += "<thead><tr style='background:#fafbfc;color:#64748b;'>"
          + "<th style='" + thBase + "text-align:left;'>Tag</th>"
          + "<th style='" + thBase + "text-align:left;'>Beginn</th>"
          + "<th style='" + thBase + "text-align:left;'>Ende</th>"
          + "<th style='" + thBase + "text-align:right;'>Schichtdauer</th>"
          + "<th style='" + thBase + "text-align:right;'>Netto</th>"
          + "<th style='" + thBase + "text-align:left;'>LKW</th>"
          + "</tr></thead><tbody>";
    html += _renderShiftRows(rows);
    html += "</tbody></table>";
    return html;
  }}

  function faRenderShifts(name, panel) {{
    var all = (TIMEREC_DATA[name] || []).slice();
    var yr = faYearFilter;
    var yearShifts = (yr === "all") ? all : all.filter(function(s) {{
      var m = (s.tag || "").match(/(\\d{{4}})$/);
      return m && m[1] === yr;
    }});

    var byMonth = {{}};
    yearShifts.forEach(function(s) {{
      var mi = _monthInfo(s);
      if (!byMonth[mi.key]) byMonth[mi.key] = {{ label: mi.label, shifts: [] }};
      byMonth[mi.key].shifts.push(s);
    }});
    _faAddAbsenceMonths(name, yr, byMonth);
    var monthKeys = Object.keys(byMonth).sort(function(a,b) {{ return String(b).localeCompare(String(a)); }});

    // Standardansicht beim Öffnen: Jahresübersicht / alle Monate.
    // Nur wenn der Nutzer bewusst einen Monat gewählt hat und dieser Monat nicht mehr existiert,
    // wird wieder auf die Jahresübersicht zurückgestellt.
    if (!faShiftMonthFilterByDriver[name]) {{
      faShiftMonthFilterByDriver[name] = "all";
    }} else if (faShiftMonthFilterByDriver[name] !== "all" && !byMonth[faShiftMonthFilterByDriver[name]]) {{
      faShiftMonthFilterByDriver[name] = "all";
    }}
    var monthFilter = faShiftMonthFilterByDriver[name] || "all";
    var shifts = monthFilter === "all" ? yearShifts : ((byMonth[monthFilter] && byMonth[monthFilter].shifts) ? byMonth[monthFilter].shifts : []);
    var selectedLabel = monthFilter === "all" ? "Alle Monate" : (byMonth[monthFilter] ? byMonth[monthFilter].label : "Monat");
    var stats = _summarizeShifts(shifts);
    var sickRows = _faSickEntries(name, yr, monthFilter);
    var vacationRows = _faVacationEntries(name, yr, monthFilter);
    var sickCount = sickRows.length;
    var vacationCount = vacationRows.length;
    var vacationCredit = _vacationCreditMin(vacationRows);
    var nettoWithVacation = stats.netto + vacationCredit;

    var html = "";

    html += "<div style='background:#fff;border:1.5px solid #e2e8f0;border-radius:5px;padding:14px 18px;margin-bottom:12px;'>"
          + "<div style='display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;'>"
          + "<div>"
          + "<div style='font-size:20px;font-weight:900;color:#0b1220;'>" + faEsc(name) + "</div>"
          + "<div style='font-size:11.5px;color:#64748b;font-weight:700;margin-top:3px;'>Schichten / Tachograph · Netto-Arbeitszeit aus Arbeitszeitprofil · Urlaub +8:00 je Tag</div>"
          + "</div>"
          + "<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;'>"
          + "<label style='font-size:10.5px;color:#64748b;font-weight:800;text-transform:uppercase;letter-spacing:.5px;'>Monat</label>"
          + "<select onchange=\\\"faSetShiftMonth(this.value)\\\" style='padding:7px 10px;border:1px solid #cbd5e1;border-radius:5px;background:#fff;font-size:12px;font-weight:700;color:#0f172a;'>";
    html += "<option value='all'" + (monthFilter === "all" ? " selected" : "") + ">Alle Monate</option>";
    monthKeys.forEach(function(mk) {{
      html += "<option value='" + faEsc(mk) + "'" + (monthFilter === mk ? " selected" : "") + ">" + faEsc(byMonth[mk].label) + "</option>";
    }});
    html += "</select>"
          + "<button type='button' onclick='faPrintShiftMonth()' title='Aktuelle Monatsauswahl drucken' style='padding:8px 12px;border:1px solid #dc2626;border-radius:5px;background:#dc2626;color:#fff;font-size:12px;font-weight:900;cursor:pointer;font-family:inherit;white-space:nowrap;'>PDF Druck</button>"
          + "</div></div></div>";

    if (!yearShifts.length && !sickCount && !vacationCount) {{
      html += "<div style='color:#94a3b8;padding:40px;text-align:center;font-size:14px;background:#fff;border:1px solid #e2e8f0;border-radius:5px;'>"
            + "Keine Schichten, Kranktage oder Urlaubstage in diesem Zeitraum.</div>";
      panel.innerHTML = html;
      panel.scrollTop = 0;
      return;
    }}

    html += "<div style='background:#fff;border:1px solid #e2e8f0;border-radius:5px;padding:9px 12px;margin-bottom:10px;display:flex;gap:16px;flex-wrap:wrap;'>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Auswahl</div>"
          + "<div style='font-size:17px;font-weight:900;color:#0f172a;line-height:1.25;'>" + faEsc(selectedLabel) + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Schichten</div>"
          + "<div style='font-size:17px;font-weight:800;color:#0f172a;font-variant-numeric:tabular-nums;line-height:1.15;'>" + stats.count + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Kranktage</div>"
          + "<div style='font-size:17px;font-weight:900;color:" + (sickCount ? "#be123c" : "#166534") + ";font-variant-numeric:tabular-nums;line-height:1.15;'>" + sickCount + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Urlaubstage</div>"
          + "<div style='font-size:17px;font-weight:900;color:" + (vacationCount ? "#075985" : "#166534") + ";font-variant-numeric:tabular-nums;line-height:1.15;'>" + vacationCount + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Σ Netto Schichten</div>"
          + "<div style='font-size:17px;font-weight:900;color:#1e3a5f;font-variant-numeric:tabular-nums;line-height:1.15;'>" + _fmtMin(stats.netto) + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Urlaub +8h</div>"
          + "<div style='font-size:17px;font-weight:900;color:" + (vacationCredit ? "#075985" : "#166534") + ";font-variant-numeric:tabular-nums;line-height:1.15;'>" + _fmtMin(vacationCredit) + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Σ Netto inkl. Urlaub</div>"
          + "<div style='font-size:17px;font-weight:900;color:#0f172a;font-variant-numeric:tabular-nums;line-height:1.15;'>" + _fmtMin(nettoWithVacation) + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Schichten &gt; 10:00 Netto</div>"
          + "<div style='font-size:17px;font-weight:900;color:" + (stats.over10 ? "#be123c" : "#166534") + ";font-variant-numeric:tabular-nums;line-height:1.15;'>" + stats.over10 + "</div></div>";
    html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Σ Schichtdauer</div>"
          + "<div style='font-size:17px;font-weight:800;color:#475569;font-variant-numeric:tabular-nums;line-height:1.15;'>" + _fmtMin(stats.dauer) + "</div></div>";
    if (stats.samstage) html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Samstage</div>"
          + "<div style='font-size:17px;font-weight:800;color:#b45309;font-variant-numeric:tabular-nums;line-height:1.15;'>" + stats.samstage + "</div></div>";
    if (stats.sonntage) html += "<div><div style='font-size:9.5px;color:#64748b;text-transform:uppercase;letter-spacing:.45px;font-weight:700;'>Sonntage</div>"
          + "<div style='font-size:17px;font-weight:800;color:#dc2626;font-variant-numeric:tabular-nums;line-height:1.15;'>" + stats.sonntage + "</div></div>";
    html += "</div>";
    html += _renderSickBox(sickRows);
    html += _renderVacationBox(vacationRows);

    var lkwEntries = Object.keys(stats.lkwSet).map(function(k){{return [k, stats.lkwSet[k]];}}).sort(function(a,b){{return b[1]-a[1];}});
    html += _renderLkwBox(lkwEntries);

    if (monthFilter !== "all") {{
      html += "<div style='background:#fff;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;margin-bottom:12px;'>";
      html += "<div style='padding:9px 14px;background:#f8fbff;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:14px;flex-wrap:wrap;'>";
      html += "<span style='font-size:12px;font-weight:800;color:#1e3a5f;text-transform:uppercase;letter-spacing:.6px;'>" + faEsc(selectedLabel) + "</span>";
      html += "<span style='font-size:11px;color:#94a3b8;font-weight:600;'>" + stats.count + " Schichten" + (sickCount ? " · Krank " + sickCount : "") + (vacationCount ? " · Urlaub " + vacationCount : "") + "</span>";
      html += "<span style='margin-left:auto;font-size:11px;color:#64748b;font-weight:600;'>Σ Netto <b style='color:#1e3a5f;font-variant-numeric:tabular-nums;'>" + _fmtMin(stats.netto) + "</b> &nbsp;·&nbsp; Urlaub +8h <b style='color:#075985;font-variant-numeric:tabular-nums;'>" + _fmtMin(vacationCredit) + "</b> &nbsp;·&nbsp; Σ inkl. Urlaub <b style='color:#0f172a;font-variant-numeric:tabular-nums;'>" + _fmtMin(nettoWithVacation) + "</b> &nbsp;·&nbsp; &gt; 10:00 Netto <b style='color:#be123c;font-variant-numeric:tabular-nums;'>" + stats.over10 + "</b></span>";
      html += "</div>" + _renderShiftTable(shifts) + "</div>";
    }} else {{
      monthKeys.forEach(function(mk) {{
        var grp = byMonth[mk];
        var gs = _summarizeShifts(grp.shifts);
        var gsSick = _faSickEntries(name, yr, mk).length;
        var gsVacation = _faVacationEntries(name, yr, mk).length;
        var gsVacationCredit = gsVacation * 480;
        var gsNettoWithVacation = gs.netto + gsVacationCredit;
        html += "<div style='background:#fff;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;margin-bottom:12px;'>";
        html += "<div style='padding:9px 14px;background:#f8fbff;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:14px;flex-wrap:wrap;'>";
        html += "<span style='font-size:12px;font-weight:800;color:#1e3a5f;text-transform:uppercase;letter-spacing:.6px;'>" + faEsc(grp.label) + "</span>";
        html += "<span style='font-size:11px;color:#94a3b8;font-weight:600;'>" + gs.count + " Schichten" + (gsSick ? " · Krank " + gsSick : "") + (gsVacation ? " · Urlaub " + gsVacation : "") + "</span>";
        html += "<span style='margin-left:auto;font-size:11px;color:#64748b;font-weight:600;'>Σ Netto <b style='color:#1e3a5f;font-variant-numeric:tabular-nums;'>" + _fmtMin(gs.netto) + "</b> &nbsp;·&nbsp; Urlaub +8h <b style='color:#075985;font-variant-numeric:tabular-nums;'>" + _fmtMin(gsVacationCredit) + "</b> &nbsp;·&nbsp; Σ inkl. Urlaub <b style='color:#0f172a;font-variant-numeric:tabular-nums;'>" + _fmtMin(gsNettoWithVacation) + "</b> &nbsp;·&nbsp; &gt; 10:00 Netto <b style='color:#be123c;font-variant-numeric:tabular-nums;'>" + gs.over10 + "</b></span>";
        html += "</div>" + _renderShiftTable(grp.shifts) + "</div>";
      }});
    }}

    panel.innerHTML = html;
    panel.scrollTop = 0;
  }}

  window.faSetShiftMonth = function(value) {{
    if (!faSelectedName) return;
    faShiftMonthFilterByDriver[faSelectedName] = value || "all";
    window.faShowDetail(faSelectedName);
  }};

  window.faPrintShiftMonth = function() {{
    var name = faSelectedName;
    if (!name) return;

    var all = (TIMEREC_DATA[name] || []).slice();
    var yr = faYearFilter;
    var yearShifts = (yr === "all") ? all : all.filter(function(s) {{
      var m = (s.tag || "").match(/(\\d{{4}})$/);
      return m && m[1] === yr;
    }});

    var byMonth = {{}};
    yearShifts.forEach(function(s) {{
      var mi = _monthInfo(s);
      if (!byMonth[mi.key]) byMonth[mi.key] = {{ label: mi.label, shifts: [] }};
      byMonth[mi.key].shifts.push(s);
    }});
    _faAddAbsenceMonths(name, yr, byMonth);

    var monthKeys = Object.keys(byMonth).sort(function(a,b) {{ return String(b).localeCompare(String(a)); }});
    var monthFilter = faShiftMonthFilterByDriver[name] || "all";
    if (monthFilter !== "all" && !byMonth[monthFilter]) monthFilter = "all";

    var shifts = monthFilter === "all" ? yearShifts : ((byMonth[monthFilter] && byMonth[monthFilter].shifts) ? byMonth[monthFilter].shifts : []);
    var label = monthFilter === "all" ? "Alle Monate" : (byMonth[monthFilter] ? byMonth[monthFilter].label : "Monat");
    var stats = _summarizeShifts(shifts);
    var sickRows = _faSickEntries(name, yr, monthFilter);
    var vacationRows = _faVacationEntries(name, yr, monthFilter);
    var vacationCredit = _vacationCreditMin(vacationRows);
    var nettoWithVacation = stats.netto + vacationCredit;
    var lkwEntries = Object.keys(stats.lkwSet || {{}}).map(function(k) {{ return [k, stats.lkwSet[k]]; }}).sort(function(a,b) {{ return b[1]-a[1]; }});
    var printedAt = new Date().toLocaleString("de-DE");

    function _printRows(rowList) {{
      var out = "";
      rowList.forEach(function(s) {{
        var netto = _toMin(s.profil);
        var invalid = _faIsShiftInvalid(s);
        var cls = invalid ? "invalid" : (netto > 600 ? "over" : "");
        out += "<tr" + (invalid ? " style='opacity:.5;'" : "") + ">"
           + "<td>" + faEsc((s.wochentag || "") + " " + (s.tag || "")) + (invalid ? " <span style='font-size:5pt;color:#92400e;font-weight:800;'>" + (_faIsShiftCutoff(s) ? "laufend" : "Fehler") + "</span>" : "") + "</td>"
           + "<td>" + faEsc(s.beginn || "") + "</td>"
           + "<td>" + faEsc(s.ende || "") + (s.ende_naechster_tag ? " +1" : "") + "</td>"
           + "<td class='num'" + (invalid ? " style='text-decoration:line-through;'" : "") + ">" + faEsc(s.schichtdauer || "") + "</td>"
           + "<td class='num " + cls + "'" + (invalid ? " style='text-decoration:line-through;'" : "") + ">" + faEsc(s.profil || "") + "</td>"
           + "<td>" + faEsc(s.lkw || "") + "</td>"
           + "</tr>";
      }});
      if (!out) out = "<tr><td colspan='6' class='empty'>Keine Schichten in diesem Monat</td></tr>";
      return out;
    }}

    function _printAbsenceLine(title, rows, styleAttr, suffixFn) {{
      if (!rows || !rows.length) return "";
      suffixFn = suffixFn || function() {{ return ""; }};
      return "<div class='mini' style='" + styleAttr + "'><b>" + title + ":</b> "
        + rows.map(function(e) {{ return faEsc(e.datum || e.dateKey) + suffixFn(e); }}).join(", ")
        + "</div>";
    }}

    function _printLkwLine(entries) {{
      if (!entries || !entries.length) return "";
      return "<div class='mini'><b>LKW:</b> "
        + entries.map(function(e) {{
            return "<span class='chip'>" + faEsc(e[0]) + " <span style='color:#64748b;'>" + e[1] + "x</span></span>";
          }}).join(" ")
        + "</div>";
    }}

    function _printMonthSection(mk, monthLabel, rowList) {{
      rowList = rowList || [];
      var ms = _summarizeShifts(rowList);
      var mSick = _faSickEntries(name, yr, mk);
      var mVacation = _faVacationEntries(name, yr, mk);
      var mVacationCredit = _vacationCreditMin(mVacation);
      var mNettoWithVacation = ms.netto + mVacationCredit;
      var mLkwEntries = Object.keys(ms.lkwSet || {{}}).map(function(k) {{ return [k, ms.lkwSet[k]]; }}).sort(function(a,b) {{ return b[1]-a[1]; }});
      var sec = "<section class='month-block'>";
      sec += "<div class='month-head'><div class='month-title'>" + faEsc(monthLabel) + "</div>";
      sec += "<div class='month-meta'>"
          + "<span>Schichten <b>" + ms.count + "</b></span>"
          + "<span>Krank <b>" + mSick.length + "</b></span>"
          + "<span>Urlaub <b>" + mVacation.length + "</b></span>"
          + "<span>Netto <b>" + _fmtMin(ms.netto) + "</b></span>"
          + "<span>Urlaub +8h <b>" + _fmtMin(mVacationCredit) + "</b></span>"
          + "<span>Σ inkl. Urlaub <b>" + _fmtMin(mNettoWithVacation) + "</b></span>"
          + "<span>&gt;10:00 <b style='color:#991b1b;'>" + ms.over10 + "</b></span>"
          + "</div></div>";
      sec += _printAbsenceLine("Kranktage", mSick, "border-color:#fecaca;background:#fff7f7;color:#991b1b;");
      sec += _printAbsenceLine("Urlaubstage (+8:00 je Tag / Σ " + _fmtMin(mVacationCredit) + ")", mVacation, "border-color:#bae6fd;background:#f0f9ff;color:#075985;", function() {{ return " (+8:00)"; }});
      sec += _printLkwLine(mLkwEntries);
      sec += "<table><thead><tr><th>Tag</th><th>Beginn</th><th>Ende</th><th class='num'>Schichtdauer</th><th class='num'>Netto-Arbeitszeit</th><th>LKW</th></tr></thead><tbody>" + _printRows(rowList) + "</tbody></table>";
      sec += "</section>";
      return sec;
    }}

    var doc = "<!doctype html><html><head><meta charset='utf-8'><title>Schichten " + faEsc(name) + " " + faEsc(label) + "</title>" + _printShiftStyles() + "</head><body>";
    doc += "<h1>Schichten / Tachograph</h1>";
    doc += "<div class='sub'><b>Fahrer:</b> " + faEsc(name) + " &nbsp; · &nbsp; <b>Auswahl:</b> " + faEsc(label) + " &nbsp; · &nbsp; <b>Ausdruck:</b> " + faEsc(printedAt) + "</div>";
    doc += "<div class='cards'>"
        + "<div class='card'><div class='label'>Schichten</div><div class='value'>" + stats.count + "</div></div>"
        + "<div class='card'><div class='label'>Kranktage</div><div class='value'>" + sickRows.length + "</div></div>"
        + "<div class='card'><div class='label'>Urlaubstage</div><div class='value'>" + vacationRows.length + "</div></div>"
        + "<div class='card'><div class='label'>Σ Netto Schichten</div><div class='value'>" + _fmtMin(stats.netto) + "</div></div>"
        + "<div class='card'><div class='label'>Urlaub +8h</div><div class='value'>" + _fmtMin(vacationCredit) + "</div></div>"
        + "<div class='card'><div class='label'>Σ Netto inkl. Urlaub</div><div class='value'>" + _fmtMin(nettoWithVacation) + "</div></div>"
        + "<div class='card'><div class='label'>Schichten &gt; 10:00 Netto</div><div class='value'>" + stats.over10 + "</div></div>"
        + "<div class='card'><div class='label'>Σ Schichtdauer</div><div class='value'>" + _fmtMin(stats.dauer) + "</div></div>"
        + "</div>";

    if (monthFilter === "all") {{
      monthKeys.forEach(function(mk) {{
        var grp = byMonth[mk] || {{ label: _monthLabelFromKey(mk), shifts: [] }};
        doc += _printMonthSection(mk, grp.label, grp.shifts || []);
      }});
    }} else {{
      var grp = byMonth[monthFilter] || {{ label: label, shifts: shifts }};
      doc += _printMonthSection(monthFilter, label, grp.shifts || shifts);
    }}

    doc += "<script>window.onload=function(){{setTimeout(function(){{window.print();}},150);}}<\\/script>";
    doc += "</body></html>";

    var w = window.open("", "_blank");
    if (!w) {{
      alert("Der PDF-Druck konnte nicht geöffnet werden. Bitte Pop-up-Blocker prüfen.");
      return;
    }}
    w.document.open();
    w.document.write(doc);
    w.document.close();
  }};

  window.faSwitchTab = function(tab) {{
    faActiveTab = tab;
    if (faSelectedName) window.faShowDetail(faSelectedName);
  }};

  // Override
  window.faShowDetail = function(name) {{
    faSelectedName = name;
    if (window.FA_10H_MODE) {{
      window.FA_10H_SELECTED_DRIVER = name || "";
      faBuildSidebarHighlight(name);
      window.faShow10hTours(name);
      return;
    }}
    window.FA_10H_MODE = false;
    _faSet10hButton(false);
    faBuildSidebarHighlight(name);
    var panel = document.getElementById("fa-detail-panel");
    if (!panel) return;

    var hasShifts = faHasShifts(name);
    var driver = FA_DATA.find(function(d){{ return d.name === name; }});
    var hasUebersicht = !!driver;

    // Wenn nur Schichten-Daten vorhanden, automatisch dorthin wechseln
    if (!hasUebersicht && hasShifts && faActiveTab !== "schichten") {{
      faActiveTab = "schichten";
    }}
    if (!hasShifts && faActiveTab === "schichten") {{
      faActiveTab = "uebersicht";
    }}

    if (faActiveTab === "schichten" && hasShifts) {{
      faRenderShifts(name, panel);
    }} else if (hasUebersicht) {{
      _faShowDetailOrig(name);
      // Tab-Bar oben einfuegen, falls beide Tabs verfuegbar
      var tabBar = faTabBar(name, hasShifts, true);
      if (tabBar) panel.insertAdjacentHTML('afterbegin', tabBar);
    }} else {{
      // Kein Driver in FA_DATA und keine Schichten
      panel.innerHTML = "<div style='color:#94a3b8;padding:40px;text-align:center;font-size:14px;'>Keine Daten f&uuml;r diesen Fahrer.</div>";
    }}
  }};

  // faBuildSidebarHighlight ergänzen: Fahrer aus TIMEREC_DATA hinzufügen, die nicht in FA_DATA sind
  var _faGetFilteredOrig = window.faGetFiltered;
  if (typeof _faGetFilteredOrig === "function") {{
    window.faGetFiltered = function() {{
      var list = _faGetFilteredOrig();
      // Schichten-only Fahrer mergen
      if (TIMEREC_DATA && typeof TIMEREC_DATA === "object") {{
        var have = {{}};
        list.forEach(function(d) {{ have[d.name] = true; }});
        var q = (faSearchQuery || "").toLowerCase().trim();
        Object.keys(TIMEREC_DATA).forEach(function(n) {{
          if (have[n]) return;
          if (q && n.toLowerCase().indexOf(q) < 0) return;
          // Year-Filter prüfen
          if (faYearFilter !== "all") {{
            var hasYr = (TIMEREC_DATA[n] || []).some(function(s) {{
              var m = (s.tag || "").match(/(\\d{{4}})$/);
              return m && m[1] === faYearFilter;
            }});
            if (!hasYr) return;
          }}
          list.push({{ name: n, years: {{}}, _shiftsOnly: true }});
        }});
        if (faCurrentSort === "name") {{
          list.sort(function(a,b){{ return a.name.localeCompare(b.name, "de"); }});
        }}
      }}
      return list;
    }};
  }}

  // faPopulateYears erweitern: Jahre aus TIMEREC_DATA hinzufuegen
  var _faPopulateYearsOrig = window.faPopulateYears;
  if (typeof _faPopulateYearsOrig === "function") {{
    window.faPopulateYears = function() {{
      _faPopulateYearsOrig();
      var yrSel = document.getElementById("fa-year-sel");
      if (!yrSel) return;
      var existing = {{}};
      Array.prototype.slice.call(yrSel.options).forEach(function(o){{ existing[o.value] = true; }});
      var newYears = [];
      Object.keys(TIMEREC_DATA || {{}}).forEach(function(n) {{
        (TIMEREC_DATA[n] || []).forEach(function(s) {{
          var m = (s.tag || "").match(/(\\d{{4}})$/);
          if (m && !existing[m[1]] && m[1] !== "2024") {{
            existing[m[1]] = true;
            newYears.push(m[1]);
          }}
        }});
      }});
      if (newYears.length) {{
        var current = yrSel.value;
        var allValues = Array.prototype.slice.call(yrSel.options).map(function(o){{return o.value;}}).concat(newYears);
        allValues = allValues.filter(function(v,i,a){{ return a.indexOf(v) === i; }});
        allValues.sort().reverse();
        yrSel.innerHTML = allValues.map(function(y){{
          return "<option value='" + y + "'>" + y + "</option>";
        }}).join("");
        if (current && allValues.indexOf(current) !== -1) yrSel.value = current;
        else yrSel.value = allValues[0];
        faYearFilter = yrSel.value;
      }}
    }};
  }}

  // faRender erweitern: auch bei leerem FA_DATA arbeiten, sofern TIMEREC_DATA da ist
  var _faRenderOrig = window.faRender;
  if (typeof _faRenderOrig === "function") {{
    window.faRender = function(q) {{
      faSearchQuery = q || "";
      var hasFA = FA_DATA && FA_DATA.length;
      var hasTR = TIMEREC_DATA && Object.keys(TIMEREC_DATA).length > 0;
      if (!hasFA && !hasTR) {{
        var c = document.getElementById("fa-detail-panel");
        if (c) c.innerHTML = "<div style='color:#94a3b8;padding:40px;text-align:center;font-size:14px;'>Keine Daten vorhanden.<br>Bitte Fahrerauswertungs- oder Schicht-Dateien hochladen.</div>";
        return;
      }}
      window.faPopulateYears();
      window.faBuildSidebarHighlight(null);
    }};
  }}

  // CSV-only Fahrerauswertung: keine alte Übersicht, kein Switcher.
  window.faSort = function(mode) {{
    faCurrentSort = mode;
    ["name","arbeit"].forEach(function(m) {{
      var btn = document.getElementById("fa-sort-" + m);
      if (!btn) return;
      btn.style.background = mode === m ? "#334155" : "#fff";
      btn.style.color = mode === m ? "#111827" : "#334155";
    }});
    faBuildSidebarHighlight(null);
  }};

  window.faYearChange = function(yr) {{
    faYearFilter = yr || "all";
    if (window.FA_10H_MODE) {{
      window.faShow10hTours(window.FA_10H_SELECTED_DRIVER || "");
    }} else {{
      faBuildSidebarHighlight(null);
    }}
  }};

  function _faAllShiftNames() {{
    return Object.keys(TIMEREC_DATA || {{}}).filter(function(n) {{
      return n && (TIMEREC_DATA[n] || []).length > 0;
    }});
  }}

  function _faShiftRowsForYear(name) {{
    var rows = (TIMEREC_DATA && TIMEREC_DATA[name] ? TIMEREC_DATA[name] : []).slice();
    if (faYearFilter === "all") return rows;
    return rows.filter(function(s) {{
      var m = (s.tag || "").match(/(\\d{{4}})$/);
      return m && m[1] === faYearFilter;
    }});
  }}

  function _faShiftStatsForName(name) {{
    var rows = _faShiftRowsForYear(name);
    return _summarizeShifts(rows);
  }}

  function _faSet10hButton(active) {{
    var btn = document.getElementById("fa-btn-10h");
    if (!btn) return;
    btn.textContent = "10H Touren";
    // Wichtig: CSS aus dem neutralen Schema arbeitet mit !important.
    // Deshalb auch hier setProperty(..., "important"), sonst wird der Text
    // beim aktiven Button farblich und wirkt verschwunden.
    btn.style.setProperty("background", active ? "#dc2626" : "#fff", "important");
    btn.style.setProperty("color", active ? "#fff" : "#dc2626", "important");
    btn.style.setProperty("border-color", "#dc2626", "important");
    var btnDriver = document.getElementById("fa-sort-name");
    if (btnDriver) {{
      btnDriver.textContent = "Fahrer Übersicht";
      btnDriver.style.setProperty("background", "linear-gradient(180deg,#e2e8f0 0%,#cbd5e1 100%)", "important");
      btnDriver.style.setProperty("color", "#334155", "important");
      btnDriver.style.setProperty("border-color", "#94a3b8", "important");
    }}
  }}

  function _faShiftDateKey(s) {{
    var raw = String((s && s.tag) || "");
    var m = raw.match(/^(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}})$/);
    if (!m) return "";
    return m[3] + "-" + m[2] + "-" + m[1];
  }}

  function _faDateLabelFromKey(key) {{
    var m = String(key || "").match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
    if (!m) return key || "";
    return m[3] + "." + m[2] + "." + m[1];
  }}

  // ── 10H "Unfertig"-Erkennung ─────────────────────────────────────────────
  // Wenn die Fahrerkarte nach der Tour noch nicht erneut ausgelesen wurde, setzt
  // der Tachograph-Download als Schichtende den Auslesezeitpunkt ein. Dadurch wird
  // die Schicht künstlich lang. Erkennbar daran, dass viele lange Schichten exakt
  // im selben Zeitfenster "enden" (= Moment der Massen-Auslesung) ODER dass eine
  // Schichtdauer völlig unrealistisch ist (> 16:00). Eine echte 10H-Tour (z.B.
  // Ende 13:27 / Dauer 10:57) wird so NICHT fälschlich markiert.
  var _faCutoffData = null;

  function _faEndDateKey(s) {{
    var m = String(_faComputeEndDate(s)).match(/^(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}})$/);
    return m ? (m[3] + "-" + m[2] + "-" + m[1]) : "";
  }}

  function _faEndMinutes(s) {{
    var m = String((s && s.ende) || "").match(/^(\\d{{1,2}}):(\\d{{2}})/);
    if (!m) return -1;
    return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
  }}

  // Schwellen
  var FA_CUTOFF_LONG_MIN   = 720;  // ab 12:00 Schichtdauer gilt eine Schicht als "lang/auffällig"
  var FA_CUTOFF_ABSURD_MIN = 960;  // ab 16:00 Schichtdauer = einzeln schon unmöglich
  var FA_CUTOFF_WINDOW     = 12;   // Minuten-Fenster für gemeinsames Ende
  var FA_CUTOFF_CLUSTER    = 3;    // so viele lange Schichten im Fenster = Massen-Auslesung

  function _faGetCutoffData() {{
    if (_faCutoffData) return _faCutoffData;
    var byDay = {{}};
    Object.keys(TIMEREC_DATA || {{}}).forEach(function(name) {{
      (TIMEREC_DATA[name] || []).forEach(function(s) {{
        if (!s.ende) return;
        if (_toMin(s.schichtdauer) <= FA_CUTOFF_LONG_MIN) return;
        var dk = _faEndDateKey(s);
        var em = _faEndMinutes(s);
        if (!dk || em < 0) return;
        (byDay[dk] = byDay[dk] || []).push(em);
      }});
    }});
    _faCutoffData = byDay;
    return _faCutoffData;
  }}

  function _faIsShiftReadoutCutoff(s) {{
    if (!s.ende) return false;
    var gross = _toMin(s.schichtdauer);
    // a) einzelne, völlig unrealistische Schichtdauer
    if (gross > FA_CUTOFF_ABSURD_MIN) return true;
    // b) lange Schicht, die mit weiteren im selben Fenster endet (Massen-Auslesung)
    if (gross <= FA_CUTOFF_LONG_MIN) return false;
    var em = _faEndMinutes(s);
    if (em < 0) return false;
    var list = _faGetCutoffData()[_faEndDateKey(s)] || [];
    var n = 0;
    for (var i = 0; i < list.length; i++) {{
      if (Math.abs(list[i] - em) <= FA_CUTOFF_WINDOW) n++;
    }}
    return n >= FA_CUTOFF_CLUSTER;
  }}

  function _faIs10hUnfertig(s) {{
    if (_faIsShiftReadoutCutoff(s)) return true;  // fuzzy: Ende-Fenster / unmögliche Dauer
    if (_faIsShiftCutoff(s)) return true;          // exakt identisches Ende (Altlogik)
    return false;
  }}

  function _faNormLkw(value) {{
    var s = String(value == null ? "" : value).toLowerCase();
    try {{ s = s.normalize("NFKD").replace(/[̀-ͯ]/g, ""); }} catch(e) {{}}
    s = s.replace(/[^a-z0-9]+/g, "");
    return s;
  }}

  function _faLkwMatch(a, b) {{
    var an = _faNormLkw(a), bn = _faNormLkw(b);
    if (!an || !bn) return false;
    return an === bn || an.indexOf(bn) >= 0 || bn.indexOf(an) >= 0;
  }}

  function _faEntryTimeDiffMin(planTime, shiftTime) {{
    var a = _toMin(planTime), b = _toMin(shiftTime);
    if (!a || !b) return 99999;
    return Math.abs(a - b);
  }}

  function _faAddDaysKey(key, days) {{
    var m = String(key || "").match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
    if (!m) return "";
    var d = new Date(parseInt(m[1],10), parseInt(m[2],10)-1, parseInt(m[3],10));
    d.setDate(d.getDate() + (days || 0));
    var y = d.getFullYear();
    var mo = String(d.getMonth()+1).padStart(2,"0");
    var da = String(d.getDate()).padStart(2,"0");
    return y + "-" + mo + "-" + da;
  }}

  function _faCandidateDateKeys(shift) {{
    var dk = _faShiftDateKey(shift);
    if (!dk) return [];
    // Schichtbeginn prüfen: bei spätem Start (>= 20:00) auch Folgetag suchen,
    // weil der Fahrer kurz vor Mitternacht seine Karte steckt, die Tour aber
    // im Tourenplan unter dem nächsten Tag steht.
    var beginn = _toMin(shift && shift.beginn);
    if (beginn && beginn >= 1200) {{
      // 1200 min = 20:00 Uhr → auch Folgetag als Kandidat
      var nextDk = _faAddDaysKey(dk, 1);
      return nextDk ? [dk, nextDk] : [dk];
    }}
    return [dk];
  }}

  function _faPlanEntriesForShift(name, shift) {{
    var driver = _faPlanDriver(name);
    if (!driver || !driver.years) return [];
    var candidateKeys = _faCandidateDateKeys(shift);
    if (!candidateKeys.length) return [];
    var keyRank = {{}};
    candidateKeys.forEach(function(k, i) {{ keyRank[k] = i; }});
    var years = faYearFilter === "all" ? Object.keys(driver.years || {{}}) : [faYearFilter];
    // Bei Schichten über Jahreswechsel auch angrenzende Jahre durchsuchen.
    candidateKeys.forEach(function(k) {{
      var y = k.slice(0,4);
      if (years.indexOf(y) < 0) years.push(y);
    }});
    var out = [];
    years.forEach(function(y) {{
      var data = driver.years[y];
      if (!data) return;
      (data.eintraege || []).forEach(function(e) {{
        var planKey = _faPlanDateKey(e);
        if (keyRank[planKey] === undefined) return;
        var t = String(e.tour || "");
        if (/krank|urlaub|ausgleich/i.test(t)) return;
        e._matchDateRank = keyRank[planKey];
        e._matchDriverName = driver.name || name;
        e._matchSource = "Fahrer + Anfangstag";
        out.push(e);
      }});
    }});
    return out;
  }}

  function _faAllPlanEntriesForShift(shift) {{
    var candidateKeys = _faCandidateDateKeys(shift);
    if (!candidateKeys.length || !Array.isArray(FA_DATA)) return [];
    var keyRank = {{}};
    candidateKeys.forEach(function(k, i) {{ keyRank[k] = i; }});
    var years = faYearFilter === "all" ? null : {{}};
    if (years) {{
      years[faYearFilter] = true;
      candidateKeys.forEach(function(k) {{ years[k.slice(0,4)] = true; }});
    }}
    var out = [];
    FA_DATA.forEach(function(driver) {{
      if (!driver || !driver.years) return;
      Object.keys(driver.years || {{}}).forEach(function(y) {{
        if (years && !years[y]) return;
        var data = driver.years[y];
        if (!data) return;
        (data.eintraege || []).forEach(function(e) {{
          var planKey = _faPlanDateKey(e);
          if (keyRank[planKey] === undefined) return;
          var t = String(e.tour || "");
          if (/krank|urlaub|ausgleich/i.test(t)) return;
          e._matchDateRank = keyRank[planKey];
          e._matchDriverName = driver.name || "";
          e._matchSource = "Fallback Anfangstag";
          out.push(e);
        }});
      }});
    }});
    return out;
  }}

  function _faScorePlanCandidate(e, shift, strictDriver) {{
    var score = strictDriver ? 500 : 900;
    var dateRank = e._matchDateRank || 0;
    score += dateRank * 120;

    var planLkw = String(e.lkw || "").trim();
    var shiftLkw = String(shift && shift.lkw || "").trim();
    var hasPlanLkw = !!_faNormLkw(planLkw);
    var hasShiftLkw = !!_faNormLkw(shiftLkw);
    var lkwOk = _faLkwMatch(planLkw, shiftLkw);
    if (lkwOk) score -= strictDriver ? 700 : 850;
    else if (hasPlanLkw && hasShiftLkw) score += strictDriver ? 180 : 420;
    else score += 80;

    var diff = _faEntryTimeDiffMin(e.zeit, shift.beginn);
    // Bei Folgetag-Zuordnung (dateRank>0, Schicht >= 20:00): Zeitdiff über
    // Mitternacht berechnen, z.B. Schicht 23:44, Plan 00:00 → 16 min
    if (dateRank > 0) {{
      var shiftMin = _toMin(shift && shift.beginn);
      var planMin  = _toMin(e.zeit);
      if (shiftMin >= 1200 && planMin !== null) {{
        var midnightDiff = (1440 - shiftMin) + planMin;
        if (midnightDiff < diff) diff = midnightDiff;
      }}
    }}
    if (diff <= 30) score -= 360;
    else if (diff <= 90) score -= 260;
    else if (diff <= 180) score -= 120;
    else if (diff <= 360) score -= 40;
    else score += strictDriver ? 40 : 140;

    var tourText = String(e.tour || "").trim();
    if (tourText) score -= 120;
    if (/z\\.?\\s*b\\.?\\s*v|sonder/i.test(tourText)) score -= 50;
    return score;
  }}

  function _faFindPlanEntryForShift(name, shift) {{
    function _candidateIsUsable(e, strictDriver, candidateCount) {{
      var lkwOk = _faLkwMatch(e && e.lkw, shift && shift.lkw);
      var diff = _faEntryTimeDiffMin(e && e.zeit, shift && shift.beginn);
      var hasPlanLkw = !!_faNormLkw(e && e.lkw);
      var hasShiftLkw = !!_faNormLkw(shift && shift.lkw);
      var hasPlanTime = !!String((e && e.zeit) || "").trim();

      // Bei exakt gleichem Fahrer + Anfangstag reicht ein einzelner Eintrag aus.
      // Gibt es mehrere Eintraege am Tag, muss LKW oder Startzeit helfen.
      if (strictDriver && candidateCount === 1) return true;
      if (strictDriver && lkwOk) return true;
      if (strictDriver && hasPlanTime && diff <= 180) return true;

      // Fallback ohne sauberen Fahrernamen nur verwenden, wenn LKW und Zeit stimmig sind.
      // Reines "gleicher Tag" ist zu unsicher und fuehrte zu falschen Zuordnungen.
      if (!strictDriver && lkwOk && diff <= 360) return true;
      if (!strictDriver && lkwOk && !hasPlanTime) return true;
      if (!strictDriver && !hasShiftLkw && hasPlanTime && diff <= 45) return true;
      return false;
    }}

    var candidates = _faPlanEntriesForShift(name, shift);
    var best = null;
    var bestScore = 999999;

    candidates.forEach(function(e) {{
      if (!_candidateIsUsable(e, true, candidates.length)) return;
      var score = _faScorePlanCandidate(e, shift, true);
      if (score < bestScore) {{ bestScore = score; best = e; }}
    }});
    if (best) {{
      var lkwOkStrict = _faLkwMatch(best.lkw, shift.lkw);
      var diffStrict = _faEntryTimeDiffMin(best.zeit, shift.beginn);
      var nextDay = (best._matchDateRank || 0) > 0 ? " (Folgetag)" : "";
      if (lkwOkStrict && diffStrict <= 360) best._matchSource = "Fahrer + Anfangstag + LKW + Startzeit" + nextDay;
      else if (lkwOkStrict) best._matchSource = "Fahrer + Anfangstag + LKW" + nextDay;
      else if (diffStrict <= 180) best._matchSource = "Fahrer + Anfangstag + Startzeit" + nextDay;
      else best._matchSource = "Fahrer + Anfangstag" + nextDay;
      return best;
    }}

    // Fallback nur noch bei eindeutigen harten Treffern.
    // Keine Tour wird besser ausgeblendet als falsch angezeigt.
    var allCandidates = _faAllPlanEntriesForShift(shift).filter(function(e) {{
      return _candidateIsUsable(e, false, 0);
    }});
    allCandidates.forEach(function(e) {{
      var score = _faScorePlanCandidate(e, shift, false);
      if (score < bestScore) {{ bestScore = score; best = e; }}
    }});
    if (best) {{
      var lkwOk = _faLkwMatch(best.lkw, shift.lkw);
      var diff = _faEntryTimeDiffMin(best.zeit, shift.beginn);
      var nextDay = (best._matchDateRank || 0) > 0 ? " (Folgetag)" : "";
      if (lkwOk && diff <= 360) best._matchSource = "Fallback: LKW + Anfangstag + Startzeit" + nextDay;
      else if (lkwOk) best._matchSource = "Fallback: LKW + Anfangstag" + nextDay;
      else best._matchSource = "Fallback: Anfangstag + Startzeit" + nextDay;
    }}
    return best;
  }}

  function _faBuild10hRows(driverFilter) {{
    var q = (faSearchQuery || "").toLowerCase().trim();
    var selectedDriver = String(driverFilter || "").trim();
    var rows = [];
    Object.keys(TIMEREC_DATA || {{}}).forEach(function(name) {{
      if (selectedDriver && name !== selectedDriver) return;
      if (q && name.toLowerCase().indexOf(q) < 0) return;
      (TIMEREC_DATA[name] || []).forEach(function(s) {{
        var yrOk = true;
        if (faYearFilter !== "all") {{
          var m = (s.tag || "").match(/(\\d{{4}})$/);
          yrOk = !!(m && m[1] === faYearFilter);
        }}
        if (!yrOk) return;
        if (_faIsShiftInvalid(s)) return;
        var netto = _toMin(s.profil);
        if (netto <= 600) return;
        var plan = _faFindPlanEntryForShift(name, s);
        var tourText = plan && plan.tour ? String(plan.tour).trim() : "";
        // 10H-Schichten immer anzeigen. Wenn keine Tour gefunden wurde,
        // bleibt die Zeile sichtbar und wird in der Tour-Spalte markiert.
        if (!tourText) tourText = "nicht gefunden";
        var dk = _faShiftDateKey(s);
        rows.push({{
          fahrer: name,
          dateKey: dk,
          datum: _faDateLabelFromKey(dk),
          wochentag: s.wochentag || "",
          beginn: s.beginn || "",
          ende: (s.ende || "") + (s.ende_naechster_tag ? " +1" : ""),
          schichtdauer: s.schichtdauer || "",
          nettoText: s.profil || _fmtMin(netto),
          nettoMin: netto,
          lkw: s.lkw || "",
          tour: tourText,
          planLkw: plan && plan.lkw ? String(plan.lkw).trim() : "",
          planZeit: plan && plan.zeit ? String(plan.zeit).trim() : "",
          planDatum: plan ? _faDateLabelFromKey(_faPlanDateKey(plan)) : "",
          planFahrer: plan && plan._matchDriverName ? String(plan._matchDriverName).trim() : "",
          matchSource: plan && plan._matchSource ? String(plan._matchSource).trim() : "",
          unfertig: _faIs10hUnfertig(s)
        }});
      }});
    }});
    rows.sort(function(a,b) {{
      return (b.dateKey || "").localeCompare(a.dateKey || "") || a.fahrer.localeCompare(b.fahrer, "de") || (a.beginn || "").localeCompare(b.beginn || "");
    }});
    return rows;
  }}

  function _faCountKeys(obj) {{
    return Object.keys(obj || {{}}).length;
  }}

  function _faRender10hFrequency(rows) {{
    if (!rows.length) return "";
    var tourMap = {{}}, fahrerMap = {{}};
    rows.forEach(function(r) {{
      var tour = String(r.tour || "").trim();
      if (!tour) return;
      if (!tourMap[tour]) tourMap[tour] = {{ name: tour, count: 0, netto: 0, fahrer: {{}} }};
      tourMap[tour].count += 1;
      tourMap[tour].netto += r.nettoMin || 0;
      tourMap[tour].fahrer[r.fahrer || ""] = true;

      var fahrer = String(r.fahrer || "").trim() || "unbekannt";
      if (!fahrerMap[fahrer]) fahrerMap[fahrer] = {{ name: fahrer, count: 0, netto: 0, touren: {{}} }};
      fahrerMap[fahrer].count += 1;
      fahrerMap[fahrer].netto += r.nettoMin || 0;
      fahrerMap[fahrer].touren[tour] = true;
    }});

    function sortedList(map) {{
      return Object.keys(map).map(function(k) {{ return map[k]; }}).sort(function(a,b) {{
        return (b.count - a.count) || a.name.localeCompare(b.name, "de");
      }});
    }}

    function renderBox(title, sub, headers, rowsHtml, countLabel) {{
      return "<details style='background:#fff;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;min-width:0;'>"
        + "<summary style='list-style:none;cursor:pointer;padding:10px 12px;background:#f8fbff;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between;gap:10px;'>"
        + "<span><span style='font-size:13px;font-weight:950;color:#0f172a;'>" + title + "</span><span style='display:block;font-size:10.5px;font-weight:700;color:#64748b;margin-top:2px;'>" + sub + "</span></span>"
        + "<span style='font-size:10px;font-weight:950;color:#be123c;background:#fff1f2;border:1px solid #fecdd3;border-radius:999px;padding:3px 8px;white-space:nowrap;'>" + countLabel + "</span>"
        + "</summary>"
        + "<div style='max-height:260px;overflow:auto;'>"
        + "<table style='width:100%;border-collapse:collapse;font-size:11.5px;'>"
        + "<thead><tr style='background:#f1f5f9;'>" + headers.map(function(h) {{ return "<th style='position:sticky;top:0;background:#f1f5f9;padding:7px 9px;border-bottom:1px solid #cbd5e1;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.35px;color:#475569;font-weight:950;'>" + h + "</th>"; }}).join("") + "</tr></thead>"
        + "<tbody>" + rowsHtml + "</tbody></table></div></details>";
    }}

    var tourRows = sortedList(tourMap).map(function(t, i) {{
      return "<tr style='background:" + (i % 2 ? "#fff" : "#f8fafc") + ";border-bottom:1px solid #e2e8f0;'>"
        + "<td style='padding:7px 9px;font-weight:900;color:#0f172a;white-space:normal;line-height:1.25;'>" + faEsc(t.name) + "</td>"
        + "<td style='padding:7px 9px;text-align:right;font-weight:950;color:#be123c;font-variant-numeric:tabular-nums;'>" + t.count + "</td>"
        + "<td style='padding:7px 9px;text-align:right;font-weight:850;color:#0f172a;font-variant-numeric:tabular-nums;'>" + _faCountKeys(t.fahrer) + "</td>"
        + "<td style='padding:7px 9px;text-align:right;font-weight:850;color:#0f172a;font-variant-numeric:tabular-nums;'>" + _fmtMin(t.netto) + "</td>"
        + "</tr>";
    }}).join("");

    var fahrerRows = sortedList(fahrerMap).map(function(f, i) {{
      return "<tr style='background:" + (i % 2 ? "#fff" : "#f8fafc") + ";border-bottom:1px solid #e2e8f0;'>"
        + "<td style='padding:7px 9px;font-weight:900;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + faEsc(f.name) + "</td>"
        + "<td style='padding:7px 9px;text-align:right;font-weight:950;color:#be123c;font-variant-numeric:tabular-nums;'>" + f.count + "</td>"
        + "<td style='padding:7px 9px;text-align:right;font-weight:850;color:#0f172a;font-variant-numeric:tabular-nums;'>" + _faCountKeys(f.touren) + "</td>"
        + "<td style='padding:7px 9px;text-align:right;font-weight:850;color:#0f172a;font-variant-numeric:tabular-nums;'>" + _fmtMin(f.netto) + "</td>"
        + "</tr>";
    }}).join("");

    return "<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;'>"
      + renderBox("Touren-Häufigkeit", "Welche Tournummer / welcher Tourtext kommt in den 10H-Schichten wie oft vor", ["Tournummer", "Anzahl", "Fahrer", "Netto"], tourRows, sortedList(tourMap).length + " Touren")
      + renderBox("Fahrer-Häufigkeit", "Welche Fahrer haben wie viele 10H-Touren", ["Fahrer", "Anzahl", "Touren", "Netto"], fahrerRows, sortedList(fahrerMap).length + " Fahrer")
      + "</div>";
  }}

  function _faRender10hRows(rows) {{
    if (!rows.length) {{
      return "<div style='background:#fff;border:1px solid #e2e8f0;border-radius:5px;padding:40px;text-align:center;color:#94a3b8;font-size:14px;'>Keine 10H Touren in der aktuellen Auswahl.</div>";
    }}
    var html = "<div style='background:#fff;border:1px solid #e2e8f0;border-radius:6px;overflow:auto;'>";
    html += "<table style='width:100%;min-width:1380px;border-collapse:collapse;font-size:12px;table-layout:fixed;'>";
    html += "<colgroup><col style='width:185px;'><col style='width:104px;'><col style='width:70px;'><col style='width:70px;'><col style='width:82px;'><col style='width:82px;'><col style='width:120px;'><col style='width:340px;'><col style='width:240px;'></colgroup>";
    html += "<thead><tr style='background:#fff1f2;color:#334155;'>";
    ["Fahrer","Datum","Beginn","Ende","Schicht","Netto","LKW","Tournummer","Zuordnung"].forEach(function(h) {{
      html += "<th style='padding:8px 9px;border-bottom:1px solid #fecdd3;text-align:left;font-size:9.5px;text-transform:uppercase;letter-spacing:.35px;font-weight:900;'>" + h + "</th>";
    }});
    html += "</tr></thead><tbody>";
    rows.forEach(function(r, i) {{
      var bg = i % 2 ? "#fff" : "#fff7f8";
      if (r.unfertig) bg = i % 2 ? "#fffbeb" : "#fef3c7";
      var found = r.tour && r.tour !== "nicht gefunden";
      var unfertigBadge = r.unfertig
        ? "<div style='margin-top:3px;'><span title='Karte nach der Tour noch nicht ausgelesen – Zeit unvollständig' style='display:inline-block;padding:1px 7px;border-radius:999px;background:#f59e0b;color:#fff;font-size:8.5px;font-weight:950;text-transform:uppercase;letter-spacing:.4px;'>Unfertig</span></div>"
        : "";
      html += "<tr style='background:" + bg + ";border-bottom:1px solid #f1f5f9;'>";
      html += "<td style='padding:7px 9px;font-weight:900;color:#0f172a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + faEsc(r.fahrer) + "</td>";
      html += "<td style='padding:7px 9px;font-weight:800;color:#0f172a;font-variant-numeric:tabular-nums;white-space:nowrap;'>" + faEsc((r.wochentag ? r.wochentag + " " : "") + r.datum) + unfertigBadge + "</td>";
      html += "<td style='padding:7px 9px;color:#475569;font-variant-numeric:tabular-nums;white-space:nowrap;'>" + faEsc(r.beginn) + "</td>";
      html += "<td style='padding:7px 9px;color:#475569;font-variant-numeric:tabular-nums;white-space:nowrap;'>" + faEsc(r.ende) + "</td>";
      html += "<td style='padding:7px 9px;text-align:right;color:#1e3a5f;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;'>" + faEsc(r.schichtdauer) + "</td>";
      html += "<td style='padding:7px 9px;text-align:right;color:#be123c;font-weight:950;font-variant-numeric:tabular-nums;white-space:nowrap;'>" + faEsc(r.nettoText) + "</td>";
      html += "<td style='padding:7px 9px;color:#166534;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + faEsc(r.lkw) + "</td>";
      html += "<td style='padding:7px 9px;font-weight:950;color:" + (found ? "#0f172a" : "#be123c") + ";white-space:normal;overflow:visible;text-overflow:clip;line-height:1.25;'>" + faEsc(r.tour) + "</td>";
      html += "<td style='padding:7px 9px;color:#64748b;font-size:10.5px;font-weight:700;white-space:normal;line-height:1.25;overflow:visible;text-overflow:clip;'>" + (found ? ("Excel: " + faEsc((r.planDatum ? r.planDatum + " · " : "") + (r.planZeit ? r.planZeit + " · " : "") + (r.planLkw ? r.planLkw + " · " : "") + (r.planFahrer ? r.planFahrer + " · " : "") + (r.matchSource || ""))) : "kein passender Excel-Eintrag") + "</td>";
      html += "</tr>";
    }});
    html += "</tbody></table></div>";
    return html;
  }}

  function _fa10hAggRows(rows, mode) {{
    var map = {{}};
    (rows || []).forEach(function(r) {{
      var tour = String(r.tour || "").trim();
      if (!tour) return;
      var driver = String(r.fahrer || "unbekannt").trim() || "unbekannt";
      if (mode === "tour") {{
        if (!map[tour]) map[tour] = {{ name: tour, count: 0, netto: 0, fahrer: {{}} }};
        map[tour].count += 1;
        map[tour].netto += r.nettoMin || 0;
        map[tour].fahrer[driver] = true;
      }} else {{
        if (!map[driver]) map[driver] = {{ name: driver, count: 0, netto: 0, touren: {{}} }};
        map[driver].count += 1;
        map[driver].netto += r.nettoMin || 0;
        map[driver].touren[tour] = true;
      }}
    }});
    return Object.keys(map).map(function(k) {{ return map[k]; }}).sort(function(a,b) {{
      return (b.count - a.count) || a.name.localeCompare(b.name, "de");
    }});
  }}

  function _faSafePdfName(v) {{
    return String(v || "")
      .replace(/[\\/:*?"<>|]+/g, "_")
      .replace(/\\s+/g, "_")
      .slice(0, 80) || "Auswertung";
  }}

  window.faExport10hPdf = function() {{
    var rows = (window.FA_LAST_10H_ALL_ROWS || window.FA_LAST_10H_ROWS || []).slice();
    if (!rows.length) rows = _faBuild10hRows("");
    if (!rows.length) {{ alert("Keine 10H Touren in der aktuellen Auswahl."); return; }}
    if(!window.jspdf || !window.jspdf.jsPDF || typeof window.jspdf.jsPDF !== "function") {{
      alert("PDF-Bibliothek ist noch nicht geladen. Bitte kurz warten und erneut versuchen.");
      return;
    }}
    var jsPDF = window.jspdf.jsPDF;
    var doc = new jsPDF({{ orientation:"portrait", unit:"mm", format:"a4" }});
    var yearLabel = faYearFilter === "all" ? "Alle Jahre" : faYearFilter;
    var sumNetto = rows.reduce(function(s, r) {{ return s + (r.nettoMin || 0); }}, 0);
    var title = "10H Touren - Touren-Häufigkeit - " + yearLabel;

    doc.setFont("helvetica", "bold");
    doc.setFontSize(15);
    doc.text(title, 12, 14);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.text("Treffer: " + rows.length + "   Netto gesamt: " + _fmtMin(sumNetto) + "   Zuordnung nach Anfangstag", 12, 20);

    var tourAgg = _fa10hAggRows(rows, "tour").map(function(t) {{
      return [t.name, String(t.count), String(_faCountKeys(t.fahrer)), _fmtMin(t.netto)];
    }});
    doc.autoTable({{
      startY: 27,
      head: [["Tournummer / Tourtext", "Anzahl", "Fahrer", "Netto"]],
      body: tourAgg,
      styles: {{ fontSize: 8, cellPadding: 1.8, overflow: "linebreak", valign: "top" }},
      headStyles: {{ fillColor: [109, 40, 217], textColor: 255, fontStyle: "bold" }},
      columnStyles: {{ 0: {{ cellWidth: 112 }}, 1: {{ halign: "right", cellWidth: 22 }}, 2: {{ halign: "right", cellWidth: 22 }}, 3: {{ halign: "right", cellWidth: 28 }} }},
      margin: {{ left: 12, right: 12 }},
      didDrawPage: function(data) {{
        var pageCount = doc.internal.getNumberOfPages();
        doc.setFontSize(7);
        doc.setTextColor(120);
        doc.text("Seite " + pageCount, 198, 288, {{ align: "right" }});
      }}
    }});
    doc.save("10H_Touren_Haeufigkeit_Tour_" + _faSafePdfName(yearLabel) + ".pdf");
  }};

  window.faExport10hDriverPdf = function(driverName) {{
    var rows = (window.FA_LAST_10H_ALL_ROWS || window.FA_LAST_10H_ROWS || []).slice();
    if (!rows.length) rows = _faBuild10hRows("");
    if (!driverName) {{
      var sel = document.getElementById("fa10hDriverPdfSel");
      driverName = sel ? sel.value : "";
    }}
    driverName = String(driverName || "").trim();
    if (!driverName) {{ alert("Bitte Fahrer auswählen."); return; }}
    rows = rows.filter(function(r) {{ return String(r.fahrer || "").trim() === driverName; }});
    if (!rows.length) {{ alert("Keine 10H Touren für " + driverName + " in der aktuellen Auswahl."); return; }}
    if(!window.jspdf || !window.jspdf.jsPDF || typeof window.jspdf.jsPDF !== "function") {{
      alert("PDF-Bibliothek ist noch nicht geladen. Bitte kurz warten und erneut versuchen.");
      return;
    }}
    var jsPDF = window.jspdf.jsPDF;
    var doc = new jsPDF({{ orientation:"landscape", unit:"mm", format:"a4" }});
    var yearLabel = faYearFilter === "all" ? "Alle Jahre" : faYearFilter;
    var sumNetto = rows.reduce(function(s, r) {{ return s + (r.nettoMin || 0); }}, 0);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(15);
    doc.text("10H Touren - " + driverName, 10, 14);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.text("Auswahl: " + yearLabel + "   Treffer: " + rows.length + "   Netto gesamt: " + _fmtMin(sumNetto) + "   Zuordnung nach Anfangstag", 10, 20);

    var tourAgg = _fa10hAggRows(rows, "tour").map(function(t) {{
      return [t.name, String(t.count), _fmtMin(t.netto)];
    }});
    doc.autoTable({{
      startY: 27,
      head: [["Tournummer / Tourtext", "Anzahl", "Netto"]],
      body: tourAgg,
      styles: {{ fontSize: 7.5, cellPadding: 1.5, overflow: "linebreak" }},
      headStyles: {{ fillColor: [109, 40, 217], textColor: 255, fontStyle: "bold" }},
      columnStyles: {{ 0: {{ cellWidth: 105 }}, 1: {{ halign: "right", cellWidth: 20 }}, 2: {{ halign: "right", cellWidth: 25 }} }},
      margin: {{ left: 10, right: 140 }}
    }});
    var startRowsY = doc.lastAutoTable ? Math.max(doc.lastAutoTable.finalY + 8, 27) : 27;
    if (startRowsY > 72) {{ doc.addPage(); startRowsY = 14; }}

    doc.autoTable({{
      startY: startRowsY,
      head: [["Datum", "Beginn", "Ende", "Schicht", "Netto", "LKW", "Tournummer", "Zuordnung"]],
      body: rows.map(function(r) {{
        var found = r.tour && r.tour !== "nicht gefunden";
        return [
          (r.wochentag ? r.wochentag + " " : "") + (r.datum || ""),
          r.beginn || "",
          r.ende || "",
          r.schichtdauer || "",
          r.nettoText || "",
          r.lkw || "",
          r.tour || "",
          (r.planDatum ? r.planDatum + " · " : "") + (r.planZeit ? r.planZeit + " · " : "") + (r.planLkw ? r.planLkw + " · " : "") + (r.matchSource || "")
        ];
      }}),
      styles: {{ fontSize: 7.2, cellPadding: 1.5, overflow: "linebreak", valign: "top" }},
      headStyles: {{ fillColor: [124, 58, 237], textColor: 255, fontStyle: "bold" }},
      columnStyles: {{
        0: {{ cellWidth: 28 }},
        1: {{ cellWidth: 16 }},
        2: {{ cellWidth: 17 }},
        3: {{ cellWidth: 18, halign: "right" }},
        4: {{ cellWidth: 18, halign: "right" }},
        5: {{ cellWidth: 25 }},
        6: {{ cellWidth: 92 }},
        7: {{ cellWidth: 58 }}
      }},
      margin: {{ left: 10, right: 10 }},
      didDrawPage: function(data) {{
        var pageCount = doc.internal.getNumberOfPages();
        doc.setFontSize(7);
        doc.setTextColor(120);
        doc.text("Seite " + pageCount, 286, 202, {{ align: "right" }});
      }}
    }});
    doc.save("10H_Touren_" + _faSafePdfName(driverName) + "_" + _faSafePdfName(yearLabel) + ".pdf");
  }};

  window.FA_10H_MODE = false;
  window.FA_10H_SELECTED_DRIVER = "";

  window.faShow10hTours = function(driverFilter) {{
    var panel = document.getElementById("fa-detail-panel");
    if (!panel) return;
    _faSet10hButton(true);
    window.FA_10H_MODE = true;
    var selectedDriver = (driverFilter === undefined || driverFilter === null) ? String(window.FA_10H_SELECTED_DRIVER || "").trim() : String(driverFilter || "").trim();
    window.FA_10H_SELECTED_DRIVER = selectedDriver;
    if (selectedDriver) {{
      faSelectedName = selectedDriver;
      faBuildSidebarHighlight(selectedDriver);
    }} else {{
      faSelectedName = null;
    }}

    var allRows = _faBuild10hRows("");
    var rows = selectedDriver ? allRows.filter(function(r) {{ return String(r.fahrer || "") === selectedDriver; }}) : allRows;
    window.FA_LAST_10H_ALL_ROWS = allRows;
    window.FA_LAST_10H_ROWS = rows;
    var sumNetto = rows.reduce(function(s, r) {{ return s + (r.nettoMin || 0); }}, 0);
    var yearLabel = faYearFilter === "all" ? "Alle Jahre" : faYearFilter;
    var html = "";
    html += "<div style='background:#fff;border:1.5px solid #cbd5e1;border-radius:5px;padding:14px 18px;margin-bottom:12px;'>";
    html += "<div style='display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;'>";
    html += "<div><div style='font-size:20px;font-weight:950;color:#334155;'>10H Touren" + (selectedDriver ? " · " + faEsc(selectedDriver) : "") + "</div>";
    html += "<div style='font-size:11.5px;color:#64748b;font-weight:700;margin-top:3px;'>Alle Schichten mit mehr als 10:00 Netto-Arbeitszeit. Schichten ohne passende Tour werden mit „nicht gefunden“ angezeigt. Zuordnung nach Fahrer, Anfangsdatum, LKW und Startzeit.</div></div>";
    html += "<div style='display:flex;gap:8px;flex-wrap:wrap;align-items:center;'>";
    var driverOptions = Object.keys(allRows.reduce(function(o, r) {{ if (r.fahrer) o[r.fahrer] = true; return o; }}, {{}})).sort(function(a,b) {{ return a.localeCompare(b, "de"); }}).map(function(n) {{
      return "<option value='" + faEsc(n) + "'" + (n === selectedDriver ? " selected" : "") + ">" + faEsc(n) + "</option>";
    }}).join("");
    html += "<button type='button' onclick='faExport10hPdf()' style='border:2px solid #cbd5e1;background:#cbd5e1;color:#111827;border-radius:5px;padding:5px 12px;font-size:12px;font-weight:950;cursor:pointer;font-family:inherit;'>PDF Tour-Häufigkeit</button>";
    html += "<select id='fa10hDriverPdfSel' onchange='window.FA_10H_SELECTED_DRIVER=this.value; faShow10hTours(this.value);' style='border:1px solid #cbd5e1;background:#fff;color:#0f172a;border-radius:5px;padding:5px 8px;font-size:12px;font-weight:800;font-family:inherit;max-width:260px;'><option value=''>Alle Fahrer anzeigen</option>" + driverOptions + "</select>";
    html += "<button type='button' onclick='faExport10hDriverPdf()' style='border:2px solid #cbd5e1;background:#cbd5e1;color:#111827;border-radius:5px;padding:5px 12px;font-size:12px;font-weight:950;cursor:pointer;font-family:inherit;'>PDF Fahrer</button>";
    html += "<span style='background:#f1f5f9;color:#334155;border:1px solid #cbd5e1;border-radius:5px;padding:5px 12px;font-size:12px;font-weight:900;'>" + rows.length + " Treffer</span>";
    html += "<span style='background:#f8fbff;color:#0f172a;border:1px solid #e2e8f0;border-radius:5px;padding:5px 12px;font-size:12px;font-weight:800;'>Auswahl: " + faEsc(yearLabel) + "</span>";
    html += "<span style='background:#f8fbff;color:#0f172a;border:1px solid #e2e8f0;border-radius:5px;padding:5px 12px;font-size:12px;font-weight:800;'>Σ Netto " + _fmtMin(sumNetto) + "</span>";
    html += "</div></div></div>";
    html += _faRender10hFrequency(rows);
    html += _faRender10hRows(rows);
    panel.innerHTML = html;
    panel.scrollTop = 0;
  }};

  window.faGetFiltered = function() {{
    var q = (faSearchQuery || "").toLowerCase().trim();
    var list = _faAllShiftNames().map(function(n) {{
      return {{ name: n, years: {{}}, _shiftsOnly: true }};
    }}).filter(function(d) {{
      if (q && d.name.toLowerCase().indexOf(q) < 0) return false;
      if (faYearFilter !== "all" && !_faShiftRowsForYear(d.name).length) return false;
      return true;
    }});

    if (faCurrentSort === "arbeit") {{
      list.sort(function(a,b) {{
        var sa = _faShiftStatsForName(a.name);
        var sb = _faShiftStatsForName(b.name);
        return (sb.netto - sa.netto) || (sb.count - sa.count) || a.name.localeCompare(b.name, "de");
      }});
    }} else {{
      list.sort(function(a,b) {{ return a.name.localeCompare(b.name, "de"); }});
    }}
    return list;
  }};

  window.faBuildSidebarHighlight = function(activeName) {{
    var sidebar = document.getElementById("fa-sidebar-list");
    if (!sidebar) return;
    var filtered = window.faGetFiltered();

    var statsEl = document.getElementById("fa-stats");
    if (statsEl) {{
      var totalShifts = 0, totalNetto = 0, totalOver10 = 0, totalSick = 0, totalVacation = 0, totalVacationCredit = 0;
      filtered.forEach(function(d) {{
        var s = _faShiftStatsForName(d.name);
        var sick = _faSickEntries(d.name, faYearFilter, "all").length;
        var vacation = _faVacationEntries(d.name, faYearFilter, "all").length;
        totalShifts += s.count || 0;
        totalNetto += s.netto || 0;
        totalOver10 += s.over10 || 0;
        totalSick += sick || 0;
        totalVacation += vacation || 0;
        totalVacationCredit += vacation * 480;
      }});
      statsEl.innerHTML = "<b>" + filtered.length + "</b> Fahrer &nbsp;&middot;&nbsp; "
        + "<b>" + totalShifts + "</b> Schichten &nbsp;&middot;&nbsp; Krank <b style='color:#be123c;'>" + totalSick + "</b>"
        + " &nbsp;&middot;&nbsp; Urlaub <b style='color:#075985;'>" + totalVacation + "</b>"
        + " &nbsp;&middot;&nbsp; Σ Netto Schichten <b>" + _fmtMin(totalNetto) + "</b>"
        + " &nbsp;&middot;&nbsp; Urlaub +8h <b style='color:#075985;'>" + _fmtMin(totalVacationCredit) + "</b>"
        + " &nbsp;&middot;&nbsp; Σ inkl. Urlaub <b>" + _fmtMin(totalNetto + totalVacationCredit) + "</b>"
        + " &nbsp;&middot;&nbsp; &gt;10:00 Netto <b style='color:#be123c;'>" + totalOver10 + "</b>";
    }}

    var html = "";
    filtered.forEach(function(d) {{
      var s = _faShiftStatsForName(d.name);
      var sick = _faSickEntries(d.name, faYearFilter, "all").length;
      var vacation = _faVacationEntries(d.name, faYearFilter, "all").length;
      var active = d.name === activeName;
      var bg = active ? "#334155" : "#fff";
      var fg = active ? "#fff" : "#0b1220";
      var sub = active ? "rgba(255,255,255,.88)" : "#64748b";
      var badgeBg = active ? "rgba(255,255,255,.25)" : "#f1f5f9";
      var badgeFg = active ? "#fff" : "#334155";
      var overBg = active ? "#991b1b" : "#fee2e2";
      var overFg = active ? "#fecaca" : "#be123c";
      html += "<div data-fa-driver='" + faEsc(d.name) + "'"
        + " style='padding:10px 14px;cursor:pointer;border-bottom:1px solid #f1f5f9;background:" + bg + ";'>"
        + "<div style='font-weight:800;font-size:13px;color:" + fg + ";white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + faEsc(d.name) + "</div>"
        + "<div style='font-size:10px;color:" + sub + ";font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums;'>" + s.count + " Schichten" + (sick ? " · Krank " + sick : "") + (vacation ? " · Urlaub " + vacation + " (+" + _fmtMin(vacation * 480) + ")" : "") + " · Netto " + _fmtMin(s.netto + (vacation * 480)) + "</div>"
        + "<div style='display:flex;gap:3px;flex-wrap:wrap;margin-top:4px;'>"
        + "<span style='font-size:8px;font-weight:800;padding:1px 4px;border-radius:2px;background:" + badgeBg + ";color:" + badgeFg + ";'>" + s.count + " S</span>"
        + (sick ? "<span style='font-size:8px;font-weight:800;padding:1px 4px;border-radius:2px;background:" + (active ? "#991b1b" : "#fee2e2") + ";color:" + (active ? "#fecaca" : "#be123c") + ";'>K " + sick + "</span>" : "")
        + (vacation ? "<span style='font-size:8px;font-weight:800;padding:1px 4px;border-radius:2px;background:" + (active ? "#075985" : "#f1f5f9") + ";color:" + (active ? "#bae6fd" : "#075985") + ";'>U " + vacation + "</span>" : "")
        + (s.over10 ? "<span style='font-size:8px;font-weight:800;padding:1px 4px;border-radius:2px;background:" + overBg + ";color:" + overFg + ";'>&gt;10h " + s.over10 + "</span>" : "")
        + "</div></div>";
    }});
    sidebar.innerHTML = html || "<div style='padding:20px;color:#94a3b8;font-size:12px;text-align:center;'>Keine Tachograph-Schichten gefunden</div>";
    Array.prototype.slice.call(sidebar.querySelectorAll("[data-fa-driver]")).forEach(function(item) {{
      item.onclick = function() {{ window.faShowDetail(item.getAttribute("data-fa-driver")); }};
    }});

    if (!activeName && filtered.length) {{
      faSelectedName = filtered[0].name;
      window.faShowDetail(filtered[0].name);
    }} else if (activeName) {{
      faSelectedName = activeName;
    }}
  }};

  window.faPopulateYears = function() {{
    var allYears = [];
    Object.keys(TIMEREC_DATA || {{}}).forEach(function(n) {{
      (TIMEREC_DATA[n] || []).forEach(function(s) {{
        var m = (s.tag || "").match(/(\\d{{4}})$/);
        if (m && m[1] !== "2024" && allYears.indexOf(m[1]) === -1) allYears.push(m[1]);
      }});
    }});
    allYears.sort().reverse();
    var yrSel = document.getElementById("fa-year-sel");
    if (!yrSel) return;
    var current = yrSel.value;
    yrSel.innerHTML = allYears.map(function(y) {{ return "<option value='" + y + "'>" + y + "</option>"; }}).join("");
    if (allYears.length > 1) yrSel.insertAdjacentHTML("beforeend", "<option value='all'>Alle Jahre</option>");
    var curYr = String(new Date().getFullYear());
    if (current && (allYears.indexOf(current) !== -1 || current === "all")) yrSel.value = current;
    else if (allYears.indexOf(curYr) !== -1) yrSel.value = curYr;
    else if (allYears.length) yrSel.value = allYears[0];
    else yrSel.innerHTML = "<option value='all'>Keine Daten</option>";
    faYearFilter = yrSel.value || "all";
  }};

  window.faShowDetail = function(name) {{
    faSelectedName = name;
    if (window.FA_10H_MODE) {{
      window.FA_10H_SELECTED_DRIVER = name || "";
      faBuildSidebarHighlight(name);
      window.faShow10hTours(name);
      return;
    }}
    window.FA_10H_MODE = false;
    _faSet10hButton(false);
    faBuildSidebarHighlight(name);
    var panel = document.getElementById("fa-detail-panel");
    if (!panel) return;
    if (!faHasShifts(name)) {{
      panel.innerHTML = "<div style='color:#94a3b8;padding:40px;text-align:center;font-size:14px;'>Keine Schichten / Tachograph-Daten f&uuml;r diesen Fahrer.</div>";
      return;
    }}
    faRenderShifts(name, panel);
  }};

  window.faRender = function(q) {{
    faSearchQuery = q || "";
    if (!TIMEREC_DATA || !Object.keys(TIMEREC_DATA).length) {{
      var c = document.getElementById("fa-detail-panel");
      if (c) c.innerHTML = "<div style='color:#94a3b8;padding:40px;text-align:center;font-size:14px;'>Keine Tachograph-Daten vorhanden.<br>Bitte CSV <b>timerecording_v3*.csv</b> hochladen.</div>";
      var side = document.getElementById("fa-sidebar-list");
      if (side) side.innerHTML = "";
      var stats = document.getElementById("fa-stats");
      if (stats) stats.innerHTML = "";
      return;
    }}
    window.faPopulateYears();
    if (window.FA_10H_MODE) window.faShow10hTours(window.FA_10H_SELECTED_DRIVER || "");
    else window.faBuildSidebarHighlight(null);

  }};

  // ── Fahrer Übersicht: normale Timerecording-/Tachograph-Auswertung ──
  // Der Button darf NICHT auf die alte Excel-Fahrerauswertung zurückschalten.
  // Er schaltet nur aus der 10H-Ansicht zurück auf die normale Schichtenansicht.
  window.faShowFahrerUebersicht = function() {{
    window.FA_10H_MODE = false;
    window.FA_10H_SELECTED_DRIVER = "";
    _faSet10hButton(false);
    faBuildSidebarHighlight(null);
  }};

}})();
// ── /Schichten-Tab ─────────────────────────────────────────────────────────────

{zulage_js_code}
{verstoss_js_code}



</script>

</body>
</html>"""


def combine_html(instances: list, tel_json: str = "[]", sam_json: str = "[]", fa_json: str = "[]", zulage_json: str = "{}", zulage_xlsx_sonder: str = "", zulage_xlsx_fuengers: str = "", drittkunden_json: str = "[]", zulage_xlsx_drittkunden: str = "", fahrzeugwaesche_json: str = "[]", verstoss_json: str = '{"drivers":[],"total_violations":0}', spesen_json: str = '{"drivers":[],"months":[],"total_cost":0,"total_rows":0}', grosskunden_json: str = "[]", timerec_json: str = "{}", spediteure_json: str = '{"katalog":[],"fahrten":[]}', fahrerbewertung_json: str = '{"profile":"","event_types":[],"g_months":{},"g_ev":{},"drivers":[]}', versp_abfahrt_json: str = "{}", last_updated: str = "", generation_meta: dict | None = None) -> str:
    _combine_started = time.perf_counter()
    try:
        _logo_up = st.session_state.get("g_logo")
    except Exception:
        _logo_up = None
    logo_data_url = logo_file_to_data_uri(_logo_up) or load_logo_data_uri()

    """
    Bettet beliebig viele Suche+Druck-Paare (Instanzen) in eine HTML ein.
    Instanz-Wechsler im Topnav. BLP Druck ist intern (hidden iframe für FW-Daten).
    """
    js_parts = _dashboard_javascript_parts()
    spesen_js_code = js_parts['spesen']
    fa_js_code = js_parts['fa']
    zulage_js_code = js_parts['zulage']
    wash_js_code = js_parts['wash']
    wash_ranking_js_code = js_parts['wash_ranking']
    fw_graph_js_code = js_parts['fw_graph']
    verstoss_js_code = js_parts['verstoss']
    knapp_js_code = js_parts['knapp']
    sped_js_code = js_parts['sped']
    fabew_js_code = js_parts['fabew']
    bus_js_code = js_parts['bus']
    arzt_js_code = js_parts['arzt']
    versp_js_code = js_parts['versp']
    wa_js_code = js_parts['wa']




    # Rangliste + PDF pro Fahrer (separate Variable, Triple-Quote → keine Escape-Hölle)

    # ── Fahrzeugwäsche Graph (eigener raw-string, analog zur Verstoßauswertung) ─

    # ── Verstoßauswertung (eigener raw-string, damit keine Escape-Hölle) ─────

    documents_js_code = _build_pdf_documents_js()



    # Alle Instanzen als JS-Array vorbereiten
    instances_js = _build_instances_js(instances)


    # Sa-/So-Auswertung ausschließlich aus tatsächlichen TIMEREC-Schichten aufbauen.
    # Entscheidend ist immer der Anfangstag der Schicht. Touren-Excel ist hierfür
    # bewusst keine Datenquelle, damit geplante und tatsächlich gefahrene Einsätze
    # nicht vermischt werden.
    sam_json = _build_saturday_json_from_timerecording(timerec_json)








    # Große Zusatzdaten nicht mehr als unkomprimiertes JSON in die HTML schreiben.
    # Stattdessen werden alle Daten gemeinsam per zlib komprimiert und als Base64
    # eingebettet. Im Browser erfolgt das Entpacken einmalig beim Start.
    embedded_data_js = _build_embedded_data_js(
        fahrzeugwaesche_json=fahrzeugwaesche_json,
        tel_json=tel_json,
        sam_json=sam_json,
        fa_json=fa_json,
        fahrerbewertung_json=fahrerbewertung_json,
        zulage_json=zulage_json,
        drittkunden_json=drittkunden_json,
        verstoss_json=verstoss_json,
        spesen_json=spesen_json,
        grosskunden_json=grosskunden_json,
        timerec_json=timerec_json,
        spediteure_json=spediteure_json,
        versp_abfahrt_json=versp_abfahrt_json,
    )


    _meta = dict(generation_meta or {})
    _meta.setdefault("version", APP_DISPLAY_VERSION)
    _meta.setdefault("created_at", datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    _meta["html_bytes"] = "00000000000000000"
    _meta["generation_ms"] = "0000000000"
    generation_meta_json = json.dumps(_meta, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    _html = _render_dashboard_html(
        logo_data_url=logo_data_url,
        last_updated=last_updated,
        generation_meta_json=generation_meta_json,
        instances_js=instances_js,
        embedded_data_js=embedded_data_js,
        documents_js_code=documents_js_code,
        knapp_js_code=knapp_js_code,
        spesen_js_code=spesen_js_code,
        fa_js_code=fa_js_code,
        zulage_js_code=zulage_js_code,
        wash_js_code=wash_js_code,
        wash_ranking_js_code=wash_ranking_js_code,
        fw_graph_js_code=fw_graph_js_code,
        verstoss_js_code=verstoss_js_code,
        sped_js_code=sped_js_code,
        fabew_js_code=fabew_js_code,
        bus_js_code=bus_js_code,
        arzt_js_code=arzt_js_code,
        versp_js_code=versp_js_code,
        wa_js_code=wa_js_code,
        zulage_xlsx_sonder=zulage_xlsx_sonder,
        zulage_xlsx_fuengers=zulage_xlsx_fuengers,
        zulage_xlsx_drittkunden=zulage_xlsx_drittkunden,
    )
    _generation_ms = max(0, int(round((time.perf_counter() - _combine_started) * 1000)))
    _html_size = len(_html.encode("utf-8"))
    _html = _html.replace(
        '"html_bytes":"00000000000000000"',
        f'"html_bytes":"{_html_size:017d}"',
        1,
    ).replace(
        '"generation_ms":"0000000000"',
        f'"generation_ms":"{_generation_ms:010d}"',
        1,
    )
    return _html


def build_plane_zulagen_json(zulage_json_str: str, drittkunden_json_str: str, generated_at: str = "") -> bytes:
    """Erzeugt eine flache JSON-Datei fuer plane.php aus den bereits berechneten Zulagen.

    Die Plane muss dadurch keine Excel-Dateien lesen. Sie bekommt nur noch eine Datei:
    csv/zulagen.json
    """
    import json as _json
    import re as _re
    import datetime as _dt

    def _loads(value, fallback):
        try:
            return _json.loads(value or "")
        except Exception:
            return fallback

    def _amount(value) -> float:
        try:
            if value is None or value == "":
                return 0.0
            if isinstance(value, str):
                value = value.replace("€", "").replace(".", "").replace(",", ".").strip()
            return float(value)
        except Exception:
            return 0.0

    def _date_parts(raw: str):
        raw = str(raw or "").strip()
        m = _re.search(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
        if not m:
            return "", "", None, None
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = _dt.date(y, mo, d)
            return dt.strftime("%d.%m.%Y"), dt.isoformat(), dt.year, dt.month
        except Exception:
            return m.group(0), "", None, None

    def _kw(raw: str, iso: str) -> str:
        raw = str(raw or "")
        m = _re.search(r"KW\s*([0-9]{1,2})", raw, flags=_re.IGNORECASE)
        if m:
            return "KW" + m.group(1).zfill(2)
        if iso:
            try:
                return "KW" + str(_dt.date.fromisoformat(iso).isocalendar()[1]).zfill(2)
            except Exception:
                pass
        return ""

    entries = []
    zulage_data = _loads(zulage_json_str, {})
    dk_data = _loads(drittkunden_json_str, [])

    def _add_common(typ: str, fahrer: dict, tag: dict, betrag_key: str, extra: dict):
        raw_date = tag.get("datum") or tag.get("datum_raw") or ""
        datum, iso, jahr, monat = _date_parts(raw_date)
        if not datum:
            return
        amount = _amount(tag.get(betrag_key, tag.get("betrag", 0)))
        item = {
            "typ": typ,
            "fahrer": fahrer.get("name", ""),
            "persnr": fahrer.get("persnr", ""),
            "datum": datum,
            "datum_iso": iso,
            "datum_raw": raw_date,
            "jahr": jahr,
            "monat": monat,
            "kw": tag.get("kw") or _kw(raw_date, iso),
            "betrag": amount,
        }
        item.update({k: ("" if v is None else v) for k, v in extra.items()})
        entries.append(item)

    for typ_key, typ_label in (("sonder", "Sonderfahrzeug"), ("fuengers", "Füngers")):
        for monat in (zulage_data.get(typ_key, []) if isinstance(zulage_data, dict) else []):
            for fahrer in monat.get("fahrer", []):
                for tag in fahrer.get("tage", []):
                    if typ_key == "sonder":
                        _add_common(typ_label, fahrer, tag, "verdienst", {
                            "tour": tag.get("tour", ""),
                            "lkw": tag.get("lkw", ""),
                            "art": tag.get("art", ""),
                            "kommentar": tag.get("kommentar", ""),
                            "info": tag.get("info", ""),
                        })
                    else:
                        _add_common(typ_label, fahrer, tag, "verdienst", {
                            "tour": tag.get("tour", ""),
                            "lkw": tag.get("lkw", ""),
                            "art": tag.get("art", ""),
                            "kommentar": tag.get("kommentar", ""),
                            "info": tag.get("info", ""),
                        })

    if isinstance(dk_data, list):
        for monat in dk_data:
            for fahrer in monat.get("fahrer", []):
                for tag in fahrer.get("tage", []):
                    _add_common("Drittkunden", fahrer, tag, "zulage", {
                        "tour": tag.get("tour", ""),
                        "lkw": tag.get("lkw", ""),
                        "art": tag.get("art", ""),
                        "kommentar": tag.get("kommentar", ""),
                        "info": tag.get("info", ""),
                    })

    entries.sort(key=lambda e: (e.get("datum_iso") or "", e.get("fahrer") or "", e.get("typ") or ""), reverse=True)
    payload = {
        "schema": "plane_zulagen_v1",
        "generated_at": generated_at or _dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "source": "Streamlit Touren-Dateien",
        "entries": entries,
        "totals": {
            "entries": len(entries),
            "betrag": round(sum(_amount(e.get("betrag")) for e in entries), 2),
        },
    }
    return _json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

def generate_zulage_excel(zulage_json_str: str, tab: str = "sonder") -> bytes:
    """Formatierte Excel mit openpyxl – inkl. Persnr. und Zusammenfassung."""
    import io as _io, json as _j
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    try:
        data = _j.loads(zulage_json_str)
    except Exception:
        return None
    months = data.get(tab, [])
    if not months:
        return None
    wb = Workbook(); wb.remove(wb.active)
    def _s(st="thin",c="CCCCCC"): return Side(style=st, color=c)
    thin = Border(left=_s(),right=_s(),top=_s(),bottom=_s())
    med  = Border(left=_s("medium","1F4E78"),right=_s("medium","1F4E78"),top=_s("medium","1F4E78"),bottom=_s("medium","1F4E78"))
    gmed = Border(left=_s("medium","70AD47"),right=_s("medium","70AD47"),top=_s("medium","70AD47"),bottom=_s("medium","70AD47"))
    FT=PatternFill("solid",fgColor="1F4E78"); FH=PatternFill("solid",fgColor="D9E2F3")
    FN=PatternFill("solid",fgColor="4472C4"); FG=PatternFill("solid",fgColor="70AD47")
    FL=PatternFill("solid",fgColor="1F4E78"); FA=PatternFill("solid",fgColor="F8F9FA")
    FW=PatternFill("solid",fgColor="FFFFFF"); FS=PatternFill("solid",fgColor="EEF4FF")

    # Track summary: {(name, persnr): total}
    summary = {}

    for monat in months:
        ws = wb.create_sheet(title=monat["monat"][:31])
        is_s = (tab == "sonder")
        # Columns: Name | Persnr. | Datum | Tour | LKW | Art | Verdienst
        #      or: Name | Persnr. | Datum | Kommentar | Verdienst
        hdrs   = ["Name","Persnr.","Datum","Tour","LKW","Art","Verdienst"] if is_s else ["Name","Persnr.","Datum","Kommentar","Verdienst"]
        widths = [24,13,32,14,10,14,14]                                    if is_s else [24,13,32,40,14]
        nc = len(hdrs)

        # Title
        ws.append([monat["monat"]]+[""]*( nc-1)); tr=ws.max_row
        ws.merge_cells(start_row=tr,start_column=1,end_row=tr,end_column=nc)
        c=ws.cell(tr,1); c.font=Font(name="Calibri",bold=True,size=14,color="FFFFFF")
        c.fill=FT; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=med
        ws.row_dimensions[tr].height=28

        # Header
        ws.append(hdrs); hr=ws.max_row
        for col in range(1,nc+1):
            c=ws.cell(hr,col); c.font=Font(name="Calibri",bold=True,size=10,color="1F4E78")
            c.fill=FH; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=thin
        ws.row_dimensions[hr].height=22

        alt=False
        for fahrer in monat["fahrer"]:
            r0=ws.max_row+1
            # Accumulate for summary
            key=(fahrer["name"], fahrer["persnr"])
            summary[key] = summary.get(key, 0) + fahrer["gesamt"]

            for ti,tag in enumerate(fahrer["tage"]):
                if is_s:
                    tour=tag.get("tour","")
                    if not tour or tour=="zbv": tour="z.b.v."
                    row=[fahrer["name"] if ti==0 else "",
                         fahrer["persnr"] if ti==0 else "",
                         tag["datum"],tour,tag.get("lkw",""),tag.get("art",""),tag["verdienst"]]
                else:
                    row=[fahrer["name"] if ti==0 else "",
                         fahrer["persnr"] if ti==0 else "",
                         tag["datum"],tag.get("kommentar",""),tag["verdienst"]]
                ws.append(row); r=ws.max_row; fill=FA if alt else FW
                for col in range(1,nc+1):
                    if col in (1,2) and ti==0: continue   # styled after merge
                    c=ws.cell(r,col); iv=(col==nc)
                    c.font=Font(name="Calibri",size=10,color="16A34A" if iv else "2C3E50",bold=iv)
                    c.fill=fill; c.border=thin
                    c.alignment=Alignment(horizontal="right" if iv else ("center" if col==2 else "left"),vertical="center")
                    if iv and isinstance(c.value,(int,float)): c.number_format='#,##0.00 "€"'
                ws.row_dimensions[r].height=20; alt=not alt

            # Merge Name column over all tage
            ws.merge_cells(start_row=r0,start_column=1,end_row=ws.max_row,end_column=1)
            nc2=ws.cell(r0,1); nc2.font=Font(name="Calibri",bold=True,size=11,color="FFFFFF")
            nc2.fill=FN; nc2.alignment=Alignment(horizontal="left",vertical="center"); nc2.border=med

            # Merge Persnr column over all tage
            ws.merge_cells(start_row=r0,start_column=2,end_row=ws.max_row,end_column=2)
            pc=ws.cell(r0,2); pc.font=Font(name="Calibri",bold=False,size=10,color="FFFFFF")
            pc.fill=FN; pc.alignment=Alignment(horizontal="center",vertical="center"); pc.border=med

            # Gesamt row
            gv=[""]*nc; gv[nc-2]="Gesamt"; gv[nc-1]=fahrer["gesamt"]
            ws.append(gv); gr=ws.max_row
            ws.merge_cells(start_row=gr,start_column=1,end_row=gr,end_column=nc-1)
            for col in range(1,nc+1):
                c=ws.cell(gr,col); c.font=Font(name="Calibri",bold=True,size=11,color="FFFFFF")
                c.fill=FG; c.alignment=Alignment(horizontal="right",vertical="center"); c.border=gmed
                if col==nc and isinstance(c.value,(int,float)): c.number_format='#,##0.00 "€"'
            ws.row_dimensions[gr].height=20; alt=False

            # Spacer
            ws.append([""]*nc); ws.row_dimensions[ws.max_row].height=6

        # Monatsgesamt
        tv=[""]*nc; tv[nc-2]="Monatsgesamt"; tv[nc-1]=sum(f["gesamt"] for f in monat["fahrer"])
        ws.append(tv); tr2=ws.max_row
        ws.merge_cells(start_row=tr2,start_column=1,end_row=tr2,end_column=nc-1)
        for col in range(1,nc+1):
            c=ws.cell(tr2,col); c.font=Font(name="Calibri",bold=True,size=13,color="FFFFFF")
            c.fill=FL; c.alignment=Alignment(horizontal="right",vertical="center"); c.border=med
            if col==nc and isinstance(c.value,(int,float)): c.number_format='#,##0.00 "€"'
        ws.row_dimensions[tr2].height=26

        for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
        ws.freeze_panes="B3"   # freeze Name+Persnr columns + title+header rows

    # ── Zusammenfassung sheet ─────────────────────────────────────────────
    if summary:
        ws_sum = wb.create_sheet(title="Zusammenfassung", index=0)
        tab_label = "Sonderfahrzeuge" if tab=="sonder" else "F\u00fcngers"
        # Title
        ws_sum.append([f"Zusammenfassung \u2013 {tab_label}","",""])
        tr=ws_sum.max_row
        ws_sum.merge_cells(start_row=tr,start_column=1,end_row=tr,end_column=3)
        c=ws_sum.cell(tr,1); c.font=Font(name="Calibri",bold=True,size=14,color="FFFFFF")
        c.fill=FT; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=med
        ws_sum.row_dimensions[tr].height=30

        # Header
        ws_sum.append(["Name","Personalnummer","Gesamt"]); hr=ws_sum.max_row
        for col in range(1,4):
            c=ws_sum.cell(hr,col); c.font=Font(name="Calibri",bold=True,size=11,color="1F4E78")
            c.fill=FH; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=thin
        ws_sum.row_dimensions[hr].height=24

        # Data rows sorted by name
        total_all = 0
        for ri,(( name, persnr), total) in enumerate(sorted(summary.items(), key=lambda x:x[0][0])):
            fill = FA if ri%2==0 else FW
            ws_sum.append([name, persnr, total]); r=ws_sum.max_row
            total_all += total
            for col in range(1,4):
                c=ws_sum.cell(r,col); iv=(col==3)
                c.font=Font(name="Calibri",size=11,color="16A34A" if iv else "2C3E50",bold=iv)
                c.fill=fill; c.border=thin
                c.alignment=Alignment(horizontal="right" if iv else ("center" if col==2 else "left"),vertical="center")
                if iv: c.number_format='#,##0.00 "€"'
            ws_sum.row_dimensions[r].height=21

        # Gesamtsumme
        ws_sum.append(["","Gesamtsumme", total_all]); gr=ws_sum.max_row
        for col in range(1,4):
            c=ws_sum.cell(gr,col); c.font=Font(name="Calibri",bold=True,size=13,color="FFFFFF")
            c.fill=FL; c.border=med
            c.alignment=Alignment(horizontal="right",vertical="center")
            if col==3: c.number_format='#,##0.00 "€"'
        ws_sum.row_dimensions[gr].height=28

        ws_sum.column_dimensions["A"].width=26
        ws_sum.column_dimensions["B"].width=18
        ws_sum.column_dimensions["C"].width=16
        ws_sum.freeze_panes="A3"

    buf=_io.BytesIO(); wb.save(buf); return buf.getvalue()


_ZULAGE_PERSNR = {
    "Adler":{"Philipp":"00041450"},"Auer":{"Frank":"00020795"},
    "Batkowski":{"Tilo":"00046601"},"Benabbes":{"Badr":"00048980"},
    "Biebow":{"Thomas":"00042004"},"Blaesing":{"Elmar":"00049093"},
    "Bursian":{"Ronny":"00025714"},"Buth":{"Sven":"00046673"},
    "Boehnke":{"Marcel":"00020833"},"Carstensen":{"Martin":"00042412"},
    "Chege":{"Moses Gichuru":"00046106"},"Dammasch":{"Bernd":"00019297"},
    "Demuth":{"Harry":"00020796"},"Doroszkiewicz":{"Bogumil":"00049132"},
    "Duerr":{"Holger":"00039164"},"Effenberger":{"Sven":"00030807"},
    "Engel":{"Raymond":"00033429"},
    "Fechner":{"Danny":"00043696","Klaus":"00038278"},
    "Findeklee":{"Bernd":"00020804"},"Flint":{"Henryk":"00042414"},
    "Fuhlbruegge":{"Justin":"00046289"},"Gehrmann":{"Rayk":"00046702"},
    "Gheonea":{"Daniel-Costel":"00054489"},"Glanz":{"Bjoern":"00041914"},
    "Gnech":{"Torsten":"00018613"},"Greve":{"Nicole":"00040760"},
    "Guthmann":{"Fred":"00018328"},"Hagen":{"Andy":"00020271"},
    "Hartig":{"Sebastian":"00044120"},"Haus":{"David":"00046101"},
    "Heeser":{"Bernd":"00041916"},"Helm":{"Philipp":"00046685"},
    "Henkel":{"Bastian":"00048187"},"Holtz":{"Torsten":"00021159"},
    "Hirdina":{"Christopher":"00053400"},"Hintz":{"Leif":"00054808"},
    "Huebner":{"Christian":"00054531"},"Janikiewicz":{"Radoslaw":"00042159"},
    "Kelling":{"Jonas Ole":"00044140"},"Kleiber":{"Lutz":"00026255"},
    "Klemkow":{"Ralf":"00040634"},"Kollmann":{"Steffen":"00040988"},
    "Koenig":{"Heiko":"00036341"},"Krazewski":{"Cezary":"00039463"},
    "Krieger":{"Christian":"00049092"},"Krull":{"Benjamin":"00044192"},
    "Lange":{"Michael":"00035407"},"Lewandowski":{"Kamil":"00041044"},
    "Likoonski":{"Vladimir":"00044766"},"Linke":{"Erich":"00048377"},
    "Lefkih":{"Houssni":"00052293"},"Ludolf":{"Michel":"00048814"},
    "Laemmel":{"Patrick":"00052946"},"Marouni":{"Ayyoub":"00048986"},
    "Mintel":{"Mario":"00046686"},"Ohlenroth":{"Nadja":"00042114"},
    "Ohms":{"Torsten":"00019300"},"Okoth":{"Tedy Omondi":"00046107"},
    "Oszmian":{"Jacub":"00039464"},"Paul":{"Toralf":"00010490"},
    "Pabst":{"Torsten":"00021976"},"Pawlak":{"Bartosz":"00036381"},
    "Piepke":{"Torsten":"00021390"},"Plinke":{"Killian":"00044137"},
    "Pogodski":{"Enrico":"00046668"},"Postu":{"Mihai":"00051391"},
    "Quint":{"Stefan":"00035718"},"Rimba":{"Rimba Gona":"00046108"},
    "Rheinschmitt":{"Ronald":"00053356"},"Rudert":{"Kevin":"00052858"},
    "Rudolph":{"Yves":"00052855"},"Ruge":{"Fabian":"00054705"},
    "Sarwatka":{"Heiko":"00028747"},"Swietoslawski":{"Jacek":"00052955"},
    "Seredynski":{"Ireneusz":"00053452"},
    "Scheil":{"Eric-Rene":"00038579","Rene":"00020851"},
    "Schlichting":{"Michael":"00021452"},
    "Schlutt":{"Hubert":"00020880","Rene":"00042932"},
    "Schmieder":{"Steffen":"00046286"},"Schneider":{"Matthias":"00045495"},
    "Schulz":{"Julian":"00049130","Stephan":"00041558"},
    "Singh":{"Jagtah":"00040902"},"Stoltz":{"Thorben":"00040991"},
    "Thal":{"Jannic":"00046006"},"Tumanow":{"Vasilli":"00045019"},
    "Wachnowski":{"Klaus":"00026019"},"Wendel":{"Danilo":"00048994"},
    "Waschitschek":{"Detlef":"00020436"},"Wille":{"Rene":"00021393"},
    "Wisniewski":{"Krzysztof":"00046550"},"Zander":{"Jan":"00042454"},
    "Zosel":{"Ingo":"00026303"},"Strehlow":{"Yves":"00052855"},
}
_ZP_MONTHS_DE = ["","Januar","Februar","März","April","Mai","Juni",
                  "Juli","August","September","Oktober","November","Dezember"]
_ZP_DAYS = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"]


def _zp_norm(s):
    import unicodedata as _uc
    s=(s or "").strip().lower().replace(" "," ")
    s=_uc.normalize("NFKC",s).replace("-"," ")
    for a,b in [("ä","ae"),("ö","oe"),("ü","ue"),("ß","ss")]: s=s.replace(a,b)
    return " ".join(s.split())


def _zp_persnr(nn,vn):
    nk,vk=_zp_norm(nn),_zp_norm(vn)
    for ln,inner in _ZULAGE_PERSNR.items():
        if _zp_norm(ln)==nk:
            for fn,pn in inner.items():
                if _zp_norm(fn)==vk: return pn
            for fn,pn in inner.items():
                fn2=_zp_norm(fn)
                if vk.startswith(fn2) or fn2.startswith(vk): return pn
            return "Unbekannt"
    return "Unbekannt"


def _zp_art(val):
    try:
        v=int(str(val).split("-")[-1].strip())
        if v in [602,156]: return "Gigaliner"
        if v in [350,620]: return "Tandem"
        if v in [520,266,458,548,541,542,543,558]: return "Gliederzug"
    except Exception: pass
    return "Unbekannt"


def _zp_verdienst(lkw1,lkw2):
    total=0
    for v in [lkw1,lkw2]:
        try:
            if v is None: continue
            vi=int(str(v).split("-")[-1].strip())
            if vi in [602,156]: total+=40
            elif vi in [350,620,520,266,458,548,541,542,543,558]: total+=20
        except Exception: pass
    return total


def parse_zulage_excel(dateien: list) -> str:
    import json as _j
    from io import BytesIO
    import datetime as _dt
    sonder_map={}; fuengers_map={}
    cur_year=_dt.datetime.now().year
    for datei in dateien:
        try:
            datei.seek(0); df_h=pd.read_excel(BytesIO(datei.read()),sheet_name="Touren",header=0)
            datei.seek(0); df_nh=pd.read_excel(BytesIO(datei.read()),sheet_name="Touren",header=None)
            df_nh=df_nh.iloc[4:].reset_index(drop=True); df_nh.columns=range(df_nh.shape[1])
        except Exception: continue
        try:
            mask=df_h.iloc[:,13].astype(str).str.strip().str.upper()=="AZ"
            for _,row in df_h[mask].iterrows():
                datum=pd.to_datetime(row.iloc[14],errors="coerce")
                if pd.isna(datum) or datum.year < 2026: continue
                mk=f"{datum.month:02d}-{datum.year}"; ml=f"{_ZP_MONTHS_DE[datum.month]} {datum.year}"
                kw=int(datum.strftime("%W"))+1
                ds=f"{_ZP_DAYS[datum.weekday()]}, {datum.strftime('%d.%m.%Y')} (KW{kw})"
                def _c(x): return str(x).replace(" "," ").strip() if pd.notnull(x) else ""
                nn=_c(row.iloc[3] if len(row)>3 else ""); vn=_c(row.iloc[4] if len(row)>4 else "")
                if not nn or not vn or nn in("0","nan") or vn in("0","nan"):
                    nn=_c(row.iloc[6] if len(row)>6 else ""); vn=_c(row.iloc[7] if len(row)>7 else "")
                if not nn or not vn or nn in("0","nan") or vn in("0","nan"): continue
                lkw1=row.iloc[10] if len(row)>10 and pd.notnull(row.iloc[10]) else None
                lkw2=row.iloc[11] if len(row)>11 and pd.notnull(row.iloc[11]) else None
                tour=str(row.iloc[0]).strip() if pd.notnull(row.iloc[0]) else ""
                verd=_zp_verdienst(lkw1,lkw2)
                if verd==0: continue
                if mk not in sonder_map: sonder_map[mk]={"label":ml,"fahrer":{}}
                sonder_map[mk]["fahrer"].setdefault((nn,vn),[]).append(
                    {"datum":ds,"tour":tour,"lkw":str(lkw2 or lkw1 or "").strip(),"art":_zp_art(lkw2 or lkw1),"verdienst":verd})
        except Exception: pass
        try:
            for _,row in df_nh.iterrows():
                komm=str(row[15]).strip() if 15 in row and pd.notnull(row[15]) else ""
                if "füngers" not in komm.lower() and "fuengers" not in komm.lower(): continue
                nn=str(row[3]).replace(" "," ").strip() if 3 in row and pd.notnull(row[3]) else ""
                vn=str(row[4]).replace(" "," ").strip() if 4 in row and pd.notnull(row[4]) else ""
                if not nn or not vn or nn in("0","nan") or vn in("0","nan"): continue
                datum=pd.to_datetime(row[14] if 14 in row else None,errors="coerce")
                if pd.isna(datum) or datum.year < 2026: continue
                mk=f"{datum.month:02d}-{datum.year}"; ml=f"{_ZP_MONTHS_DE[datum.month]} {datum.year}"
                kw=datum.isocalendar()[1]; ds=datum.strftime("%d.%m.%Y")+f" (KW {kw})"
                if mk not in fuengers_map: fuengers_map[mk]={"label":ml,"fahrer":{}}
                fuengers_map[mk]["fahrer"].setdefault((nn,vn),[]).append(
                    {"datum":ds,"kommentar":komm,"verdienst":20})
        except Exception: pass
    def _build(dm):
        res=[]
        for mk in sorted(dm.keys()):
            e=dm[mk]; fl=[]
            for (nn,vn),tage in sorted(e["fahrer"].items()):
                fl.append({"name":f"{vn} {nn}","persnr":_zp_persnr(nn,vn),
                           "gesamt":sum(t["verdienst"] for t in tage),"tage":tage})
            fl.sort(key=lambda x:x["name"]); res.append({"monat":e["label"],"fahrer":fl})
        return res
    return _j.dumps({"sonder":_build(sonder_map),"fuengers":_build(fuengers_map)},ensure_ascii=False)



DRITTKUNDEN_KEYWORDS = [
    "ahaus", "borkholzhausen", "glandorf", "optifair", "opti fair",
    "edv", "edv fleisch", "elfering", "elfering ahaus"
]


def _dk_check(comment):
    if isinstance(comment, str):
        c = comment.lower()
        return any(k in c for k in DRITTKUNDEN_KEYWORDS)
    return False


def parse_drittkunden_excel(dateien: list) -> str:
    """Liest Touren-Excels auf Drittkunden-Zulage (Ahaus etc.)."""
    import json as _j
    from io import BytesIO
    import datetime as _dt
    entries = []
    _months_de = ["","Januar","Februar","März","April","Mai","Juni",
                  "Juli","August","September","Oktober","November","Dezember"]
    for datei in dateien:
        try:
            datei.seek(0)
            df = pd.read_excel(BytesIO(datei.read()), sheet_name=0, header=None)
            df = df.iloc[4:].reset_index(drop=True)
            df.columns = range(df.shape[1])
        except Exception:
            continue
        for _, row in df.iterrows():
            kommentar = str(row[15]).strip() if 15 in row and pd.notna(row[15]) else ""
            if not _dk_check(kommentar):
                continue
            datum = pd.to_datetime(row[14] if 14 in row else None, errors="coerce")
            if pd.isna(datum) or datum.year < 2026:
                continue
            lkw = str(row[11]).strip() if 11 in row and pd.notna(row[11]) else ""
            info = str(row[1]).strip() if 1 in row and pd.notna(row[1]) else ""
            kw = f"KW{datum.isocalendar()[1]}"
            monat_label = f"{_months_de[datum.month]} {datum.year}"
            datum_str = datum.strftime("%d.%m.%Y")
            # Two driver pairs: D/E (3/4) and G/H (6/7)
            fahrer_paare = []
            if len(row) > 4: fahrer_paare.append((row[3], row[4]))
            if len(row) > 7: fahrer_paare.append((row[6], row[7]))
            seen = set()
            for nn_raw, vn_raw in fahrer_paare:
                if pd.isna(nn_raw) and pd.isna(vn_raw):
                    continue
                nn = str(nn_raw).strip() if pd.notna(nn_raw) else ""
                vn = str(vn_raw).strip() if pd.notna(vn_raw) else ""
                if not nn:
                    continue
                name = f"{nn}, {vn}".strip().rstrip(",")
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                zulage = 0 if "zippel" in nn.lower() else 20
                persnr = _zp_persnr(nn, vn)
                entries.append({
                    "name": name,
                    "persnr": persnr,
                    "datum": datum_str,
                    "kw": kw,
                    "lkw": lkw,
                    "zulage": zulage,
                    "info": info,
                    "monat": monat_label,
                    "monat_sort": f"{datum.year}{datum.month:02d}",
                })
    # Group by monat
    monat_map = {}
    for e in entries:
        mk = e["monat_sort"]
        monat_map.setdefault(mk, {"monat": e["monat"], "eintraege": []})
        monat_map[mk]["eintraege"].append(e)
    # Per month: group by name
    result = []
    for mk in sorted(monat_map.keys()):
        m = monat_map[mk]
        name_map = {}
        for e in m["eintraege"]:
            name_map.setdefault(e["name"], []).append(e)
        fahrer = []
        for name in sorted(name_map.keys()):
            tage = sorted(name_map[name], key=lambda x: x["datum"])
            gesamt = sum(t["zulage"] for t in tage)
            fahrer.append({"name": name, "persnr": tage[0].get("persnr", "Unbekannt") if tage else "Unbekannt", "gesamt": gesamt, "tage": tage})
        result.append({"monat": m["monat"], "fahrer": fahrer})
    return _j.dumps(result, ensure_ascii=False)


def generate_drittkunden_excel(drittkunden_json_str: str) -> bytes:
    """Formatierte Excel für Drittkunden-Zulage."""
    import io as _io, json as _j
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    try:
        months = _j.loads(drittkunden_json_str)
    except Exception:
        return None
    if not months:
        return None
    wb = Workbook(); wb.remove(wb.active)
    def _s(st="thin",c="CCCCCC"): return Side(style=st, color=c)
    thin = Border(left=_s(),right=_s(),top=_s(),bottom=_s())
    med  = Border(left=_s("medium","1F4E78"),right=_s("medium","1F4E78"),top=_s("medium","1F4E78"),bottom=_s("medium","1F4E78"))
    gmed = Border(left=_s("medium","70AD47"),right=_s("medium","70AD47"),top=_s("medium","70AD47"),bottom=_s("medium","70AD47"))
    FT=PatternFill("solid",fgColor="1F4E78"); FH=PatternFill("solid",fgColor="D9E2F3")
    FN=PatternFill("solid",fgColor="4472C4"); FG=PatternFill("solid",fgColor="70AD47")
    FL=PatternFill("solid",fgColor="1F4E78"); FA=PatternFill("solid",fgColor="F8F9FA")
    FW=PatternFill("solid",fgColor="FFFFFF")
    hdrs   = ["Name","Datum","KW","LKW","Zulage","Info"]
    widths = [26,14,8,14,12,30]
    nc = len(hdrs)
    # Summary sheet first
    summary = {}
    for monat in months:
        for f in monat["fahrer"]:
            summary[f["name"]] = summary.get(f["name"], 0) + f["gesamt"]
    ws_sum = wb.create_sheet(title="Zusammenfassung", index=0)
    ws_sum.append([f"Zusammenfassung – Drittkunden","",""])
    tr=ws_sum.max_row; ws_sum.merge_cells(start_row=tr,start_column=1,end_row=tr,end_column=3)
    c=ws_sum.cell(tr,1); c.font=Font(name="Calibri",bold=True,size=14,color="FFFFFF")
    c.fill=FT; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=med
    ws_sum.row_dimensions[tr].height=30
    ws_sum.append(["Name","Personalnummer","Gesamt"]); hr=ws_sum.max_row
    for col in range(1,4):
        c=ws_sum.cell(hr,col); c.font=Font(name="Calibri",bold=True,size=11,color="1F4E78")
        c.fill=FH; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=thin
    ws_sum.row_dimensions[hr].height=24
    total_all=0
    for ri,(name,total) in enumerate(sorted(summary.items())):
        fill=FA if ri%2==0 else FW; ws_sum.append([name,"",total]); r=ws_sum.max_row; total_all+=total
        for col in range(1,4):
            c=ws_sum.cell(r,col); iv=(col==3)
            c.font=Font(name="Calibri",size=11,color="16A34A" if iv else "2C3E50",bold=iv)
            c.fill=fill; c.border=thin
            c.alignment=Alignment(horizontal="right" if iv else "left",vertical="center")
            if iv: c.number_format='#,##0.00 "€"'
        ws_sum.row_dimensions[r].height=21
    ws_sum.append(["","Gesamtsumme",total_all]); gr=ws_sum.max_row
    for col in range(1,4):
        c=ws_sum.cell(gr,col); c.font=Font(name="Calibri",bold=True,size=13,color="FFFFFF")
        c.fill=FL; c.border=med; c.alignment=Alignment(horizontal="right",vertical="center")
        if col==3: c.number_format='#,##0.00 "€"'
    ws_sum.row_dimensions[gr].height=28
    ws_sum.column_dimensions["A"].width=26; ws_sum.column_dimensions["B"].width=18; ws_sum.column_dimensions["C"].width=16
    # Month sheets
    for monat in months:
        ws = wb.create_sheet(title=monat["monat"][:31])
        msum = sum(f["gesamt"] for f in monat["fahrer"])
        ws.append([monat["monat"]]+[""]*( nc-1)); tr=ws.max_row
        ws.merge_cells(start_row=tr,start_column=1,end_row=tr,end_column=nc)
        c=ws.cell(tr,1); c.font=Font(name="Calibri",bold=True,size=14,color="FFFFFF")
        c.fill=FT; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=med
        ws.row_dimensions[tr].height=28
        ws.append(hdrs); hr=ws.max_row
        for col in range(1,nc+1):
            c=ws.cell(hr,col); c.font=Font(name="Calibri",bold=True,size=10,color="1F4E78")
            c.fill=FH; c.alignment=Alignment(horizontal="center",vertical="center"); c.border=thin
        ws.row_dimensions[hr].height=22
        alt=False
        for fahrer in monat["fahrer"]:
            r0=ws.max_row+1
            for ti,tag in enumerate(fahrer["tage"]):
                row=[fahrer["name"] if ti==0 else "",tag["datum"],tag["kw"],tag["lkw"],tag["zulage"],tag.get("info","")]
                ws.append(row); r=ws.max_row; fill=FA if alt else FW
                for col in range(1,nc+1):
                    if col==1 and ti==0: continue
                    c=ws.cell(r,col); iv=(col==5)
                    c.font=Font(name="Calibri",size=10,color="16A34A" if iv else "2C3E50",bold=iv)
                    c.fill=fill; c.border=thin
                    c.alignment=Alignment(horizontal="right" if iv else "left",vertical="center")
                    if iv and isinstance(c.value,(int,float)): c.number_format='#,##0.00 "€"'
                ws.row_dimensions[r].height=20; alt=not alt
            ws.merge_cells(start_row=r0,start_column=1,end_row=ws.max_row,end_column=1)
            nc2=ws.cell(r0,1); nc2.font=Font(name="Calibri",bold=True,size=11,color="FFFFFF")
            nc2.fill=FN; nc2.alignment=Alignment(horizontal="left",vertical="center"); nc2.border=med
            gv=[""]*nc; gv[nc-2]="Gesamt"; gv[nc-1]=fahrer["gesamt"]
            ws.append(gv); gr=ws.max_row
            ws.merge_cells(start_row=gr,start_column=1,end_row=gr,end_column=nc-1)
            for col in range(1,nc+1):
                c=ws.cell(gr,col); c.font=Font(name="Calibri",bold=True,size=11,color="FFFFFF")
                c.fill=FG; c.alignment=Alignment(horizontal="right",vertical="center"); c.border=gmed
                if col==nc and isinstance(c.value,(int,float)): c.number_format='#,##0.00 "€"'
            ws.row_dimensions[gr].height=20; alt=False
            ws.append([""]*nc); ws.row_dimensions[ws.max_row].height=6
        tv=[""]*nc; tv[nc-2]="Monatsgesamt"; tv[nc-1]=msum
        ws.append(tv); tr2=ws.max_row
        ws.merge_cells(start_row=tr2,start_column=1,end_row=tr2,end_column=nc-1)
        for col in range(1,nc+1):
            c=ws.cell(tr2,col); c.font=Font(name="Calibri",bold=True,size=13,color="FFFFFF")
            c.fill=FL; c.alignment=Alignment(horizontal="right",vertical="center"); c.border=med
            if col==nc and isinstance(c.value,(int,float)): c.number_format='#,##0.00 "€"'
        ws.row_dimensions[tr2].height=26
        for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
        ws.freeze_panes="A3"
    buf=_io.BytesIO(); wb.save(buf); return buf.getvalue()


def parse_telefon_excel(up) -> str:
    """Liest Telefonnummern.xlsx (Sheet 'aktuell') und gibt JSON-String zurück."""
    import json as _json
    try:
        df = pd.read_excel(up, sheet_name="aktuell", dtype=str, engine=EXCEL_READ_ENGINE)
        df.columns = ["name","vorname","vorwahl","nummer","mail","gruppe"]
        df = df.fillna("")
        groups, current_group, current_entries = [], "Eigene Fahrer", []
        # itertuples + Positions-Zugriff statt iterrows mit r["..."] —
        # bei langen Telefonlisten klar schneller, gleiche Logik.
        for tup in df.itertuples(index=False, name=None):
            name    = tup[0].strip()
            vorname = tup[1].strip()
            vorwahl = tup[2].strip()
            nummer  = tup[3].strip()
            mail    = tup[4].strip()
            gruppe  = tup[5].strip()
            if not name and not vorname and not vorwahl and not nummer:
                if current_entries:
                    groups.append({"gruppe": current_group, "personen": current_entries})
                current_entries = []
                continue
            if gruppe:
                if current_entries:
                    groups.append({"gruppe": current_group, "personen": current_entries})
                    current_entries = []
                current_group = gruppe
            tel = ""
            if vorwahl and vorwahl.lower() not in ("nan","n.a.",""):
                tel = vorwahl.strip()
                if nummer and nummer.lower() not in ("nan","n.a.",""):
                    tel += " " + nummer.strip()
            elif nummer and nummer.lower() not in ("nan","n.a.",""):
                tel = nummer.strip()
            else:
                tel = "n.a."
            vname = " ".join(filter(None, [vorname, name]))
            if not vname.strip(): continue
            current_entries.append({
                "name": vname,
                "tel":  tel,
                "mail": mail if mail.lower() not in ("nan","") else ""
            })
        if current_entries:
            groups.append({"gruppe": current_group, "personen": current_entries})
        return _json.dumps(groups, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        st.warning(f"Telefonliste konnte nicht gelesen werden: {e}")
        return "[]"




def parse_spesen_csv(uploaded_file) -> str:
    """Parst die Reisekosten-/Spesen-CSV und aggregiert nur Steuerfreiheit + steuerpflichtigen Betrag pro Fahrer."""
    import csv as _csv
    from io import StringIO as _SIO

    empty = json.dumps({"drivers": [], "months": [], "total_cost": 0, "total_tax_free": 0, "total_taxable": 0, "total_rows": 0}, ensure_ascii=False)
    payload = read_upload_bytes(uploaded_file)
    if not payload:
        return empty

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = payload.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        return empty
    if text.startswith("\ufeff"):
        text = text[1:]

    def _clean(v):
        s = "" if v is None else str(v).strip()
        return "" if s.lower() in ("nan", "none", "null") else s

    def _money(v) -> float:
        s = _clean(v)
        if not s:
            return 0.0
        s = s.replace("€", "").replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        try:
            return round(float(s), 2)
        except Exception:
            return 0.0

    def _duration(v) -> int:
        s = _clean(v)
        if not s:
            return 0
        if ":" in s:
            parts = s.split(":")
            try:
                h = int(float(parts[0] or 0))
                m = int(float(parts[1] or 0)) if len(parts) > 1 else 0
                return h * 60 + m
            except Exception:
                return 0
        try:
            return int(round(float(s.replace(".", "").replace(",", "."))))
        except Exception:
            return 0

    def _dt(v):
        s = _clean(v).strip('"')
        if not s:
            return None
        s19 = s[:19]
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.datetime.strptime(s19, fmt)
            except Exception:
                pass
        return None

    def _date_from_value(v):
        s = _clean(v).strip('"')
        if not s:
            return None
        s16 = s[:16]
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
            try:
                return datetime.datetime.strptime(s16, fmt).date()
            except Exception:
                pass
        return None

    def _time_label(dt):
        return dt.strftime("%H:%M") if dt else ""

    def _duration_label(minutes):
        minutes = int(minutes or 0)
        if minutes <= 0:
            return "0 Minuten"
        h, m = divmod(minutes, 60)
        if h and m:
            return f"{h} Std. {m:02d} Min."
        if h:
            return f"{h} Std."
        return f"{m} Min."

    def _month_label(value: str) -> str:
        names = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        try:
            y, m = value.split("-", 1)
            return f"{names[int(m)]} {y}"
        except Exception:
            return value

    def _weekday(d):
        names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        try:
            return names[d.weekday()]
        except Exception:
            return ""

    try:
        reader = _csv.DictReader(_SIO(text), delimiter=";", quotechar='"')
    except Exception as e:
        st.warning(f"Spesen-CSV konnte nicht gelesen werden: {e}")
        return empty

    by_driver = {}
    total_rows = 0
    months_seen = set()

    for row in reader:
        total_rows += 1
        norm = {(k or "").strip().lstrip("\ufeff").upper(): v for k, v in row.items()}
        name = _clean(norm.get("DRIVER"))
        if not name:
            continue

        start_dt = _dt(norm.get("START"))
        end_dt = _dt(norm.get("END"))
        date_obj = _date_from_value(norm.get("DATE"))
        if date_obj is None and start_dt is not None:
            date_obj = start_dt.date()
        if date_obj is None:
            continue

        date_iso = date_obj.strftime("%Y-%m-%d")
        month = date_obj.strftime("%Y-%m")
        months_seen.add(month)
        duration_minutes = _duration(norm.get("DURATION"))
        # Nur diese beiden Spalten werden fuer die Spesenberechnung verwendet:
        # Steuerfreiheit -> TAX_FREE_AMOUNT
        # Steuerpflichtiger Betrag -> TAXABLE_AMOUNT
        tax_free = _money(norm.get("TAX_FREE_AMOUNT"))
        taxable = _money(norm.get("TAXABLE_AMOUNT"))
        total = round(tax_free + taxable, 2)

        entry = {
            "date_iso": date_iso,
            "date_sort": date_iso + " " + (_time_label(start_dt) or "00:00"),
            "date_label": date_obj.strftime("%d.%m.%Y"),
            "weekday": _weekday(date_obj),
            "month": month,
            "start": _clean(norm.get("START")),
            "end": _clean(norm.get("END")),
            "start_time": _time_label(start_dt),
            "end_time": _time_label(end_dt),
            "duration_minutes": duration_minutes,
            "duration_label": _duration_label(duration_minutes),
            "vehicles": _clean(norm.get("VEHICLES")),
            "region": _clean(norm.get("REGION")),
            "tax_free": tax_free,
            "taxable": taxable,
            # Legacy-Aliase fuer vorhandene Frontend-Funktionen:
            # meal = steuerfrei, night = steuerpflichtig
            "meal": tax_free,
            "night": taxable,
            "total": total,
            "pos_start": _clean(norm.get("POS_START")),
            "pos_end": _clean(norm.get("POS_END")),
            "absence": _clean(norm.get("ABSENCE")),
            "employee_nr": _clean(norm.get("EMPLOYEE_NR")),
        }

        if name not in by_driver:
            by_driver[name] = {
                "name": name,
                "employee_nr": _clean(norm.get("EMPLOYEE_NR")),
                "rows": [],
                "tax_free": 0.0,
                "taxable": 0.0,
                # Legacy-Aliase fuer vorhandene Frontend-Funktionen:
                "meal": 0.0,
                "night": 0.0,
                "gesamt": 0.0,
                "duration_minutes": 0,
                "fahrtage": 0,
            }
        d = by_driver[name]
        if not d.get("employee_nr") and entry.get("employee_nr"):
            d["employee_nr"] = entry["employee_nr"]
        d["rows"].append(entry)
        d["tax_free"] = round(d["tax_free"] + tax_free, 2)
        d["taxable"] = round(d["taxable"] + taxable, 2)
        d["meal"] = d["tax_free"]      # Legacy-Alias: steuerfrei
        d["night"] = d["taxable"]      # Legacy-Alias: steuerpflichtig
        d["gesamt"] = round(d["gesamt"] + total, 2)
        d["duration_minutes"] += duration_minutes
        if duration_minutes > 0 or entry.get("start_time") or entry.get("end_time") or entry.get("vehicles"):
            d["fahrtage"] += 1

    drivers = []
    for d in by_driver.values():
        d["rows"].sort(key=lambda r: r.get("date_sort", ""))
        drivers.append(d)
    drivers.sort(key=lambda d: (-float(d.get("gesamt", 0)), d.get("name", "")))

    months = [{"value": m, "label": _month_label(m)} for m in sorted(months_seen, reverse=True)]
    total_tax_free = round(sum(float(d.get("tax_free", 0)) for d in drivers), 2)
    total_taxable = round(sum(float(d.get("taxable", 0)) for d in drivers), 2)
    total_cost = round(total_tax_free + total_taxable, 2)

    return json.dumps({
        "drivers": drivers,
        "months": months,
        "total_cost": total_cost,
        "total_tax_free": total_tax_free,
        "total_taxable": total_taxable,
        # Legacy-Aliase fuer vorhandene Frontend-Funktionen:
        "total_meal": total_tax_free,
        "total_night": total_taxable,
        "total_rows": total_rows,
    }, ensure_ascii=False)


def parse_fahrerbewertung_json(uploaded_file) -> str:
    """Liest die Fahrerbewertungs-Rohdaten (d_rohdaten.json) und verdichtet sie zu einer
    kompakten Zusammenfassung pro Fahrer.

    Die Roh-JSON ist sehr groß (zehntausende Einzel-Events). Damit das erzeugte
    suche.html nicht aufgebläht wird, werden hier NUR Aggregate eingebettet:
    Noten, Ereigniszähler je Art und Monat sowie Kennzahlen. Die Einzel-Events
    selbst werden NICHT übernommen.
    """
    import json as _json

    try:
        raw = uploaded_file.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        data = _json.loads(raw)
    except Exception:
        return _json.dumps({"profile": "", "event_types": [], "g_months": {},
                            "g_ev": {}, "drivers": []}, ensure_ascii=False)

    drivers = data.get("drivers", []) if isinstance(data, dict) else []
    EVT = ["BRAKE", "CURVE", "OVERSPEED", "SPEEDUP"]
    out_drivers = []
    g_months: dict = {}
    g_ev = {t: 0 for t in EVT}
    profile = ""

    def _r1(x):
        return round(x, 1) if isinstance(x, (int, float)) else x

    for d in drivers:
        if not isinstance(d, dict):
            continue
        g = d.get("grades") or {}
        rp = d.get("rating_profiles") or []
        if rp and not profile:
            profile = rp[0]
        sc = g.get("sub_critical_grades") or {}
        se = g.get("sub_economic_grades") or {}
        sd = g.get("sub_difficulty_grades") or {}

        ev = {t: 0 for t in EVT}
        months: dict = {}
        for e in (d.get("critical_events") or []):
            if not isinstance(e, dict):
                continue
            t = e.get("type")
            if t in ev:
                ev[t] += 1
            st = str(e.get("start_time") or "")
            mk = st[:7] if len(st) >= 7 and st[4] == "-" else None
            if mk:
                mm = months.setdefault(mk, {"_t": 0})
                if t:
                    mm[t] = mm.get(t, 0) + 1
                mm["_t"] += 1
                gm = g_months.setdefault(mk, {"_t": 0})
                if t:
                    gm[t] = gm.get(t, 0) + 1
                gm["_t"] += 1
        for t in EVT:
            g_ev[t] += ev[t]

        fuel = None
        for fi in (d.get("fms_info") or []):
            if isinstance(fi, dict) and fi.get("vehicle") == "__ALL__":
                fuel = fi.get("avg_used_fuel")
                break

        dt = d.get("driving_time") or 0
        subs = {k: v for k, v in {
            "overspeed": sc.get("overspeed"), "brake": sc.get("brake"),
            "speedup": sc.get("speedup"), "curves": sc.get("curves"),
            "foresight": sc.get("foresight_driving"),
            "idle": se.get("idle"), "avg_speed": se.get("avg_speed"),
            "wearfree_brake": se.get("wearfree_brake"), "cruise": se.get("cruisecontrol"),
            "altitude": sd.get("altitude"), "street_type": sd.get("street_type"),
            "stopps": sd.get("count_stopps"),
        }.items() if v is not None}

        out_drivers.append({
            "name": d.get("name", ""),
            "persnr": str(d.get("employee_number", "") or ""),
            "grade": g.get("main_grade"),
            "g_crit": g.get("main_critical_grade"),
            "g_eco": g.get("main_economic_grade"),
            "g_diff": g.get("main_difficulty_grade"),
            "subs": subs,
            "dist": d.get("distance", 0) or 0,
            "hours": round(dt / 3600.0, 1) if dt else 0,
            "trips": d.get("count_evaluated_trips", 0) or 0,
            "fuel": _r1(fuel),
            "ev": ev,
            "evt": sum(ev.values()),
            "brake": d.get("total_brake_count", 0) or 0,
            "curve": d.get("total_curve_count", 0) or 0,
            "speedup": d.get("total_speedup_count", 0) or 0,
            "months": months,
            "vehicles": d.get("vehicles") or [],
        })

    out_drivers.sort(key=lambda r: (r.get("name") or "").lower())
    return _json.dumps({
        "profile": profile,
        "event_types": EVT,
        "g_months": g_months,
        "g_ev": g_ev,
        "drivers": out_drivers,
    }, ensure_ascii=False, separators=(",", ":"))


def parse_verstoss_csv(uploaded_file) -> str:
    """Parst die Digitacho-Verstoßauswertungs-CSV und aggregiert Verstöße und Bußgelder pro Fahrer.

    Die Bußgeld-Spalten werden robust gelesen. Akzeptiert werden zum Beispiel
    160, 160 €, 160,00, 1.520 € oder 1.520,50 €.
    """
    import csv as _csv
    from io import StringIO as _SIO

    empty = json.dumps({
        "drivers": [],
        "total_violations": 0,
        "total_driver_penalty": 0,
        "total_company_penalty": 0,
        "first_violation_month": "",
        "last_violation_month": "",
    }, ensure_ascii=False)

    payload = read_upload_bytes(uploaded_file)
    if not payload:
        return empty

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = payload.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        return empty

    if text.startswith("\ufeff"):
        text = text[1:]

    try:
        reader = _csv.DictReader(_SIO(text), delimiter=";", quotechar='"')
    except Exception as e:
        st.warning(f"Verstoß-CSV konnte nicht gelesen werden: {e}")
        return empty

    def _clean(v):
        return str(v or "").replace("\xa0", " ").strip()

    def _norm_key(v: str) -> str:
        s = _clean(v).lower().replace("ß", "ss")
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]+", "", s)

    def _row_get(row_norm: dict, *aliases: str) -> str:
        for alias in aliases:
            key = _norm_key(alias)
            if key in row_norm:
                return row_norm.get(key)
        return ""

    def _to_int(v):
        """Robust fuer Minuten/Werte: '48', '48 Min.', '1 Std. 15 Min.' -> Minuten."""
        s = _clean(v)
        if not s or s in ("—", "-", "–"):
            return 0
        h = re.search(r"(\d+)\s*(?:std|stunde|stunden|h)\b", s, flags=re.I)
        m = re.search(r"(\d+)\s*(?:min|minute|minuten|m)\b", s, flags=re.I)
        if h or m:
            return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
        m = re.search(r"-?\d+", s.replace(".", ""))
        return int(m.group(0)) if m else 0

    def _to_money(v):
        """Robust fuer Euro-Werte: '160', '160 €', '160,00', '1.520 €'."""
        s = _clean(v)
        if not s or s in ("—", "-", "–"):
            return 0
        s = re.sub(r"[^0-9,\.\-]", "", s)
        if not s or s in ("-", ",", "."):
            return 0
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        else:
            if s.count(".") > 1:
                s = s.replace(".", "")
            elif re.match(r"^-?\d{1,3}\.\d{3}$", s):
                s = s.replace(".", "")
        try:
            value = float(s)
        except Exception:
            return 0
        return int(value) if value.is_integer() else round(value, 2)

    by_driver = {}
    date_re = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")

    for row in reader:
        row_norm = {_norm_key(k): v for k, v in (row or {}).items()}

        name = _clean(_row_get(row_norm, "DRIVER", "Fahrer", "Name", "Driver Name"))
        if not name:
            continue

        start = _clean(_row_get(row_norm, "START_DATE", "Start Date", "Beginn", "Start", "Von"))
        end = _clean(_row_get(row_norm, "END_DATE", "End Date", "Ende", "Bis"))
        target = _to_int(_row_get(row_norm, "TARGET", "Soll", "Grenzwert", "Limit"))
        ist = _to_int(_row_get(row_norm, "IS", "Ist", "Tatsaechlich", "Tatsächlich"))
        diff = _to_int(_row_get(row_norm, "DIFF", "Differenz", "Ueberschreitung", "Überschreitung"))
        viol = _clean(_row_get(row_norm, "VIOLATION", "Verstoß", "Verstoss", "Verstoßart", "Verstossart"))
        law = _clean(_row_get(row_norm, "LAW", "Gesetz", "Rechtsgrundlage", "Paragraph"))
        dp = _to_money(_row_get(
            row_norm,
            "DRIVER_PENALTY", "Driver Penalty", "Bußgeld Fahrer", "Bussgeld Fahrer",
            "Fahrer Bußgeld", "Fahrer Bussgeld", "Bußgeld Fahrer EUR", "Bussgeld Fahrer EUR"
        ))
        cp = _to_money(_row_get(
            row_norm,
            "COMPANY_PENALTY", "Company Penalty", "Bußgeld Firma", "Bussgeld Firma",
            "Firma Bußgeld", "Firma Bussgeld", "Unternehmen Bußgeld", "Unternehmen Bussgeld",
            "Pauschalbetrag", "Bußgeld", "Bussgeld", "Company Fine"
        ))
        idate = _clean(_row_get(row_norm, "INSTRUCTION_DATE", "Instruction Date", "Unterweisungsdatum", "Belehrungsdatum"))
        iby = _clean(_row_get(row_norm, "INSTRUCTION_BY", "Instruction By", "Unterwiesen durch", "Belehrt durch"))
        remark = _clean(_row_get(row_norm, "REMARK_TEXT", "Remark Text", "Bemerkung", "Kommentar", "Notiz"))

        date_sort = ""
        m = date_re.match(start)
        if m:
            dd, mm, yyyy = f"{int(m.group(1)):02d}", f"{int(m.group(2)):02d}", m.group(3)
            hh = f"{int(m.group(4) or 0):02d}"
            mi = f"{int(m.group(5) or 0):02d}"
            date_sort = f"{yyyy}-{mm}-{dd} {hh}:{mi}"

        instructed = bool(idate)

        entry = {
            "start": start,
            "end": end,
            "date_sort": date_sort,
            "target": target,
            "ist": ist,
            "diff": diff,
            "violation": viol,
            "law": law,
            "driver_penalty": dp,
            "company_penalty": cp,
            "instruction_date": idate,
            "instruction_by": iby,
            "remark": remark,
            "instructed": instructed,
        }

        if name not in by_driver:
            by_driver[name] = {
                "name": name,
                "verstoesse": [],
                "count": 0,
                "count_instructed": 0,
                "count_open": 0,
                "sum_driver_penalty": 0,
                "sum_company_penalty": 0,
                "sum_diff": 0,
                "types": {},
            }

        d = by_driver[name]
        d["verstoesse"].append(entry)
        d["count"] += 1
        d["sum_driver_penalty"] = round(d["sum_driver_penalty"] + dp, 2)
        d["sum_company_penalty"] = round(d["sum_company_penalty"] + cp, 2)
        d["sum_diff"] += diff
        if instructed:
            d["count_instructed"] += 1
        else:
            d["count_open"] += 1
        t = viol or "—"
        d["types"][t] = d["types"].get(t, 0) + 1

    drivers = []
    for d in by_driver.values():
        d["verstoesse"].sort(key=lambda x: x.get("date_sort", ""), reverse=True)
        d["types"] = sorted(d["types"].items(), key=lambda kv: (-kv[1], kv[0]))
        drivers.append(d)

    def _last_sort(d):
        return d["verstoesse"][0].get("date_sort", "") if d["verstoesse"] else ""
    drivers.sort(key=lambda x: (_last_sort(x), x["name"]), reverse=True)

    total = sum(d["count"] for d in drivers)
    total_driver_penalty = round(sum(float(d.get("sum_driver_penalty", 0) or 0) for d in drivers), 2)
    total_company_penalty = round(sum(float(d.get("sum_company_penalty", 0) or 0) for d in drivers), 2)

    _all_dates = [
        e.get("date_sort", "")[:10]
        for d in drivers
        for e in d.get("verstoesse", [])
        if e.get("date_sort")
    ]

    def _month_label(iso_date: str) -> str:
        try:
            yyyy, mm, _dd = iso_date.split("-", 2)
            return f"{mm}/{yyyy}"
        except Exception:
            return ""

    first_violation_month = _month_label(min(_all_dates)) if _all_dates else ""
    last_violation_month = _month_label(max(_all_dates)) if _all_dates else ""

    return json.dumps({
        "drivers": drivers,
        "total_violations": total,
        "total_driver_penalty": total_driver_penalty,
        "total_company_penalty": total_company_penalty,
        "first_violation_month": first_violation_month,
        "last_violation_month": last_violation_month,
    }, ensure_ascii=False)

def parse_fahrzeugwaesche_excel(uploaded_files) -> str:
    """Verarbeitet mehrere Fahrzeugwäsche-Excel-Dateien zu JSON für die Übersicht."""
    def _norm_header(value: str) -> str:
        value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]+", "", value.strip().lower())

    def _clean_text(value) -> str:
        if pd.isna(value):
            return ""
        s = str(value).strip()
        if s.lower() == "nan":
            return ""
        s = re.sub(r"\s+", " ", s)
        return s.replace("_", " ").strip()

    def _parse_date(value):
        if pd.isna(value):
            return "", ""
        if isinstance(value, (datetime.datetime, datetime.date, pd.Timestamp)):
            dt = pd.Timestamp(value)
            return dt.strftime("%d.%m.%Y"), dt.strftime("%Y-%m-%d")
        dt = pd.to_datetime(value, dayfirst=True, errors="coerce")
        if pd.notna(dt):
            return dt.strftime("%d.%m.%Y"), dt.strftime("%Y-%m-%d")
        s = _clean_text(value)
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
        if m:
            d, mo, y = m.groups()
            return f"{int(d):02d}.{int(mo):02d}.{y}", f"{y}-{int(mo):02d}-{int(d):02d}"
        return s, ""

    def _normalize_kennzeichen(value: str) -> str:
        """Normalisiert Spalte E (Fahrzeug-Kennzeichen) auf 'LWL - E XXX'.

        Extrahiert die erste zusammenhängende Ziffernfolge aus dem Eingabewert
        und baut damit das einheitliche Zielformat. Enthält der Wert keine
        Ziffern, wird er unverändert zurückgegeben (damit Freitext nicht verloren geht).
        """
        s = _clean_text(value)
        if not s:
            return ""
        m = re.search(r"\d+", s)
        if not m:
            return s
        return f"LWL - E {m.group(0)}"

    def _parse_time(value):
        if pd.isna(value):
            return ""
        if isinstance(value, datetime.datetime):
            return value.strftime("%H:%M:%S")
        if isinstance(value, datetime.time):
            return value.strftime("%H:%M:%S")
        if isinstance(value, pd.Timestamp):
            return value.strftime("%H:%M:%S")
        if isinstance(value, (int, float)) and 0 <= float(value) < 1:
            total_seconds = int(round(float(value) * 24 * 60 * 60))
            hours = (total_seconds // 3600) % 24
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        s = _clean_text(value)
        m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
        if m:
            hh, mm, ss = m.group(1), m.group(2), m.group(3) or "00"
            return f"{int(hh):02d}:{mm}:{ss}"
        return s

    header_aliases = {
        "fahrzeug_kategorie": ["fahrzeugkategorie", "kategorie"],
        "datum": ["datumdertransaktion", "datum"],
        "uhrzeit": ["zeitpunktdertransaktion", "zeitpunkt", "uhrzeit", "zeit"],
        "fahrzeug_ia": ["fahrzeugia"],
        "fahrzeug": ["fahrzeug"],
        "fahrer": ["fahrer"],
        "produkt": ["produkt"],
        "transaktions_typ": ["transaktionstyp"],
        "zapfsaeule": ["zapfsaule", "zapfsaeule"],
    }
    # Fahrzeug-IA ist optional, weil einige Waschdateien diese Spalte nicht enthalten.
    # Wenn sie hier Pflicht bleibt, werden komplette Dateien bzw. Blätter übersprungen.
    required_fields = ["datum", "uhrzeit", "fahrer", "fahrzeug", "produkt"]

    rows = []
    seen = set()

    for uploaded_file in uploaded_files or []:
        payload = read_upload_bytes(uploaded_file)
        if not payload:
            continue
        try:
            xls = pd.ExcelFile(io.BytesIO(payload), engine=EXCEL_READ_ENGINE)
        except Exception:
            continue

        for sheet_name in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=sheet_name)
            except Exception:
                continue
            if df is None or df.empty:
                continue

            norm_cols = {_norm_header(col): col for col in df.columns}
            resolved = {}
            for target, aliases in header_aliases.items():
                for alias in aliases:
                    if alias in norm_cols:
                        resolved[target] = norm_cols[alias]
                        break

            # Kennzeichen steht IMMER in Spalte E (0-basiert: Index 4) — positional übersteuern,
            # weil die Header in den Quell-Dateien uneinheitlich sind und das Header-Matching
            # sonst teils Spalte D (Fahrzeug-IA) liefert.
            if len(df.columns) > 4:
                resolved["fahrzeug"] = df.columns[4]

            if not all(field in resolved for field in required_fields):
                continue

            selected = pd.DataFrame()
            for target in header_aliases:
                if target in resolved:
                    selected[target] = df[resolved[target]]
                else:
                    selected[target] = ""
            selected = selected.dropna(how="all")
            if selected.empty:
                continue

            # itertuples statt iterrows — bei tausenden Zeilen ist iterrows
            # mit Series-Bauen pro Zeile mit Abstand der teuerste Anteil.
            sel_cols = list(selected.columns)
            i_datum   = sel_cols.index("datum")              if "datum" in sel_cols else None
            i_uhrzeit = sel_cols.index("uhrzeit")            if "uhrzeit" in sel_cols else None
            i_fahrer  = sel_cols.index("fahrer")             if "fahrer" in sel_cols else None
            i_fzg     = sel_cols.index("fahrzeug")           if "fahrzeug" in sel_cols else None
            i_fzg_ia  = sel_cols.index("fahrzeug_ia")        if "fahrzeug_ia" in sel_cols else None
            i_prod    = sel_cols.index("produkt")            if "produkt" in sel_cols else None
            i_kat     = sel_cols.index("fahrzeug_kategorie") if "fahrzeug_kategorie" in sel_cols else None
            i_typ     = sel_cols.index("transaktions_typ")   if "transaktions_typ" in sel_cols else None
            i_zaps    = sel_cols.index("zapfsaeule")         if "zapfsaeule" in sel_cols else None

            quelle_name = getattr(uploaded_file, "name", "")

            def _at(row, idx):
                if idx is None: return None
                try: return row[idx]
                except IndexError: return None

            for row in selected.itertuples(index=False, name=None):
                datum, date_iso = _parse_date(_at(row, i_datum))
                uhrzeit = _parse_time(_at(row, i_uhrzeit))
                fahrer = _clean_text(_at(row, i_fahrer))
                fahrzeug = _clean_text(_at(row, i_fzg))
                fahrzeug_ia = _clean_text(_at(row, i_fzg_ia))
                if not fahrzeug and fahrzeug_ia:
                    fahrzeug = fahrzeug_ia
                fahrzeug = _normalize_kennzeichen(fahrzeug)
                produkt = _clean_text(_at(row, i_prod))
                fahrzeug_kategorie = _clean_text(_at(row, i_kat))
                transaktions_typ = _clean_text(_at(row, i_typ))
                zapfsaeule = _clean_text(_at(row, i_zaps))
                if not any([datum, uhrzeit, fahrer, fahrzeug, fahrzeug_ia, produkt]):
                    continue
                datetime_iso = (date_iso + " " + (uhrzeit or "00:00:00")).strip() if date_iso else ""
                item = {
                    "datum": datum,
                    "date_iso": date_iso,
                    "uhrzeit": uhrzeit,
                    "datetime_iso": datetime_iso,
                    "fahrer": fahrer,
                    "fahrzeug": fahrzeug,
                    "fahrzeug_ia": fahrzeug_ia,
                    "produkt": produkt,
                    "fahrzeug_kategorie": fahrzeug_kategorie,
                    "transaktions_typ": transaktions_typ,
                    "zapfsaeule": zapfsaeule,
                    "quelle": quelle_name,
                }
                key = (
                    item["datum"], item["uhrzeit"], item["fahrer"], item["fahrzeug"],
                    item["fahrzeug_ia"], item["produkt"], item["fahrzeug_kategorie"],
                    item["transaktions_typ"], item["zapfsaeule"], item["quelle"],
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(item)

    rows.sort(key=lambda x: (x.get("datetime_iso", ""), x.get("fahrer", ""), x.get("fahrzeug", "")), reverse=True)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def parse_grosskunden_excel(uploaded_file) -> str:
    """Liest alle Blätter der Großkunden-Excel aus und liefert JSON.

    Performance: read_only=True und Streaming über die Zeilen, damit große
    Großkunden-Dateien nicht komplett als Liste im Speicher landen.
    """
    import openpyxl as _opxl

    def _clean(v):
        if v is None:
            return ""
        return str(v).replace("\xa0", " ").strip()

    HEADER_KEYWORDS = {
        "name", "kundennummer", "mail", "telefon", "hinweise",
        "e-mail", "email", "adresse", "ansprechpartner", "bemerkung",
        "info", "kontakt", "anmerkung", "lieferzeit", "hinweis",
        "knr", "strasse", "straße", "plz", "ort",
    }

    def _is_structured(first_row):
        cells = {_clean(c).lower().strip() for c in first_row}
        return bool(HEADER_KEYWORDS & cells)

    def _row_has_value(row):
        return any(_clean(c) for c in row)

    empty = "[]"
    payload = read_upload_bytes(uploaded_file)
    if not payload:
        return empty
    try:
        wb = _opxl.load_workbook(io.BytesIO(payload), data_only=True, read_only=True)
    except Exception:
        return empty

    result = []
    try:
        for sname in wb.sheetnames:
            ws = wb[sname]
            row_iter = ws.iter_rows(values_only=True)
            first_row = None
            for row in row_iter:
                if _row_has_value(row):
                    first_row = row
                    break
            if first_row is None:
                continue

            if _is_structured(first_row):
                headers = [_clean(c) for c in first_row]

                name_col = next(
                    (i for i, h in enumerate(headers) if h.lower().strip() == "name"),
                    -1
                )
                knr_col = next(
                    (i for i, h in enumerate(headers)
                     if "kundennummer" in h.lower() or h.lower().strip() == "knr"),
                    -1
                )
                content_idx = [i for i, h in enumerate(headers)
                               if i != name_col and i != knr_col and h.strip()]
                content_headers = [headers[i] for i in content_idx]

                def _get(row, idx):
                    if idx < 0 or idx >= len(row):
                        return ""
                    return _clean(row[idx])

                entries = []
                current = None
                for row in row_iter:
                    if not _row_has_value(row):
                        continue
                    name = _get(row, name_col)
                    knr = _get(row, knr_col)
                    content_row = [_get(row, i) for i in content_idx]

                    if name:
                        current = {
                            "name": name,
                            "kundennummer": knr,
                            "rows": [content_row] if any(content_row) else [],
                        }
                        entries.append(current)
                    elif current and any(content_row):
                        current["rows"].append(content_row)

                if entries:
                    result.append({
                        "name": sname,
                        "type": "structured",
                        "content_headers": content_headers,
                        "entries": entries,
                    })
            else:
                lines = []
                for cell in first_row:
                    val = _clean(cell)
                    if val and val != "None":
                        lines.append(val)
                for row in row_iter:
                    if not _row_has_value(row):
                        continue
                    for cell in row:
                        val = _clean(cell)
                        if val and val != "None":
                            lines.append(val)
                if lines:
                    result.append({"name": sname, "type": "freeform", "lines": lines})
    finally:
        try:
            wb.close()
        except Exception:
            pass

    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# STREAMLIT UI
# =============================================================================

def _empty_inst(name="Normalwochen"):
    return {
        "name": name,
        "suche_html": None,
        "druck_html": None,
        "source_sig": None,
        "versp_start_json": "{}",
        "versp_start_sig": None,
        "woche_data": {},
        "excel_content_hash": "",
        "excel_filename": "",
        "quality_blocked": False,
    }

# Session-State robust initialisieren.
# Wichtig: nicht per Attributzugriff lesen, bevor der Key sicher existiert.
st.session_state.setdefault("instances", [_empty_inst("Normalwochen")])


# Zentrale Verarbeitungsanzeige. Die Eintraege bleiben waehrend der Session
# erhalten und werden bei einem neuen erfolgreichen Lauf desselben Bereichs
# aktualisiert. Fehler werden zusaetzlich in einer kompakten Historie bewahrt.
st.session_state.setdefault("_processing_status", {})
st.session_state.setdefault("_processing_errors", [])
st.session_state.setdefault("_quality_checks", {})


def _status_now() -> str:
    return datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _uploaded_name(uploaded) -> str:
    return str(getattr(uploaded, "name", "") or "")


def _set_processing_status(key: str, area: str, status: str,
                           detail: str = "", filename: str = "") -> None:
    """Aktualisiert genau einen sichtbaren Status-Eintrag."""
    status = status if status in {"ok", "warning", "error", "info"} else "info"
    st.session_state.setdefault("_processing_status", {})[key] = {
        "bereich": area,
        "status": status,
        "datei": filename,
        "detail": str(detail or ""),
        "zeit": _status_now(),
    }


def _clear_errors_for_key(key: str) -> None:
    errors = st.session_state.setdefault("_processing_errors", [])
    st.session_state["_processing_errors"] = [e for e in errors if e.get("key") != key]


def _record_processing_error(key: str, area: str, error: Exception,
                             filename: str = "", detail: str = "") -> None:
    """Zeigt einen Fehler im Status und legt eine deduplizierte Historie an."""
    err_type = type(error).__name__
    err_text = str(error) or repr(error)
    visible_detail = detail or f"{err_type}: {err_text}"
    _set_processing_status(key, area, "error", visible_detail, filename)

    errors = st.session_state.setdefault("_processing_errors", [])
    signature = f"{key}|{filename}|{err_type}|{err_text}"
    errors[:] = [e for e in errors if e.get("signature") != signature]
    errors.append({
        "key": key,
        "bereich": area,
        "datei": filename,
        "fehlertyp": err_type,
        "fehler": err_text,
        "zeit": _status_now(),
        "signature": signature,
    })
    del errors[:-50]


def _record_processing_success(key: str, area: str, detail: str = "",
                               filename: str = "") -> None:
    _clear_errors_for_key(key)
    _set_processing_status(key, area, "ok", detail, filename)


def _quality_result(status: str, area: str, filename: str, size: int,
                    summary: str, details=None, warnings=None, errors=None,
                    content_hash: str = "", required: bool = False,
                    source_signature: str = "") -> dict:
    return {
        "status": status,
        "area": area,
        "filename": filename,
        "size": int(size or 0),
        "summary": str(summary or ""),
        "details": [str(x) for x in (details or []) if str(x)],
        "warnings": [str(x) for x in (warnings or []) if str(x)],
        "errors": [str(x) for x in (errors or []) if str(x)],
        "content_hash": str(content_hash or ""),
        "required": bool(required),
        "source_signature": str(source_signature or ""),
        "checked_at": _status_now(),
    }


def _decode_text_sample(payload: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return payload.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace"), "utf-8 (Ersatzzeichen)"


def _stream_upload_hash(uploaded_file, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 ohne den gesamten Upload ein zweites Mal in den RAM zu kopieren."""
    if uploaded_file is None:
        return ""
    old_pos = None
    try:
        old_pos = uploaded_file.tell()
    except Exception:
        pass
    digest = hashlib.sha256()
    try:
        uploaded_file.seek(0)
        while True:
            chunk = uploaded_file.read(chunk_size)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="ignore")
            digest.update(chunk)
    finally:
        try:
            uploaded_file.seek(old_pos if old_pos is not None else 0)
        except Exception:
            pass
    return digest.hexdigest()


def _read_upload_sample(uploaded_file, limit: int = 131072) -> bytes:
    """Liest nur einen kleinen Anfangsbereich und stellt die Dateiposition wieder her."""
    if uploaded_file is None:
        return b""
    old_pos = None
    try:
        old_pos = uploaded_file.tell()
    except Exception:
        pass
    try:
        uploaded_file.seek(0)
        data = uploaded_file.read(limit)
    finally:
        try:
            uploaded_file.seek(old_pos if old_pos is not None else 0)
        except Exception:
            pass
    if data is None:
        return b""
    if isinstance(data, str):
        return data.encode("utf-8", errors="ignore")
    return bytes(data)


def _validate_uploaded_file(uploaded_file, expected_types, quality_key: str,
                            *, area: str = "Datei", kind: str = "auto",
                            required: bool = False) -> dict:
    """Speicherschonende Grundprüfung eines Uploads.

    Anders als Version 34 wird die Datei hier nicht komplett per ``getvalue``
    dupliziert und nicht vorsorglich mit pandas geoeffnet. Die eigentlichen
    Parser pruefen den Inhalt spaeter ohnehin nochmals. Dadurch bleibt der
    Start auch mit vielen grossen Uploads auf Streamlit Cloud stabil.
    """
    checks = st.session_state.setdefault("_quality_checks", {})
    filename = _uploaded_name(uploaded_file)
    source_sig = upload_signature(uploaded_file)
    cached = checks.get(quality_key)
    if cached and cached.get("source_signature") == source_sig and cached.get("kind") == kind:
        return cached

    try:
        size = int(getattr(uploaded_file, "size", 0) or 0)
    except Exception:
        size = 0
    ext = Path(filename).suffix.lower().lstrip(".")
    allowed = {str(x).lower().lstrip(".") for x in (expected_types or [])}
    details: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    if not filename:
        errors.append("Dateiname fehlt")
    if allowed and ext not in allowed:
        errors.append(f"Falscher Dateityp .{ext or '?'}; erwartet: " + ", ".join(sorted(allowed)))
    if size <= 0:
        errors.append("Datei ist leer")
    else:
        details.append(_human_size(size))
        if size > 75 * 1024 * 1024:
            warnings.append("Sehr grosse Datei; die Verarbeitung kann auf Streamlit Cloud viel Speicher benoetigen")

    actual_kind = kind
    if actual_kind == "auto":
        if ext == "xlsx":
            actual_kind = "xlsx"
        elif ext == "csv":
            actual_kind = "csv"
        elif ext == "json":
            actual_kind = "json"
        elif ext in {"png", "jpg", "jpeg", "svg"}:
            actual_kind = "logo"

    sample = _read_upload_sample(uploaded_file) if size > 0 else b""
    if sample and not errors:
        if actual_kind in {"xlsx", "week_excel", "key_excel"}:
            if not sample.startswith(b"PK"):
                errors.append("Datei besitzt keine gueltige XLSX-Signatur")
            else:
                details.append("XLSX-Signatur gueltig")
                if actual_kind == "week_excel":
                    details.append("Wocheninhalt wird beim Verarbeiten geprueft")
                elif actual_kind == "key_excel":
                    details.append("Schluesselzuordnungen werden beim Verarbeiten geprueft")
        elif actual_kind == "csv":
            text, encoding = _decode_text_sample(sample)
            lines = [line for line in text.splitlines() if line.strip()]
            if not lines:
                errors.append("CSV enthaelt im Dateianfang keine Daten")
            else:
                probe = lines[:20]
                delimiters = {sep: sum(line.count(sep) for line in probe) for sep in (";", ",", "\t", "|")}
                delimiter = max(delimiters, key=delimiters.get)
                col_count = max(1, probe[0].count(delimiter) + 1) if delimiters[delimiter] else 1
                details.append(f"Dateianfang lesbar · ca. {col_count} Spalten · {encoding}")
                if col_count == 1:
                    warnings.append("Kein eindeutiges CSV-Trennzeichen im Dateianfang erkannt")
        elif actual_kind == "json":
            stripped = sample.lstrip()
            if not stripped.startswith((b"{", b"[")):
                errors.append("Dateianfang sieht nicht wie JSON aus")
            else:
                details.append("JSON-Dateianfang erkannt; Vollpruefung erfolgt beim Verarbeiten")
        elif actual_kind == "logo":
            lower = sample[:500].lstrip().lower()
            valid = (
                sample.startswith(b"\x89PNG\r\n\x1a\n")
                or sample.startswith(b"\xff\xd8\xff")
                or (ext == "svg" and (lower.startswith(b"<svg") or b"<svg" in lower))
            )
            if not valid:
                errors.append("Bildinhalt passt nicht zum ausgewaehlten PNG/JPG/SVG-Format")
            else:
                details.append("Bildsignatur gueltig")

    # Nur Wochen-Dateien werden inhaltlich gehasht, damit auch umbenannte
    # Duplikate erkannt werden. Das Hashing erfolgt gestreamt ohne Vollkopie.
    if size > 0 and actual_kind == "week_excel" and not errors:
        content_hash = _stream_upload_hash(uploaded_file)
    else:
        content_hash = hashlib.sha256(f"{filename}|{size}|{source_sig}".encode("utf-8", errors="ignore")).hexdigest()

    status = "error" if errors else ("warning" if warnings else "ok")
    if status == "ok":
        summary = "Grundprüfung bestanden"
    elif status == "warning":
        summary = f"Grundprüfung bestanden · {len(warnings)} Hinweis(e)"
    else:
        summary = f"Dateiprüfung fehlgeschlagen · {len(errors)} Fehler"
    result = _quality_result(
        status, area, filename, size, summary, details, warnings, errors,
        content_hash=content_hash, required=required, source_signature=source_sig,
    )
    result["kind"] = kind
    checks[quality_key] = result
    return result


def _render_quality_result(result: dict, *, compact: bool = True) -> None:
    if not result:
        return
    detail_text = " · ".join(result.get("details", [])[:3])
    prefix = {"ok": "✓", "warning": "⚠", "error": "✗"}.get(result.get("status"), "•")
    line = f"{prefix} Dateiprüfung: {result.get('summary', '')}"
    if detail_text:
        line += f" · {detail_text}"
    if result.get("status") == "error":
        st.error(line + (" · " + " | ".join(result.get("errors", [])[:3]) if result.get("errors") else ""))
    elif result.get("status") == "warning":
        st.warning(line + (" · " + " | ".join(result.get("warnings", [])[:3]) if result.get("warnings") else ""))
    else:
        st.caption(line)


def _quality_table_rows() -> list[dict]:
    rows = []
    for key, result in sorted((st.session_state.get("_quality_checks", {}) or {}).items(), key=lambda item: str(item[1].get("area", "")).lower()):
        status = result.get("status", "info")
        rows.append({
            "Status": {"ok": "✓ OK", "warning": "⚠ Hinweis", "error": "✗ Fehler"}.get(status, "• Info"),
            "Bereich": result.get("area", key),
            "Datei": result.get("filename", ""),
            "Groesse": _human_size(result.get("size", 0)),
            "Ergebnis": result.get("summary", ""),
            "Details": " | ".join((result.get("errors") or result.get("warnings") or result.get("details") or [])[:3]),
        })
    return rows


def _duplicate_week_groups() -> list[list[dict]]:
    by_hash: dict[str, list[dict]] = {}
    for idx, inst in enumerate(st.session_state.get("instances", []) or []):
        digest = str(inst.get("excel_content_hash", "") or "")
        if not digest:
            continue
        by_hash.setdefault(digest, []).append({
            "index": idx,
            "name": str(inst.get("name", f"Woche {idx + 1}") or f"Woche {idx + 1}"),
            "filename": str(inst.get("excel_filename", "") or ""),
        })
    return [group for group in by_hash.values() if len(group) > 1]


def _clear_generated_html_file() -> None:
    """Entfernt eine zuvor erzeugte temporäre HTML-Datei dieser Session."""
    old_path = st.session_state.pop("_generated_app_html_path", None)
    st.session_state.pop("_generated_app_html_meta", None)
    if old_path:
        try:
            Path(old_path).unlink(missing_ok=True)
        except Exception:
            pass


def _read_generated_html_file(path: str) -> bytes:
    """Wird von st.download_button erst beim tatsächlichen Download aufgerufen."""
    return Path(path).read_bytes()


def _generation_source_signature(ready_instances: list) -> str:
    """Kompakte Signatur aller Quellen, die den HTML-Export beeinflussen.

    Es werden nur bereits vorhandene Upload-/Parser-Signaturen und kleine
    Metadaten gehasht. Die großen JSON- und HTML-Inhalte werden bewusst nicht
    bei jedem Streamlit-Rerun erneut kopiert.
    """
    parts = [APP_CACHE_VERSION, EXTRA_CACHE_VERSION]
    for inst in ready_instances or []:
        parts.extend([
            str(inst.get("name", "")),
            str(inst.get("source_sig", "")),
            str(inst.get("versp_start_sig", "")),
        ])

    for key in sorted(st.session_state.keys()):
        if str(key).endswith("_sig"):
            value = st.session_state.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")

    for key in (
        "g_logo", "g_key", "g_fach", "g_fcsb", "g_lh_csv",
        "g_rahmen_csv", "g_kundenart_csv",
    ):
        parts.append(f"{key}={upload_signature(st.session_state.get(key))}")

    return combine_signatures(*parts)


def _human_size(num_bytes: int) -> str:
    try:
        value = max(0, int(num_bytes))
    except Exception:
        value = 0
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / 1024 / 1024:.1f} MB"


def _safe_state_json(key: str, fallback):
    try:
        value = json.loads(st.session_state.get(key, "") or "")
        return value
    except Exception:
        return fallback


def _embedded_pdf_stats() -> tuple[int, int]:
    """Anzahl und komprimierte Bytegroesse der eingebetteten PDFs."""
    meta = _load_asset_meta()
    return int(meta.get("pdf_count", 0) or 0), int(meta.get("pdf_compressed_bytes", 0) or 0)


def _build_generation_metadata(ready_instances: list, generated_at: datetime.datetime) -> dict:
    """Erstellt eine kompakte, browserfreundliche Datenstandsuebersicht."""
    status_map = st.session_state.get("_processing_status", {}) or {}
    source_rows = []
    status_counts = {"ok": 0, "warning": 0, "error": 0, "info": 0}
    for key, row in sorted(status_map.items(), key=lambda item: str(item[1].get("bereich", "")).lower()):
        status = str(row.get("status", "info") or "info")
        if key.startswith("export_") or key == "download_html":
            continue
        status_counts[status] = status_counts.get(status, 0) + 1
        source_rows.append({
            "status": status,
            "area": str(row.get("bereich", "") or ""),
            "file": str(row.get("datei", "") or ""),
            "detail": str(row.get("detail", "") or ""),
            "time": str(row.get("zeit", "") or ""),
        })

    tel = _safe_state_json("tel_json", [])
    wash = _safe_state_json("fahrzeugwaesche_json", [])
    timerec = _safe_state_json("timerec_json", {})
    violations = _safe_state_json("verstoss_json", {})
    expenses = _safe_state_json("spesen_json", {})
    big_customers = _safe_state_json("grosskunden_json", [])
    carriers = _safe_state_json("spediteure_json", {})
    driver_rating = _safe_state_json("fahrerbewertung_json", {})

    shift_count = sum(len(v) for v in timerec.values() if isinstance(v, list)) if isinstance(timerec, dict) else 0
    pdf_count, pdf_bytes = _embedded_pdf_stats()
    weeks = [str(inst.get("name", "Woche")) for inst in ready_instances]

    datasets = [
        {"label": "Wochen / Suche", "value": str(len(weeks)), "detail": ", ".join(weeks)},
        {"label": "Telefon / Fachberater", "value": str(len(tel) if isinstance(tel, list) else 0), "detail": "Einträge"},
        {"label": "Fahrzeugwaesche", "value": str(len(wash) if isinstance(wash, list) else 0), "detail": "Datensaetze"},
        {"label": "Schichten / Tachograph", "value": str(shift_count), "detail": f"{len(timerec) if isinstance(timerec, dict) else 0} Fahrer"},
        {"label": "Verstöße", "value": str(violations.get("total_violations", 0) if isinstance(violations, dict) else 0), "detail": f"{len(violations.get('drivers', [])) if isinstance(violations, dict) else 0} Fahrer"},
        {"label": "Spesen", "value": str(expenses.get("total_rows", 0) if isinstance(expenses, dict) else 0), "detail": f"{len(expenses.get('drivers', [])) if isinstance(expenses, dict) else 0} Fahrer"},
        {"label": "Großkunden", "value": str(len(big_customers) if isinstance(big_customers, list) else 0), "detail": "Kunden"},
        {"label": "Spediteure", "value": str(len(carriers.get("fahrten", [])) if isinstance(carriers, dict) else 0), "detail": "Fahrten"},
        {"label": "Fahrerbewertung", "value": str(len(driver_rating.get("drivers", [])) if isinstance(driver_rating, dict) else 0), "detail": "Fahrer"},
        {"label": "PDF-Dokumente", "value": str(pdf_count), "detail": f"{_human_size(pdf_bytes)} komprimiert"},
    ]

    return {
        "version": APP_DISPLAY_VERSION,
        "created_at": generated_at.strftime("%d.%m.%Y %H:%M:%S"),
        "weeks": weeks,
        "datasets": datasets,
        "sources": source_rows[-40:],
        "stats": {
            "ok": status_counts.get("ok", 0),
            "warning": status_counts.get("warning", 0),
            "error": status_counts.get("error", 0),
            "info": status_counts.get("info", 0),
            "pdf_count": pdf_count,
            "pdf_compressed_bytes": pdf_bytes,
        },
    }


def _estimate_export_size(ready_instances: list) -> int:
    """Grobe Exportprognose ohne die große HTML vorab zu erzeugen.

    Wochen-HTML und Zusatzdaten werden beim Export stark komprimiert; die
    eingebetteten PDF-Blöcke liegen dagegen bereits als Base64 vor. Die
    Schätzung dient nur als Orientierung in der Startprüfung.
    """
    week_chars = 0
    for inst in ready_instances or []:
        week_chars += len(str(inst.get("suche_html", "") or ""))
        week_chars += len(str(inst.get("druck_html", "") or ""))

    extra_chars = 0
    for key in (
        "tel_json", "sam_json", "fa_json", "zulage_json",
        "drittkunden_json", "fahrzeugwaesche_json", "verstoss_json",
        "spesen_json", "grosskunden_json", "timerec_json",
        "spediteure_json", "fahrerbewertung_json",
    ):
        extra_chars += len(str(st.session_state.get(key, "") or ""))

    asset_meta = _load_asset_meta()
    pdf_b64_chars = int(asset_meta.get("pdf_b64_chars", 0) or 0)
    static_js_chars = int(asset_meta.get("dashboard_js_chars", 0) or 0)

    # Erfahrungswerte: Wochen-HTML ca. 35–45 %, JSON ca. 45–60 % nach
    # Deflate + Base64. Ein Sicherheitsaufschlag deckt HTML/CSS/Metadaten ab.
    estimated = (
        pdf_b64_chars
        + int(week_chars * 0.43)
        + int(extra_chars * 0.56)
        + static_js_chars
        + 700_000
    )
    return max(0, int(estimated))


def _build_export_preflight(ready_instances: list) -> tuple[list[dict], list[str], int]:
    """Erstellt die sichtbare Startprüfung und nennt echte Blocker."""
    rows: list[dict] = []
    blockers: list[str] = []

    asset_error = _asset_installation_error()
    if asset_error:
        blockers.append("Programmdateien / nfc_assets")
        rows.append({"Status": "✗ Fehlt", "Bereich": "Programmdateien", "Details": asset_error})

    def add(label: str, ok: bool, detail: str, *, required: bool = False,
            warning: bool = False) -> None:
        if ok:
            status = "✓ Bereit"
        elif required:
            status = "✗ Fehlt"
            blockers.append(label)
        elif warning:
            status = "⚠ Hinweis"
        else:
            status = "– Optional"
        rows.append({
            "Status": status,
            "Prüfung": label,
            "Details": detail,
            "Pflicht": "Ja" if required else "Nein",
        })

    logo = st.session_state.get("g_logo")
    key_file = st.session_state.get("g_key")
    add("Logo", bool(logo), _uploaded_name(logo) or "Noch nicht hochgeladen", required=True)
    add("Schlüsseldatei", bool(key_file), _uploaded_name(key_file) or "Noch nicht hochgeladen", required=True)

    ready_names = [str(inst.get("name", "Woche") or "Woche") for inst in ready_instances or []]
    add(
        "Verarbeitete Woche", bool(ready_names),
        ", ".join(ready_names) if ready_names else "Noch keine Woche vollständig verarbeitet",
        required=True,
    )

    all_instances = st.session_state.get("instances", []) or []
    incomplete = [
        str(inst.get("name", "Woche") or "Woche")
        for inst in all_instances
        if not (inst.get("suche_html") and inst.get("druck_html"))
    ]
    add(
        "Weitere angelegte Wochen", not incomplete,
        "Alle angelegten Wochen sind bereit" if not incomplete else "Nicht bereit: " + ", ".join(incomplete),
        warning=bool(incomplete),
    )

    pdf_count, pdf_bytes = _embedded_pdf_stats()
    add(
        "Eingebettete PDFs", pdf_count > 0,
        f"{pdf_count} Dokumente · {_human_size(pdf_bytes)} komprimiert" if pdf_count else "Keine PDFs eingebettet",
        warning=(pdf_count == 0),
    )

    optional_keys = (
        "tel_json", "zulage_json", "drittkunden_json", "fahrzeugwaesche_json",
        "verstoss_json", "spesen_json", "grosskunden_json", "timerec_json",
        "spediteure_json", "fahrerbewertung_json",
    )
    loaded_optional = sum(
        1 for key in optional_keys
        if str(st.session_state.get(key, "") or "") not in ("", "[]", "{}", '{"drivers":[],"total_violations":0}', '{"drivers":[],"months":[],"total_cost":0,"total_rows":0}')
    )
    add(
        "Zusatzdaten", loaded_optional > 0,
        f"{loaded_optional} von {len(optional_keys)} Datenbereichen geladen" if loaded_optional else "Keine Zusatzdaten geladen",
    )

    error_count = sum(
        1 for row in (st.session_state.get("_processing_status", {}) or {}).values()
        if row.get("status") == "error"
    )
    add(
        "Verarbeitungsfehler", error_count == 0,
        "Keine aktuellen Fehler" if error_count == 0 else f"{error_count} Fehler im Verarbeitungsstatus – Export bleibt möglich",
        warning=(error_count > 0),
    )

    quality_checks = list((st.session_state.get("_quality_checks", {}) or {}).values())
    required_quality_errors = [q for q in quality_checks if q.get("required") and q.get("status") == "error"]
    optional_quality_errors = [q for q in quality_checks if not q.get("required") and q.get("status") == "error"]
    quality_warnings = [q for q in quality_checks if q.get("status") == "warning"]
    quality_detail = (
        f"{len(quality_checks)} Dateien geprueft · {len(quality_warnings)} Hinweis(e)"
        if quality_checks else "Noch keine Dateien geprueft"
    ) + (f" · {len(optional_quality_errors)} optionale Fehler" if optional_quality_errors else "")
    if required_quality_errors:
        rows.append({
            "Status": "✗ Fehler",
            "Prüfung": "Dateiqualität",
            "Details": quality_detail + f" · {len(required_quality_errors)} Pflichtdatei(en) fehlerhaft",
            "Pflicht": "Ja",
        })
        blockers.append("Dateiqualität")
    elif quality_warnings or optional_quality_errors:
        rows.append({
            "Status": "⚠ Hinweis",
            "Prüfung": "Dateiqualität",
            "Details": quality_detail,
            "Pflicht": "Nein",
        })
    else:
        rows.append({
            "Status": "✓ Bereit",
            "Prüfung": "Dateiqualität",
            "Details": quality_detail,
            "Pflicht": "Nein",
        })

    duplicate_groups = _duplicate_week_groups()
    duplicate_detail = "; ".join(
        " = ".join(f"{x['name']} ({x['filename'] or 'Datei'})" for x in group)
        for group in duplicate_groups
    )
    add(
        "Doppelte Wochen", not duplicate_groups,
        "Keine identischen Wochen-Dateien erkannt" if not duplicate_groups else duplicate_detail,
        required=True,
    )

    estimate = _estimate_export_size(ready_instances)
    rows.append({
        "Status": "• Prognose",
        "Prüfung": "Erwartete HTML-Größe",
        "Details": f"ca. {_human_size(estimate)}",
        "Pflicht": "Nein",
    })
    return rows, blockers, estimate


def _reset_all_app_data() -> None:
    """Leert Uploads, Sessiondaten, Caches und die temporäre Exportdatei."""
    _clear_generated_html_file()
    try:
        st.cache_data.clear()
    except Exception:
        pass
    try:
        st.cache_resource.clear()
    except Exception:
        pass
    for key in list(st.session_state.keys()):
        try:
            del st.session_state[key]
        except Exception:
            pass


def _safe_cached_export_b64(cache_key: str, source_value: str, builder,
                            area: str) -> str:
    """Erzeugt einen Export, ohne dass ein Fehler den gesamten Download-Tab stoppt."""
    status_key = f"export_{cache_key}"
    try:
        result = get_cached_export_b64(cache_key, source_value, builder)
        _record_processing_success(
            status_key, area,
            "Export vorbereitet" if result else "Keine Exportdaten vorhanden",
        )
        return result
    except Exception as exc:
        _record_processing_error(status_key, area, exc)
        return ""


# -----------------------------------------------------------------------------
# UI-Helpers
# -----------------------------------------------------------------------------
def _global_uploader(label, types, ss_key, widget_key):
    """Stammdaten-Uploader mit echter Inhaltspruefung."""
    up = st.file_uploader(label, type=types, key=widget_key)
    required = ss_key in {"g_logo", "g_key"}
    kind = "logo" if ss_key == "g_logo" else ("key_excel" if ss_key == "g_key" else "auto")
    if up:
        quality = _validate_uploaded_file(
            up, types, f"quality_global_{ss_key}", area=label, kind=kind, required=required
        )
        _render_quality_result(quality)
        if quality.get("status") != "error":
            st.session_state[ss_key] = up
            _record_processing_success(
                f"upload_{ss_key}", label, quality.get("summary", "Datei ausgewaehlt"), _uploaded_name(up)
            )
        else:
            _set_processing_status(
                f"upload_{ss_key}", label, "error", quality.get("summary", "Dateiprüfung fehlgeschlagen"), _uploaded_name(up)
            )
    elif st.session_state.get(ss_key):
        stored = st.session_state.get(ss_key)
        _set_processing_status(
            f"upload_{ss_key}", label, "info", "In der Session vorhanden", _uploaded_name(stored)
        )


def _extra_single_upload(label, types, key_prefix, parser, summary_fn=None,
                         spinner_text="Verarbeite ..."):
    """Generische Zusatzdatei mit Fehler- und Statusanzeige."""
    up = st.file_uploader(label, type=types, key=f"{key_prefix}_widget")
    json_key = f"{key_prefix}_json"
    sig_key  = f"{key_prefix}_sig"
    status_key = f"extra_{key_prefix}"
    if up:
        filename = _uploaded_name(up)
        quality = _validate_uploaded_file(
            up, types, f"quality_extra_{key_prefix}", area=label, kind="auto", required=False
        )
        _render_quality_result(quality)
        if quality.get("status") == "error":
            _set_processing_status(status_key, label, "error", quality.get("summary", "Dateiprüfung fehlgeschlagen"), filename)
            return
        sig = combine_signatures(EXTRA_CACHE_VERSION, key_prefix, upload_signature(up))
        if st.session_state.get(sig_key) != sig:
            try:
                with st.spinner(spinner_text):
                    parsed = parser(up)
                st.session_state[json_key] = parsed
                st.session_state[sig_key] = sig
                _record_processing_success(status_key, label, "Erfolgreich verarbeitet", filename)
            except Exception as exc:
                _record_processing_error(status_key, label, exc, filename)
                st.error(f"{label}: {type(exc).__name__}: {exc}")
        elif st.session_state.get(json_key):
            _set_processing_status(status_key, label, "ok", "Bereits verarbeitet", filename)

        if st.session_state.get(json_key):
            if summary_fn:
                try:
                    summary = summary_fn(st.session_state[json_key])
                    st.caption(summary)
                    if st.session_state.get(sig_key) == sig:
                        _record_processing_success(status_key, label, summary, filename)
                except Exception as exc:
                    st.caption(f"{label}: geladen")
                    _set_processing_status(
                        status_key, label, "warning",
                        f"Datei verarbeitet, Zusammenfassung nicht moeglich: {type(exc).__name__}",
                        filename,
                    )
            else:
                st.caption(f"{label}: geladen")
    elif st.session_state.get(json_key):
        st.caption(f"{label}: geladen")
        current = st.session_state.get("_processing_status", {}).get(status_key)
        if not current:
            _set_processing_status(status_key, label, "info", "Daten in der Session vorhanden")


def _extra_multi_upload(label, types, key_prefix, parsers, summary_fn=None,
                        spinner_text="Verarbeite ..."):
    """Multi-Upload mit getrenntem Status je Parser und Gesamtstatus."""
    ups = st.file_uploader(label, type=types, accept_multiple_files=True,
                           key=f"{key_prefix}_widget")
    sig_key = f"{key_prefix}_sig"
    group_status_key = f"extra_{key_prefix}"
    if ups:
        filenames = ", ".join(_uploaded_name(up) for up in ups)
        quality_results = [
            _validate_uploaded_file(
                up, types, f"quality_extra_{key_prefix}_{idx}",
                area=f"{label} / {_uploaded_name(up)}", kind="auto", required=False,
            )
            for idx, up in enumerate(ups)
        ]
        for quality in quality_results:
            _render_quality_result(quality)
        content_hashes = [q.get("content_hash") for q in quality_results if q.get("content_hash")]
        duplicate_count = len(content_hashes) - len(set(content_hashes))
        if duplicate_count:
            st.warning(f"{duplicate_count} doppelte Datei(en) im Mehrfach-Upload erkannt; bitte entfernen.")
        if any(q.get("status") == "error" for q in quality_results) or duplicate_count:
            _set_processing_status(group_status_key, label, "error", "Dateiprüfung oder Duplikatpruefung fehlgeschlagen", filenames)
            return
        sig = combine_signatures(EXTRA_CACHE_VERSION, key_prefix, uploads_signature(ups))
        if st.session_state.get(sig_key) != sig:
            failed = []
            with st.spinner(spinner_text):
                for state_key, parser in parsers.items():
                    parser_status_key = f"extra_{key_prefix}_{state_key}"
                    try:
                        parsed = parser(ups)
                        st.session_state[state_key] = parsed
                        _record_processing_success(
                            parser_status_key, f"{label} / {state_key}",
                            "Erfolgreich verarbeitet", filenames,
                        )
                    except Exception as exc:
                        failed.append(state_key)
                        _record_processing_error(
                            parser_status_key, f"{label} / {state_key}", exc, filenames
                        )
            if failed:
                _set_processing_status(
                    group_status_key, label, "error",
                    "Fehler in: " + ", ".join(failed), filenames,
                )
                st.error(f"{label}: Fehler in {', '.join(failed)}")
            else:
                st.session_state[sig_key] = sig
                _record_processing_success(
                    group_status_key, label,
                    f"{len(ups)} Datei(en) vollstaendig verarbeitet", filenames,
                )
        else:
            _set_processing_status(
                group_status_key, label, "ok",
                f"{len(ups)} Datei(en) bereits verarbeitet", filenames,
            )

        if any(st.session_state.get(k) for k in parsers):
            if summary_fn:
                try:
                    summary = summary_fn(ups)
                    st.caption(summary)
                    if st.session_state.get(sig_key) == sig:
                        _record_processing_success(group_status_key, label, summary, filenames)
                except Exception as exc:
                    st.caption(f"{len(ups)} Dateien geladen")
                    if st.session_state.get(sig_key) == sig:
                        _set_processing_status(
                            group_status_key, label, "warning",
                            f"Dateien verarbeitet, Zusammenfassung nicht moeglich: {type(exc).__name__}",
                            filenames,
                        )
            else:
                st.caption(f"{len(ups)} Dateien geladen")
    elif any(st.session_state.get(k) for k in parsers):
        st.caption(f"{label}: geladen")
        if not st.session_state.get("_processing_status", {}).get(group_status_key):
            _set_processing_status(group_status_key, label, "info", "Daten in der Session vorhanden")


def _format_eur(value: float) -> str:
    return f"{value:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


# -----------------------------------------------------------------------------
# UI-Layout
# -----------------------------------------------------------------------------
_boot_log("06 Funktionsdefinitionen geladen; UI-Aufbau beginnt")
# region Eingebettete PDF-Dokumente (komprimierte Base64-Daten)
# PDF-Dokumente: zlib-komprimiert + Base64. Dadurch bleiben sie in der einzigen
# Ausgabe-Datei enthalten, ohne dass ein separater Ordner benoetigt wird.
# Im Browser werden sie erst beim Oeffnen des jeweiligen Menuepunkts entpackt.
# EMBEDDED_PDF_DOCUMENTS wurde fuer Cloud-Stabilitaet nach nfc_assets ausgelagert.
# endregion

st.title(f"{APP_DISPLAY_NAME} · Version {APP_DISPLAY_VERSION}")
_boot_log("07 Titel gerendert")
st.caption("Modularer Einzeldatei-Generator mit speicherschonender Dateiprüfung und sicherem Export")

tab_stamm, tab_wochen, tab_extra, tab_dl = st.tabs(
    ["Stammdaten", "Wochen", "Zusatzdateien", "Download"]
)

# === Tab: Stammdaten =========================================================
_boot_log("08 Tab Stammdaten wird aufgebaut")
with tab_stamm:
    col_a, col_b = st.columns(2)
    with col_a:
        _global_uploader("Logo",                       ["png","jpg","jpeg","svg"], "g_logo",      "global_up_logo_v2")
        _global_uploader("Schluesseldatei (Pflicht)",  ["xlsx"],                   "g_key",       "global_up_key_v2")
        _global_uploader("Telefonnummern Fachberater", ["xlsx"],                   "g_fach",      "global_up_fach_v2")
    with col_b:
        _global_uploader("Kundenliste Original",       ["xlsx"], "g_fcsb",       "global_up_fcsb_v2")
        _global_uploader("Lieferhinweise CSV",         ["csv"],  "g_lh_csv",     "global_up_lh_csv_v2")
        _global_uploader("Rahmentourprofil CSV",       ["csv"],  "g_rahmen_csv", "global_up_rahmen_csv_v2")
        _global_uploader("Kundenart Absetzer/Rampe CSV", ["csv"], "g_kundenart_csv", "global_up_kundenart_csv_v1")

    _items = [
        ("g_logo","Logo"), ("g_key","Schluessel"), ("g_fach","FB-Tel"),
        ("g_fcsb","Kundenliste"), ("g_lh_csv","Lieferhinweise"), ("g_rahmen_csv","Rahmentour"),
        ("g_kundenart_csv","Kundenart")
    ]
    _ok   = [lbl for k, lbl in _items if st.session_state.get(k)]
    _miss = [lbl for k, lbl in _items if not st.session_state.get(k)]
    st.divider()
    if _ok:
        st.caption("✓ " + ", ".join(_ok))
    if _miss:
        st.caption("Fehlt: " + ", ".join(_miss))

# === Tab: Wochen =============================================================
_boot_log("09 Tab Wochen wird aufgebaut")
with tab_wochen:
    for i, inst in enumerate(st.session_state["instances"]):
        _is_normal = (i == 0)
        _label = "Normalwoche" if _is_normal else f"Woche {i+1}"

        with st.expander(f"{_label}: {inst['name']}", expanded=_is_normal):
            new_name = st.text_input("Bezeichnung", value=inst["name"], key=f"inst_name_{i}")
            st.session_state["instances"][i]["name"] = new_name

            # Bestehende Instanzen aus alten Sessions haben diese Keys eventuell noch nicht.
            st.session_state["instances"][i].setdefault("versp_start_json", "{}")
            st.session_state["instances"][i].setdefault("versp_start_sig", None)

            _excel_label = "Normalwochen-Excel (Pflicht)" if _is_normal else "Wochen-Excel (Pflicht)"
            excel = st.file_uploader(_excel_label, type=["xlsx"], key=f"excel_{i}")
            _week_quality = None
            if excel:
                _week_quality = _validate_uploaded_file(
                    excel, ["xlsx"], f"quality_week_{i}",
                    area=f"{_label} / Wochen-Excel", kind="week_excel", required=True,
                )
                _render_quality_result(_week_quality)
                st.session_state["instances"][i]["excel_content_hash"] = _week_quality.get("content_hash", "")
                st.session_state["instances"][i]["excel_filename"] = _uploaded_name(excel)
                st.session_state["instances"][i]["quality_blocked"] = (_week_quality.get("status") == "error")
            else:
                st.session_state["instances"][i].setdefault("excel_content_hash", "")
                st.session_state["instances"][i].setdefault("excel_filename", "")
                st.session_state["instances"][i].setdefault("quality_blocked", False)

            _start_label = "Normaltouren-Start CSV (für Verspätung)" if _is_normal else "Sonderwochen-Tourenstart CSV (optional, sonst Normaltouren)"
            start_csv = st.file_uploader(_start_label, type=["csv"], key=f"versp_start_csv_{i}")
            if start_csv:
                _start_quality = _validate_uploaded_file(
                    start_csv, ["csv"], f"quality_week_{i}_tourstart",
                    area=f"{_label} / Tourenstart", kind="csv", required=False,
                )
                _render_quality_result(_start_quality)
                if _start_quality.get("status") == "error":
                    start_csv = None
            if start_csv:
                _start_sig = combine_signatures(EXTRA_CACHE_VERSION, "versp_start_csv", upload_signature(start_csv))
                if st.session_state["instances"][i].get("versp_start_sig") != _start_sig:
                    _status_key = f"week_{i}_tourstart"
                    try:
                        with st.spinner("Lese Tourenstart-CSV ..."):
                            _parsed_start = parse_versp_abfahrt_csv(start_csv)
                        st.session_state["instances"][i]["versp_start_json"] = _parsed_start
                        st.session_state["instances"][i]["versp_start_sig"] = _start_sig
                        _record_processing_success(
                            _status_key, f"{_label} / Tourenstart",
                            "Erfolgreich verarbeitet", _uploaded_name(start_csv),
                        )
                    except Exception as exc:
                        _record_processing_error(
                            _status_key, f"{_label} / Tourenstart", exc, _uploaded_name(start_csv)
                        )
                        st.error(f"Tourenstart-CSV: {type(exc).__name__}: {exc}")
                try:
                    _vsp_obj = json.loads(st.session_state["instances"][i].get("versp_start_json") or "{}")
                    _vsp_n = len((_vsp_obj.get("__by_tour") or {})) if isinstance(_vsp_obj, dict) else 0
                    st.caption(f"Tourenstart: {_vsp_n} Abfahrten geladen")
                except Exception:
                    st.caption("Tourenstart-CSV geladen")
            elif st.session_state["instances"][i].get("versp_start_json") not in (None, "", "{}"):
                try:
                    _vsp_obj = json.loads(st.session_state["instances"][i].get("versp_start_json") or "{}")
                    _vsp_n = len((_vsp_obj.get("__by_tour") or {})) if isinstance(_vsp_obj, dict) else 0
                    st.caption(f"Tourenstart: {_vsp_n} Abfahrten geladen")
                except Exception:
                    st.caption("Tourenstart-CSV geladen")
            elif (not _is_normal) and st.session_state["instances"][0].get("versp_start_json") not in (None, "", "{}"):
                st.caption("Tourenstart: nutzt Normaltouren-CSV als Fallback")

            _logo       = st.session_state.get("g_logo")
            _key        = st.session_state.get("g_key")
            _fach       = st.session_state.get("g_fach")
            _fcsb       = st.session_state.get("g_fcsb")
            _lh_csv     = st.session_state.get("g_lh_csv")
            _rahmen_csv = st.session_state.get("g_rahmen_csv")
            _kundenart_csv = st.session_state.get("g_kundenart_csv")

            if excel and _logo and _key and not st.session_state["instances"][i].get("quality_blocked", False):
                current_source_sig = combine_signatures(
                    APP_CACHE_VERSION,
                    upload_signature(excel),  upload_signature(_logo),
                    upload_signature(_key),   upload_signature(_fach),
                    upload_signature(_fcsb),  upload_signature(_lh_csv),
                    upload_signature(_rahmen_csv), upload_signature(_kundenart_csv),
                )
                if (inst.get("source_sig") != current_source_sig
                        or not inst.get("suche_html")
                        or not inst.get("druck_html")):
                    try:
                        with st.spinner("Generiere Suche + Druck ..."):
                            st.session_state["instances"][i]["suche_html"] = generate_suche_html(
                                excel, _key, _logo, _fach, _fcsb,
                                lieferhinweis_csv=_lh_csv, rahmentour_csv=_rahmen_csv,
                                kundenart_csv=_kundenart_csv
                            )
                            st.session_state["instances"][i]["druck_html"] = generate_druck_html(
                                excel, _logo, _fcsb, lieferhinweis_csv=_lh_csv
                            )
                            try:
                                st.session_state["instances"][i]["woche_data"] = compute_woche_data(excel)
                            except Exception as exc:
                                st.session_state["instances"][i]["woche_data"] = {}
                                _record_processing_error(
                                    f"week_{i}_data", f"{_label} / Wochendaten",
                                    exc, _uploaded_name(excel),
                                )
                            else:
                                _record_processing_success(
                                    f"week_{i}_data", f"{_label} / Wochendaten",
                                    "Wochendaten berechnet", _uploaded_name(excel),
                                )
                            st.session_state["instances"][i]["source_sig"] = current_source_sig
                        kb_s = len(st.session_state["instances"][i]["suche_html"]) // 1024
                        kb_d = len(st.session_state["instances"][i]["druck_html"]) // 1024
                        _record_processing_success(
                            f"week_{i}_html", f"{_label} / HTML",
                            f"Suche {kb_s} KB, Druck {kb_d} KB", _uploaded_name(excel),
                        )
                        st.success(f"Fertig: Suche {kb_s} KB, Druck {kb_d} KB")
                    except Exception as e:
                        _record_processing_error(
                            f"week_{i}_html", f"{_label} / HTML", e, _uploaded_name(excel)
                        )
                        st.error(f"Fehler: {e}")
                else:
                    kb_s = len(inst["suche_html"]) // 1024
                    kb_d = len(inst["druck_html"]) // 1024
                    st.caption(f"Bereit: Suche {kb_s} KB, Druck {kb_d} KB")
            elif inst["suche_html"] and inst["druck_html"]:
                kb_s = len(inst["suche_html"]) // 1024
                kb_d = len(inst["druck_html"]) // 1024
                st.caption(f"Bereit: Suche {kb_s} KB, Druck {kb_d} KB")
            else:
                missing = []
                if not excel: missing.append("Wochen-Excel")
                if excel and st.session_state["instances"][i].get("quality_blocked", False): missing.append("gueltige Wochen-Excel")
                if not _logo: missing.append("Logo")
                if not _key:  missing.append("Schluesseldatei")
                if missing: st.caption("Fehlt: " + ", ".join(missing))

            if i > 0:
                if st.button("Entfernen", key=f"del_inst_{i}"):
                    st.session_state["instances"].pop(i)
                    st.rerun()

    _week_duplicates = _duplicate_week_groups()
    if _week_duplicates:
        for _dup_group in _week_duplicates:
            st.error("Doppelte Wochen-Datei erkannt: " + " = ".join(
                f"{x['name']} ({x['filename'] or 'ohne Dateiname'})" for x in _dup_group
            ))

    if st.button("Woche hinzufuegen"):
        n = len(st.session_state["instances"])
        st.session_state["instances"].append(_empty_inst(f"Sonderwoche {n}"))
        st.rerun()

# === Tab: Zusatzdateien ======================================================
_boot_log("10 Tab Zusatzdateien wird aufgebaut")
with tab_extra:
    col_l, col_r = st.columns(2)

    with col_l:
        _extra_single_upload(
            "Telefonliste (Excel)", ["xlsx"], "tel",
            parse_telefon_excel,
            summary_fn=lambda j: f"Telefonliste: {len(json.loads(j))} Gruppen"
        )

        def _touren_summary(ups):
            _zd  = json.loads(st.session_state.zulage_json)
            _zns = sum(len(m["fahrer"]) for m in _zd.get("sonder",   []))
            _znf = sum(len(m["fahrer"]) for m in _zd.get("fuengers", []))
            _dkd = json.loads(st.session_state.drittkunden_json)
            _fan = len(json.loads(st.session_state.fa_json))
            _spd = json.loads(st.session_state.get("spediteure_json", '{"katalog":[],"fahrten":[]}'))
            return (f"{len(ups)} Dateien: Sonder {_zns}, Fuengers {_znf}, "
                    f"Drittkunden {len(_dkd)}, Fahrer {_fan}, "
                    f"Spediteure {len(_spd.get('katalog', []))} ({len(_spd.get('fahrten', []))} Fahrten)")
        _extra_multi_upload(
            "Touren-Dateien (Zulagen, Drittkunden, Fahrerauswertung)",
            ["xlsx"], "touren",
            {
                "zulage_json":      parse_zulage_excel,
                "drittkunden_json": parse_drittkunden_excel,
                "fa_json":          parse_fahrer_excel,
                "spediteure_json":  parse_spediteure_excel,
            },
            summary_fn=_touren_summary,
        )

        def _fw_summary(ups):
            rows  = json.loads(st.session_state.get("fahrzeugwaesche_json", "[]"))
            drv   = len({(r.get("fahrer") or "").strip() for r in rows if (r.get("fahrer") or "").strip()})
            lkw   = len({((r.get("fahrzeug") or r.get("fahrzeug_ia") or "").strip())
                         for r in rows if ((r.get("fahrzeug") or r.get("fahrzeug_ia") or "").strip())})
            return f"{len(rows)} Waschungen, {drv} Fahrer, {lkw} LKW"
        _extra_multi_upload(
            "Fahrzeugwaesche (Excel)", ["xlsx", "xls"], "fahrzeugwaesche",
            {"fahrzeugwaesche_json": parse_fahrzeugwaesche_excel},
            summary_fn=_fw_summary,
        )

    with col_r:
        def _spesen_summary(j):
            sp     = json.loads(j or "{}")
            drv    = sp.get("drivers", [])
            total  = float(sp.get("total_cost", 0) or 0)
            rows   = int(sp.get("total_rows", 0) or 0)
            return f"{rows} Zeilen, {len(drv)} Fahrer, {_format_eur(total)}"
        _extra_single_upload(
            "Spesen / Reisekosten (CSV)", ["csv"], "spesen",
            parse_spesen_csv, summary_fn=_spesen_summary,
        )

        def _verstoss_summary(j):
            vs    = json.loads(j or '{"drivers":[]}')
            drv   = vs.get("drivers", [])
            total = vs.get("total_violations", 0)
            dp    = sum(d.get("sum_driver_penalty",  0) for d in drv)
            cp    = sum(d.get("sum_company_penalty", 0) for d in drv)
            return (f"{len(drv)} Fahrer, {total} Verstoesse, "
                    f"Fahrer {dp:,} EUR, Firma {cp:,} EUR").replace(",", ".")
        _extra_single_upload(
            "Verstossauswertung Digitacho (CSV)", ["csv"], "verstoss",
            parse_verstoss_csv, summary_fn=_verstoss_summary,
        )

        _extra_single_upload(
            "Grosskunden (Excel)", ["xlsx"], "grosskunden",
            parse_grosskunden_excel,
            summary_fn=lambda j: f"{len(json.loads(j))} Kunden geladen",
            spinner_text="Verarbeite Grosskunden-Excel ...",
        )

        def _timerec_summary(j):
            tr  = json.loads(j or "{}")
            return f"{len(tr)} Fahrer, {sum(len(v) for v in tr.values())} Schichten"
        _extra_single_upload(
            "Schichten / Tachograph (CSV: timerecording_v3*.csv)", ["csv"],
            "timerec", parse_timerecording_csv,
            summary_fn=_timerec_summary,
            spinner_text="Verarbeite Schichten-CSV ...",
        )

        def _fahrerbewertung_summary(j):
            fb  = json.loads(j or "{}")
            drv = fb.get("drivers", [])
            grd = sum(1 for d in drv if d.get("grade") is not None)
            ev  = sum(d.get("evt", 0) for d in drv)
            mon = len(fb.get("g_months", {}))
            return f"{len(drv)} Fahrer ({grd} bewertet), {ev} Ereignisse, {mon} Monate"
        _extra_single_upload(
            "Fahrerbewertung Rohdaten (JSON: d_rohdaten.json)", ["json"],
            "fahrerbewertung", parse_fahrerbewertung_json,
            summary_fn=_fahrerbewertung_summary,
            spinner_text="Verarbeite Fahrerbewertungs-JSON ...",
        )


# === Tab: Download ===========================================================
# Die große Einzeldatei wird nicht mehr bei jedem Streamlit-Rerun neu gebaut.
# Das ist besonders auf Streamlit Cloud wichtig, weil ein String plus mehrere
# UTF-8-Kopien der HTML kurzzeitig sehr viel RAM belegen können.
_boot_log("11 Tab Download wird aufgebaut")
with tab_dl:
    import gc as _gc

    instances_state = st.session_state.get("instances")
    if not isinstance(instances_state, list) or not instances_state:
        instances_state = [_empty_inst("Normalwochen")]
        st.session_state["instances"] = instances_state

    ready = [inst for inst in instances_state
             if inst.get("suche_html") and inst.get("druck_html")
             and not inst.get("quality_blocked", False)]

    _quality_rows = _quality_table_rows()
    if _quality_rows:
        _quality_has_issue = any(row.get("Status") != "✓ OK" for row in _quality_rows)
        with st.expander("Dateiqualität", expanded=_quality_has_issue):
            st.dataframe(_quality_rows, width="stretch", hide_index=True)

    st.markdown("##### Startprüfung")
    _preflight_rows, _preflight_blockers, _estimated_html_size = _build_export_preflight(ready)
    st.dataframe(_preflight_rows, width="stretch", hide_index=True)
    if _preflight_blockers:
        st.warning("Vor der HTML-Erstellung fehlt noch: " + ", ".join(_preflight_blockers))
    else:
        st.success(f"Startprüfung bestanden · erwartete Größe ca. {_human_size(_estimated_html_size)}")

    if ready:
        zulage_json_state      = st.session_state.get("zulage_json", "{}")
        drittkunden_json_state = st.session_state.get("drittkunden_json", "[]")
        zulage_xlsx_sonder = (
            _safe_cached_export_b64(
                "zulage_sonder", zulage_json_state,
                lambda v: generate_zulage_excel(v, tab="sonder"),
                "Excel-Export Sonderzulagen",
            )
            if zulage_json_state not in ("{}", "") else ""
        )
        zulage_xlsx_fuengers = (
            _safe_cached_export_b64(
                "zulage_fuengers", zulage_json_state,
                lambda v: generate_zulage_excel(v, tab="fuengers"),
                "Excel-Export Fuengers",
            )
            if zulage_json_state not in ("{}", "") else ""
        )
        zulage_xlsx_drittkunden = (
            _safe_cached_export_b64(
                "drittkunden", drittkunden_json_state, generate_drittkunden_excel,
                "Excel-Export Drittkunden",
            )
            if drittkunden_json_state not in ("[]", "") else ""
        )

        current_generation_signature = _generation_source_signature(ready)
        stored_generation_meta = st.session_state.get("_generated_app_html_meta", {}) or {}
        stored_html_path = st.session_state.get("_generated_app_html_path")
        if stored_html_path and not Path(stored_html_path).is_file():
            _clear_generated_html_file()
            stored_html_path = None
        if (stored_html_path
                and stored_generation_meta.get("source_signature") != current_generation_signature):
            _clear_generated_html_file()
            _gc.collect()

        st.markdown("##### HTML-Einzeldatei")
        st.caption(
            "Die große suche.html wird bewusst nur auf Knopfdruck erstellt. "
            "Dadurch wird sie bei Uploads, Tabwechseln oder anderen Streamlit-Neuläufen "
            "nicht mehrfach im Arbeitsspeicher aufgebaut."
        )

        build_clicked = st.button(
            "suche.html erstellen / aktualisieren",
            type="primary",
            width="stretch",
            key="build_suche_html",
            disabled=bool(_preflight_blockers),
        )

        if build_clicked:
            # Alte Exportdaten zuerst freigeben, damit während der Neuerstellung
            # nicht zwei große HTML-Dateien gleichzeitig im Speicher liegen.
            _clear_generated_html_file()
            _gc.collect()

            generated_at = datetime.datetime.now()
            generation_meta = _build_generation_metadata(ready, generated_at)
            _build_progress = st.progress(5, text="1/5 Startprüfung abgeschlossen")
            try:
                _generation_started = time.perf_counter()
                _build_progress.progress(20, text="2/5 Zusatzdaten und Metadaten vorbereiten")
                _build_progress.progress(35, text="3/5 Wochen, Auswertungen und PDFs zusammenführen")
                app_html = combine_html(
                        instances=ready,
                        tel_json=st.session_state.get("tel_json", "[]"),
                        sam_json=st.session_state.get("sam_json", "[]"),
                        fa_json=st.session_state.get("fa_json", "[]"),
                        zulage_json=zulage_json_state,
                        zulage_xlsx_sonder=zulage_xlsx_sonder,
                        zulage_xlsx_fuengers=zulage_xlsx_fuengers,
                        drittkunden_json=drittkunden_json_state,
                        zulage_xlsx_drittkunden=zulage_xlsx_drittkunden,
                        fahrzeugwaesche_json=st.session_state.get("fahrzeugwaesche_json", "[]"),
                        verstoss_json=st.session_state.get("verstoss_json", '{"drivers":[],"total_violations":0}'),
                        spesen_json=st.session_state.get("spesen_json", '{"drivers":[],"months":[],"total_cost":0,"total_rows":0}'),
                        grosskunden_json=st.session_state.get("grosskunden_json", "[]"),
                        timerec_json=st.session_state.get("timerec_json", "{}"),
                        spediteure_json=st.session_state.get("spediteure_json", '{"katalog":[],"fahrten":[]}'),
                        fahrerbewertung_json=st.session_state.get("fahrerbewertung_json", '{"profile":"","event_types":[],"g_months":{},"g_ev":{},"drivers":[]}'),
                        versp_abfahrt_json="{}",
                        last_updated=generated_at.strftime("Stand: %d.%m.%Y %H:%M"),
                        generation_meta=generation_meta,
                    )

                _build_progress.progress(78, text="4/5 HTML-Datei auf dem Server speichern")

                # Direkt auf die Platte schreiben. TextIOWrapper kodiert
                # schrittweise, sodass keine zweite vollständige Byte-Kopie
                # der großen HTML im RAM entsteht.
                import tempfile as _tempfile
                with _tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="",
                    suffix=".html",
                    prefix="nfc_suche_",
                    delete=False,
                ) as _tmp_html:
                    _tmp_html.write(app_html)
                    generated_html_path = _tmp_html.name
                del app_html

                _build_progress.progress(92, text="5/5 Dateigröße und Download vorbereiten")
                generation_seconds = time.perf_counter() - _generation_started
                generated_html_size = Path(generated_html_path).stat().st_size
                _pdf_count, _pdf_bytes = _embedded_pdf_stats()
                generated_meta = {
                    "created_at": generated_at,
                    "generation_seconds": generation_seconds,
                    "html_size": generated_html_size,
                    "pdf_count": _pdf_count,
                    "pdf_bytes": _pdf_bytes,
                    "week_names": [str(i.get("name", "")) for i in ready],
                    "source_signature": current_generation_signature,
                }
                st.session_state["_generated_app_html_path"] = generated_html_path
                st.session_state["_generated_app_html_meta"] = generated_meta
                _record_processing_success(
                    "download_html", "Gesamtdatei suche.html",
                    f"{_human_size(generated_html_size)}, {len(ready)} Woche(n), {generation_seconds:.2f} s",
                )
                _build_progress.progress(100, text=f"Fertig · {_human_size(generated_html_size)} in {generation_seconds:.2f} s")
                st.success("suche.html wurde erfolgreich erstellt und steht zum Download bereit.")
                _gc.collect()
            except Exception as exc:
                try:
                    if 'generated_html_path' in locals():
                        Path(generated_html_path).unlink(missing_ok=True)
                except Exception:
                    pass
                _clear_generated_html_file()
                _record_processing_error("download_html", "Gesamtdatei suche.html", exc)
                try:
                    _build_progress.progress(100, text="Erstellung abgebrochen – Fehlerdetails stehen unten im Status")
                except Exception:
                    pass
                st.error(f"suche.html konnte nicht erzeugt werden: {type(exc).__name__}: {exc}")
                _gc.collect()

        generated_html_path = st.session_state.get("_generated_app_html_path")
        generated_meta = st.session_state.get("_generated_app_html_meta", {}) or {}

        if generated_html_path and Path(generated_html_path).is_file():
            # Callable: Die Datei wird erst beim Klick gelesen und nicht dauerhaft
            # als zusätzlicher Bytes-Block im Streamlit-Session-Speicher gehalten.
            st.download_button(
                label="suche.html herunterladen",
                data=lambda p=generated_html_path: _read_generated_html_file(p),
                file_name="suche.html",
                mime="text/html",
                type="primary",
                width="stretch",
                on_click="ignore",
                key="download_suche_html",
            )

            st.caption(
                "Eigenständige Einzeldatei: Alle PDFs sind komprimiert in suche.html enthalten. "
                "Es wird kein zusätzlicher Ordner benötigt."
            )

            created_at = generated_meta.get("created_at")
            generation_seconds = float(generated_meta.get("generation_seconds", 0.0) or 0.0)
            html_size = int(generated_meta.get("html_size", Path(generated_html_path).stat().st_size) or Path(generated_html_path).stat().st_size)
            pdf_count = int(generated_meta.get("pdf_count", 0) or 0)
            pdf_bytes = int(generated_meta.get("pdf_bytes", 0) or 0)
            week_names = generated_meta.get("week_names", []) or []

            st.markdown("##### Generierungsstatistik")
            _g1, _g2, _g3, _g4 = st.columns(4)
            _g1.metric("Version", f"v{APP_DISPLAY_VERSION}")
            _g2.metric("HTML-Datei", _human_size(html_size))
            _g3.metric("Erzeugungszeit", f"{generation_seconds:.2f} s")
            _g4.metric("Eingebettete PDFs", f"{pdf_count} · {_human_size(pdf_bytes)}")
            if isinstance(created_at, datetime.datetime):
                created_label = created_at.strftime("%d.%m.%Y %H:%M:%S")
            else:
                created_label = str(created_at or "-")
            st.caption(
                f"Datenstand: {created_label} · "
                f"{len(week_names)} Woche(n): {', '.join(week_names)}"
            )
        else:
            st.info("Nach dem letzten Daten-Upload wurde noch keine aktuelle suche.html erstellt.")

        plane_zulagen_json = build_plane_zulagen_json(
            zulage_json_state,
            drittkunden_json_state,
            generated_at=datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
        )
        st.download_button(
            label="zulagen.json herunterladen",
            data=plane_zulagen_json,
            file_name="zulagen.json",
            mime="application/json",
            width="stretch",
            on_click="ignore",
        )
    else:
        st.button(
            "suche.html erstellen / aktualisieren",
            type="primary",
            width="stretch",
            key="build_suche_html_disabled",
            disabled=True,
        )
        st.info("Mindestens Logo, Schlüsseldatei und eine vollständig verarbeitete Wochen-Excel hochladen.")
        zulage_json_state      = st.session_state.get("zulage_json", "{}")
        drittkunden_json_state = st.session_state.get("drittkunden_json", "[]")
        if zulage_json_state not in ("{}", "") or drittkunden_json_state not in ("[]", ""):
            plane_zulagen_json = build_plane_zulagen_json(
                zulage_json_state,
                drittkunden_json_state,
                generated_at=datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            )
            st.download_button(
                label="zulagen.json herunterladen",
                data=plane_zulagen_json,
                file_name="zulagen.json",
                mime="application/json",
                width="stretch",
                on_click="ignore",
            )

# === Vollständiger Reset =====================================================
st.divider()
with st.expander("App vollständig zurücksetzen", expanded=False):
    st.warning(
        "Dabei werden alle Uploads, verarbeiteten Daten, Statusmeldungen, Caches "
        "und die temporär erzeugte suche.html dieser Sitzung entfernt."
    )
    _reset_confirmed = st.checkbox(
        "Ich möchte wirklich alle Daten dieser Sitzung löschen.",
        key="_confirm_full_app_reset",
    )
    if st.button(
        "Alle Daten und Uploads zurücksetzen",
        width="stretch",
        disabled=not _reset_confirmed,
        key="full_app_reset",
    ):
        _reset_all_app_data()
        st.rerun()

# === Zentrale Verarbeitungsanzeige ===========================================
st.divider()
_status_entries = list(st.session_state.get("_processing_status", {}).values())
_error_entries = list(st.session_state.get("_processing_errors", []))
_error_count = sum(1 for row in _status_entries if row.get("status") == "error")
_warning_count = sum(1 for row in _status_entries if row.get("status") == "warning")
_ok_count = sum(1 for row in _status_entries if row.get("status") == "ok")

with st.expander(
    f"Verarbeitungsstatus: {_ok_count} erfolgreich, {_warning_count} Hinweise, {_error_count} Fehler",
    expanded=bool(_error_count),
):
    if not _status_entries:
        st.info("Noch keine Dateien verarbeitet.")
    else:
        _status_label = {
            "ok": "✓ Erfolgreich",
            "warning": "⚠ Hinweis",
            "error": "✗ Fehler",
            "info": "• Vorhanden",
        }
        _rows = []
        for row in sorted(_status_entries, key=lambda x: (x.get("bereich", "").lower(), x.get("zeit", ""))):
            _rows.append({
                "Status": _status_label.get(row.get("status"), row.get("status", "")),
                "Bereich": row.get("bereich", ""),
                "Datei": row.get("datei", ""),
                "Details": row.get("detail", ""),
                "Zeit": row.get("zeit", ""),
            })
        st.dataframe(_rows, width="stretch", hide_index=True)

    if _error_entries:
        with st.expander("Technische Fehlerdetails", expanded=False):
            for err in reversed(_error_entries[-20:]):
                st.markdown(f"**{err.get('bereich', 'Unbekannter Bereich')}** — {err.get('zeit', '')}")
                if err.get("datei"):
                    st.caption(f"Datei: {err['datei']}")
                st.code(f"{err.get('fehlertyp', 'Fehler')}: {err.get('fehler', '')}")

        _error_export = json.dumps(
            [
                {k: v for k, v in err.items() if k != "signature"}
                for err in _error_entries
            ],
            ensure_ascii=False,
            indent=2,
        )
        st.download_button(
            "Fehlerprotokoll herunterladen",
            data=_error_export.encode("utf-8"),
            file_name="nfc_generator_fehlerprotokoll.json",
            mime="application/json",
            width="stretch",
        )

    _status_col1, _status_col2 = st.columns(2)
    with _status_col1:
        if st.button("Fehlerhistorie leeren", width="stretch"):
            st.session_state["_processing_errors"] = []
            st.rerun()
    with _status_col2:
        if st.button("Statusanzeige zuruecksetzen", width="stretch"):
            st.session_state["_processing_status"] = {}
            st.session_state["_processing_errors"] = []
            st.rerun()



_boot_log("99 App-Skript vollständig ausgeführt")

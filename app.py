"""
Comprehension Time (TTA) Evaluation App  —  v5
==================================================
Major changes vs v4:
  • Per-participant condition assignment (between-subjects design):
    each participant is assigned ONE language AND ONE condition by the admin,
    and answers all 50 questions in that single condition.
    This matches Jain et al. (2024) §8.7: "Each of these combinations are
    presented to different annotators while ensuring that no annotator see
    the same question twice."
  • Within mind-map and combined conditions, the model (Gemini vs Qwen) is
    randomly assigned per-question (~25 each).
  • Admin tab gets a third field per participant: condition.
  • Built-in statistics tab runs paired t-tests between conditions.

Storage: Google Sheets (preferred) or local SQLite fallback.
"""
from __future__ import annotations

import base64
import json
import logging as _logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st


class _SuppressDeprecation(_logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return not (("st.components.v1.html" in msg)
                    or ("use_container_width" in msg))
for _name in ("streamlit", "streamlit.runtime", "root"):
    _logging.getLogger(_name).addFilter(_SuppressDeprecation())
_logging.getLogger().addFilter(_SuppressDeprecation())


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
LOCAL_DB_PATH = APP_DIR / "tta_local.db"

LANGS = {"en": "English", "ar": "العربية", "tr": "Türkçe"}

MODELS = ["gem", "qwen"]
MODEL_LABEL = {"gem": "Gemini 2.5 Pro", "qwen": "Qwen 2.5-7B", "n/a": "—"}
MODEL_IMG_KEY = {"gem": "gemini", "qwen": "qwen"}

CONDITIONS = ["passage", "mindmap", "both"]
# Condition labels per language. The ⟨q,t⟩ / ⟨q,s⟩ / ⟨q,s+t⟩ notation is
# kept verbatim across languages (it's the standard formal notation), but
# the "Passage / Mind map / Both" parts are translated.
CONDITION_LABEL = {
    "en": {
        "passage": "⟨q, t⟩  Passage only",
        "mindmap": "⟨q, s⟩  Mind map only",
        "both":    "⟨q, s+t⟩  Mind map + Passage",
    },
    "ar": {
        "passage": "⟨q, t⟩  النص فقط",
        "mindmap": "⟨q, s⟩  الخريطة الذهنية فقط",
        "both":    "⟨q, s+t⟩  الخريطة الذهنية + النص",
    },
    "tr": {
        "passage": "⟨q, t⟩  Yalnızca metin",
        "mindmap": "⟨q, s⟩  Yalnızca zihin haritası",
        "both":    "⟨q, s+t⟩  Zihin haritası + Metin",
    },
}
# Backward-compat shim for the previous single-language constant used by
# the admin dashboard (which is English-only).
CONDITION_LABEL_EN = CONDITION_LABEL["en"]

DEFAULT_TARGET_TRIALS = 50

UI = {
    "en": {
        "app_title":         "Comprehension-Time (TTA) Study",
        "app_subtitle":      "Multilingual reading comprehension experiment",
        "login_intro":       "Type the exact name your administrator assigned. Your test language and condition are set by the administrator.",
        "login_name":        "Your name (or initials)",
        "login_continue":    "Continue",
        "login_err_empty":   "Please enter your name.",
        "login_err_unknown": "Your name is not in the participant list. Please contact the administrator.",
        "login_err_no_cond": "Your participant record is missing a condition assignment. Please contact the administrator.",
        "ui_lang_caption":   "Interface language",
        "consent_title":     "Welcome — please read before starting",
        "consent_body": (
            "This study measures how quickly you can answer reading-comprehension "
            "questions. Each participant is assigned one viewing condition for "
            "the entire session.\n\n"
            "**The timer starts the moment the question and content appear, and "
            "stops when you click _Submit_.** Read at your normal pace.\n\n"
            "After a brief practice question, you will answer 50 questions in your "
            "assigned condition."),
        "consent_btn":       "I agree — start the test",
        "question":          "Question",
        "passage":           "Passage",
        "mindmap":           "Mind map",
        "answer":            "Your answer",
        "submit":            "Submit",
        "progress":          "Progress",
        "done":              "All questions completed. Thank you!",
        "elapsed":           "Time on this question",
        "practice_done":     "Practice finished — the real test starts now.",
        "practice_label":    "Practice question (not recorded)",
        "words":             "words",
        "zoom_in":           "Zoom in",
        "zoom_out":          "Zoom out",
        "reset":             "Reset",
        "drag_hint":         "drag to pan • Ctrl + scroll to zoom",
        "trial_of":          "of",
    },
    "ar": {
        "app_title":         "دراسة وقت الفهم (TTA)",
        "app_subtitle":      "تجربة فهم القراءة متعددة اللغات",
        "login_intro":       "اكتب الاسم الذي خصصه لك المسؤول بالضبط. لغة الاختبار وشرطه يحددهما المسؤول.",
        "login_name":        "اسمك (أو الأحرف الأولى)",
        "login_continue":    "متابعة",
        "login_err_empty":   "يرجى إدخال اسمك.",
        "login_err_unknown": "اسمك غير موجود في قائمة المشاركين. يرجى التواصل مع المسؤول.",
        "login_err_no_cond": "سجل المشارك ينقصه تخصيص الشرط. يرجى التواصل مع المسؤول.",
        "ui_lang_caption":   "لغة الواجهة",
        "consent_title":     "مرحبًا — يرجى القراءة قبل البدء",
        "consent_body": (
            "تهدف هذه الدراسة إلى قياس مدى سرعتك في الإجابة عن أسئلة فهم "
            "القراءة. كل مشارك يأخذ شرط عرض واحد طوال الجلسة.\n\n"
            "**يبدأ المؤقت لحظة ظهور السؤال والمحتوى، ويتوقف عند الضغط "
            "على _إرسال_.** اقرأ بطبيعتك.\n\n"
            "بعد سؤال تدريبي قصير، ستجيب على 50 سؤالاً في شرطك المخصص."),
        "consent_btn":       "أوافق — ابدأ الاختبار",
        "question":          "السؤال",
        "passage":           "النص",
        "mindmap":           "الخريطة الذهنية",
        "answer":            "إجابتك",
        "submit":            "إرسال",
        "progress":          "التقدم",
        "done":              "تم الانتهاء من جميع الأسئلة. شكرًا لك!",
        "elapsed":           "الوقت في هذا السؤال",
        "practice_done":     "انتهى التدريب — يبدأ الاختبار الفعلي الآن.",
        "practice_label":    "سؤال تدريبي (لا يُسجَّل)",
        "words":             "كلمة",
        "zoom_in":           "تكبير",
        "zoom_out":          "تصغير",
        "reset":             "إعادة",
        "drag_hint":         "اسحب للتنقل • Ctrl + التمرير للتكبير",
        "trial_of":          "من",
    },
    "tr": {
        "app_title":         "Anlama Süresi (TTA) Çalışması",
        "app_subtitle":      "Çok dilli okuma anlama deneyi",
        "login_intro":       "Lütfen yöneticinin size verdiği adı tam olarak girin. Test diliniz ve koşulunuz yönetici tarafından belirlenir.",
        "login_name":        "Adınız (veya baş harfler)",
        "login_continue":    "Devam",
        "login_err_empty":   "Lütfen adınızı girin.",
        "login_err_unknown": "Adınız katılımcı listesinde yok. Lütfen yönetici ile iletişime geçin.",
        "login_err_no_cond": "Katılımcı kaydınızda koşul ataması eksik. Lütfen yönetici ile iletişime geçin.",
        "ui_lang_caption":   "Arayüz dili",
        "consent_title":     "Hoş geldiniz — başlamadan önce lütfen okuyun",
        "consent_body": (
            "Bu çalışma, okuma anlama sorularını ne kadar hızlı "
            "cevaplayabildiğinizi ölçer. Her katılımcı, oturum boyunca "
            "tek bir görüntüleme koşulu alır.\n\n"
            "**Zamanlayıcı, soru ve içerik ekranda göründüğü anda başlar ve "
            "_Gönder_ düğmesine bastığınızda durur.** Doğal hızınızla okuyun.\n\n"
            "Kısa bir alıştırma sorusundan sonra, atanmış koşulunuzda 50 "
            "soru cevaplayacaksınız."),
        "consent_btn":       "Kabul ediyorum — testi başlat",
        "question":          "Soru",
        "passage":           "Metin",
        "mindmap":           "Zihin haritası",
        "answer":            "Cevabınız",
        "submit":            "Gönder",
        "progress":          "İlerleme",
        "done":              "Tüm sorular tamamlandı. Teşekkürler!",
        "elapsed":           "Bu sorudaki süre",
        "practice_done":     "Alıştırma bitti — gerçek test başlıyor.",
        "practice_label":    "Alıştırma sorusu (kaydedilmez)",
        "words":             "kelime",
        "zoom_in":           "Yakınlaştır",
        "zoom_out":          "Uzaklaştır",
        "reset":             "Sıfırla",
        "drag_hint":         "kaydırmak için sürükleyin • yakınlaştırmak için Ctrl + kaydırma",
        "trial_of":          "/",
    },
}

PRACTICE = {
    "en": {
        "question": "What is the capital of France?",
        "passage":  ("France is a country in Western Europe. Its capital city, "
                     "Paris, is one of the most visited cities in the world."),
    },
    "ar": {
        "question": "ما هي عاصمة فرنسا؟",
        "passage":  ("فرنسا دولة تقع في أوروبا الغربية. عاصمتها باريس هي من "
                     "أكثر المدن زيارة في العالم."),
    },
    "tr": {
        "question": "Fransa'nın başkenti nedir?",
        "passage":  ("Fransa, Batı Avrupa'da bir ülkedir. Başkenti Paris, "
                     "dünyanın en çok ziyaret edilen şehirlerinden biridir."),
    },
}


def get_storage():
    if "storage" in st.session_state:
        return st.session_state.storage
    backend = None; err = None
    has_secrets = False
    try: has_secrets = "gcp_service_account" in st.secrets
    except Exception: pass
    if has_secrets:
        try: backend = GSpreadStorage()
        except Exception as e: err = e
    if backend is None:
        if err is not None:
            st.warning(f"Google Sheets unavailable ({err}). Falling back to local DB.")
        backend = SQLiteStorage()
    st.session_state.storage = backend
    return backend


class SQLiteStorage:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS responses (
        ts TEXT, participant TEXT, language TEXT,
        sample_id INTEGER, text_id TEXT, qid TEXT,
        condition TEXT, model TEXT,
        question TEXT, gold_answer TEXT, given_answer TEXT,
        tta_seconds REAL,
        PRIMARY KEY (participant, language, sample_id, qid)
    );
    CREATE TABLE IF NOT EXISTS participants (
        name TEXT PRIMARY KEY, language TEXT NOT NULL,
        condition TEXT NOT NULL DEFAULT 'passage'
    );
    """
    backend_name = "SQLite (local)"

    def __init__(self):
        self.conn = sqlite3.connect(str(LOCAL_DB_PATH), check_same_thread=False)
        # Make SQLite as resilient as possible while still being a local file:
        # - WAL = Write-Ahead Log (better concurrent reads, faster commits)
        # - synchronous=FULL = fsync on every commit (no lost rows on crash)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
        except Exception:
            pass
        self.conn.executescript(self.SCHEMA)
        try:
            self.conn.execute("SELECT condition FROM participants LIMIT 1")
        except Exception:
            try:
                self.conn.execute("ALTER TABLE participants ADD COLUMN condition TEXT NOT NULL DEFAULT 'passage'")
            except Exception: pass
        self.conn.commit()

    def save(self, row):
        self.conn.execute(
            """INSERT OR REPLACE INTO responses
               (ts,participant,language,sample_id,text_id,qid,condition,model,
                question,gold_answer,given_answer,tta_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["ts"], row["participant"], row["language"],
             int(row["sample_id"]), row["text_id"], row["qid"],
             row["condition"], row["model"], row["question"],
             row["gold_answer"], row["given_answer"], float(row["tta_seconds"])))
        self.conn.commit()

    def list_done_qids(self, participant, language):
        cur = self.conn.execute(
            "SELECT sample_id, qid FROM responses WHERE participant=? AND language=?",
            (participant, language))
        return {(int(r[0]), r[1]) for r in cur.fetchall()}

    def fetch_all_df(self):
        return pd.read_sql_query("SELECT * FROM responses ORDER BY ts", self.conn)

    def delete_participant_data(self, participant, language=None):
        if language:
            self.conn.execute("DELETE FROM responses WHERE participant=? AND language=?",
                               (participant, language))
        else:
            self.conn.execute("DELETE FROM responses WHERE participant=?", (participant,))
        self.conn.commit()

    def save_participant(self, name, lang, condition):
        self.conn.execute(
            "INSERT OR REPLACE INTO participants (name,language,condition) VALUES (?,?,?)",
            (name.strip().lower(), lang, condition))
        self.conn.commit()

    def delete_participant(self, name):
        self.conn.execute("DELETE FROM participants WHERE name=?",
                           (name.strip().lower(),)); self.conn.commit()

    def list_participants(self):
        cur = self.conn.execute("SELECT name, language, condition FROM participants")
        return {r[0]: {"language": r[1], "condition": r[2] or "passage"}
                for r in cur.fetchall()}

    def get_db_bytes(self):
        if LOCAL_DB_PATH.exists():
            return LOCAL_DB_PATH.read_bytes()
        return b""


class GSpreadStorage:
    HEADERS = ["ts", "participant", "language", "sample_id", "text_id", "qid",
                "condition", "model", "question", "gold_answer",
                "given_answer", "tta_seconds"]
    PART_HEADERS = ["name", "language", "condition"]
    backend_name = "Google Sheets"

    def __init__(self):
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                   "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=scopes)
        gc = gspread.authorize(creds)
        url = st.secrets.get("gsheet_url")
        if not url:
            raise RuntimeError("Missing 'gsheet_url' in secrets")
        sh = gc.open_by_url(url)
        try: self.ws = sh.worksheet("responses")
        except Exception:
            self.ws = sh.add_worksheet("responses", rows=1000, cols=len(self.HEADERS))
            self.ws.append_row(self.HEADERS)
        try: self.eval_ws = sh.worksheet("participants")
        except Exception:
            self.eval_ws = sh.add_worksheet("participants", rows=200,
                                              cols=len(self.PART_HEADERS))
            self.eval_ws.append_row(self.PART_HEADERS)
        existing = self.ws.row_values(1)
        if existing != self.HEADERS:
            if not existing: self.ws.append_row(self.HEADERS)
            else: self.ws.update("A1", [self.HEADERS])
        existing_p = self.eval_ws.row_values(1)
        if existing_p != self.PART_HEADERS:
            if not existing_p: self.eval_ws.append_row(self.PART_HEADERS)
            else: self.eval_ws.update("A1", [self.PART_HEADERS])

    def save(self, row):
        self.ws.append_row([row.get(h, "") for h in self.HEADERS])

    def list_done_qids(self, participant, language):
        recs = self.ws.get_all_records()
        out = set()
        for r in recs:
            if (str(r.get("participant", "")).strip().lower() == participant.strip().lower()
                and str(r.get("language", "")).strip().lower() == language.strip().lower()):
                try: out.add((int(r.get("sample_id")), str(r.get("qid", ""))))
                except Exception: pass
        return out

    def fetch_all_df(self):
        recs = self.ws.get_all_records()
        return pd.DataFrame(recs, columns=self.HEADERS) if recs else pd.DataFrame(columns=self.HEADERS)

    def delete_participant_data(self, participant, language=None):
        recs = self.ws.get_all_records()
        keep = []
        for r in recs:
            same_p = str(r.get("participant", "")).strip().lower() == participant.strip().lower()
            if same_p:
                if language is None or str(r.get("language","")).strip().lower() == language.strip().lower():
                    continue
            keep.append([r.get(h, "") for h in self.HEADERS])
        self.ws.clear()
        self.ws.append_row(self.HEADERS)
        if keep:
            self.ws.append_rows(keep)

    def _eval_recs(self):
        try: return self.eval_ws.get_all_records()
        except Exception: return []

    def save_participant(self, name, lang, condition):
        nm = name.strip().lower()
        for i, r in enumerate(self._eval_recs(), start=2):
            if str(r.get("name", "")).strip().lower() == nm:
                self.eval_ws.update_cell(i, 2, lang)
                self.eval_ws.update_cell(i, 3, condition)
                return
        self.eval_ws.append_row([nm, lang, condition])

    def delete_participant(self, name):
        nm = name.strip().lower()
        for i, r in enumerate(self._eval_recs(), start=2):
            if str(r.get("name", "")).strip().lower() == nm:
                self.eval_ws.delete_rows(i); return

    def list_participants(self):
        out = {}
        for r in self._eval_recs():
            n = str(r.get("name", "")).strip().lower()
            l = str(r.get("language", "")).strip().lower()
            c = str(r.get("condition", "")).strip().lower() or "passage"
            if n and l:
                out[n] = {"language": l, "condition": c}
        return out

    def get_db_bytes(self):
        return b""


PARTICIPANTS_FILE = DATA_DIR / "participants.json"


def _load_participant_map():
    """Return {name: {'language': lang, 'condition': cond}}."""
    mapping = {}

    def _normalize(value):
        if isinstance(value, str):
            return {"language": value.strip().lower(), "condition": "passage"}
        if isinstance(value, dict):
            return {
                "language":  str(value.get("language", "")).strip().lower(),
                "condition": str(value.get("condition", "passage")).strip().lower(),
            }
        return None

    try:
        sec = st.secrets.get("participants", None)
        if sec:
            for n, v in dict(sec).items():
                norm = _normalize(v)
                if norm and norm["language"]:
                    mapping[str(n).strip().lower()] = norm
    except Exception: pass

    if PARTICIPANTS_FILE.exists():
        try:
            data = json.loads(PARTICIPANTS_FILE.read_text(encoding="utf-8"))
            for n, v in data.items():
                if n.startswith("_"): continue
                norm = _normalize(v)
                if norm and norm["language"]:
                    mapping[str(n).strip().lower()] = norm
        except Exception: pass

    try:
        for n, v in get_storage().list_participants().items():
            mapping[n.strip().lower()] = {
                "language":  v.get("language", "").strip().lower(),
                "condition": v.get("condition", "passage").strip().lower(),
            }
    except Exception: pass

    return mapping


def lookup_participant_assignment(name):
    return _load_participant_map().get(name.strip().lower())


@st.cache_data
def load_manifest(language):
    p = DATA_DIR / f"{language}_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


def build_queue(language, participant, condition, target_n=DEFAULT_TARGET_TRIALS):
    """Build a single-condition queue for this participant.

    All trials get the assigned condition. Within mindmap/both, model is
    assigned ~50/50 gem/qwen. Pool selection uses a *language-level* seed
    so every participant in the same language sees the same 50 questions
    (just in different conditions, which enables matched-pairs analysis).
    Trial order shuffled with a participant-level seed.
    """
    manifest = load_manifest(language)

    pool = []
    for rec in manifest["records"]:
        for q in rec["questions"]:
            pool.append({
                "sample_id":     rec["sample_id"],
                "text_id":       rec["text_id"],
                "title":         rec["title"],
                "original_text": rec["original_text"],
                "word_count":    rec["word_count"],
                "image_gemini":  rec["image_gemini"],
                "image_qwen":    rec["image_qwen"],
                "qid":           q["qid"],
                "question":      q["question"],
                "gold_answer":   q["gold_answer"],
            })

    # Language-level seed: same 50 questions across all participants in this language
    pool_rng = random.Random(f"pool|{language}")
    pool_rng.shuffle(pool)
    pool = pool[: min(target_n, len(pool))]

    trials = []
    for i, item in enumerate(pool):
        item = dict(item)
        item["condition"] = condition
        if condition == "passage":
            item["model"] = "n/a"
        else:
            # alternate gem/qwen for ~50/50 split
            item["model"] = "gem" if (i % 2 == 0) else "qwen"
        trials.append(item)

    # Participant-level seed: shuffle the 50 trials for presentation order
    rng = random.Random(f"{participant}|{language}|{condition}")
    rng.shuffle(trials)
    return trials


@st.cache_data(show_spinner=False)
def _load_image_b64(path_str):
    p = Path(path_str)
    if not p.exists(): return None
    return base64.b64encode(p.read_bytes()).decode("ascii")


def render_mindmap_image(language, sample_id, model_key, height=500, L=None):
    L = L or UI["en"]
    img_filename = f"{sample_id:02d}_{MODEL_IMG_KEY[model_key]}.webp"
    img_path = IMAGES_DIR / language / img_filename
    b64 = _load_image_b64(str(img_path))
    if b64 is None:
        st.error(f"Mind-map image missing: {img_path}")
        return
    src = f"data:image/webp;base64,{b64}"
    html = """
<html><head><style>
  body { margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
  #wrap { width:100%; height:__H__px; overflow:hidden;
          border:1px solid #e5e7eb; border-radius:12px;
          background:#ffffff; box-sizing:border-box;
          display:flex; flex-direction:column;
          box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
  .controls { background:#f9fafb; padding:8px 12px;
              border-bottom:1px solid #e5e7eb; display:flex;
              gap:6px; font-size:13px; align-items:center; flex-shrink:0; }
  .controls button { padding:5px 14px; border:1px solid #d1d5db;
                     background:#ffffff; border-radius:6px; cursor:pointer;
                     font-weight:500; transition:all 0.15s;
                     color:#374151; min-width:36px; }
  .controls button:hover { background:#f3f4f6; border-color:#9ca3af; }
  .controls button:active { transform:scale(0.96); }
  .controls .hint { color:#6b7280; font-size:12px; margin-left:auto; }
  #stage { flex:1; overflow:hidden; cursor:grab; position:relative;
           background:#fafbfc; }
  #stage.dragging { cursor:grabbing; }
  #img-wrapper { position:absolute; top:0; left:0;
                 transform-origin:top left;
                 will-change:transform; }
  #mm-img { display:block; max-width:none;
            image-rendering:-webkit-optimize-contrast;
            image-rendering:crisp-edges;
            user-select:none; -webkit-user-drag:none; }
</style></head>
<body>
  <div id="wrap">
    <div class="controls">
      <button id="btn-in"  title="__ZIN__">＋</button>
      <button id="btn-out" title="__ZOUT__">−</button>
      <button id="btn-rst" title="__RST__">⟲</button>
      <span class="hint">__HINT__</span>
    </div>
    <div id="stage">
      <div id="img-wrapper">
        <img id="mm-img" src="__SRC__" draggable="false"/>
      </div>
    </div>
  </div>
  <script>
    (function(){
      const stage = document.getElementById('stage');
      const wrap  = document.getElementById('img-wrapper');
      const img   = document.getElementById('mm-img');
      let scale = 1.0, tx = 0, ty = 0;
      let dragging = false, sx = 0, sy = 0;
      let baseFitScale = 1.0;
      function apply() {
        wrap.style.transform =
          `translate(${tx}px, ${ty}px) scale(${scale * baseFitScale})`;
      }
      function fit() {
        const sw = stage.clientWidth, sh = stage.clientHeight;
        const iw = img.naturalWidth, ih = img.naturalHeight;
        if (!iw || !ih) return;
        baseFitScale = Math.min(sw / iw, sh / ih, 1) * 0.95;
        scale = 1.0;
        tx = (sw - iw * baseFitScale) / 2;
        ty = (sh - ih * baseFitScale) / 2;
        apply();
      }
      img.onload = fit;
      window.addEventListener('resize', fit);
      document.getElementById('btn-in').onclick  = () => { scale *= 1.25; apply(); };
      document.getElementById('btn-out').onclick = () => { scale /= 1.25; apply(); };
      document.getElementById('btn-rst').onclick = () => { fit(); };
      stage.addEventListener('mousedown', e => {
        dragging = true; sx = e.clientX - tx; sy = e.clientY - ty;
        stage.classList.add('dragging');
      });
      window.addEventListener('mousemove', e => {
        if (!dragging) return;
        tx = e.clientX - sx; ty = e.clientY - sy; apply();
      });
      window.addEventListener('mouseup', () => {
        dragging = false; stage.classList.remove('dragging');
      });
      stage.addEventListener('wheel', e => {
        if (!e.ctrlKey) return;
        e.preventDefault();
        const rect = stage.getBoundingClientRect();
        const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
        const factor = e.deltaY < 0 ? 1.1 : 1/1.1;
        const oldEff = scale * baseFitScale;
        scale *= factor;
        const newEff = scale * baseFitScale;
        tx = cx - (cx - tx) * (newEff / oldEff);
        ty = cy - (cy - ty) * (newEff / oldEff);
        apply();
      }, { passive:false });
      stage.addEventListener('touchstart', e => {
        if (e.touches.length === 1) {
          dragging = true;
          sx = e.touches[0].clientX - tx;
          sy = e.touches[0].clientY - ty;
        }
      });
      stage.addEventListener('touchmove', e => {
        if (dragging && e.touches.length === 1) {
          tx = e.touches[0].clientX - sx;
          ty = e.touches[0].clientY - sy;
          apply();
        }
      });
      stage.addEventListener('touchend', () => { dragging = false; });
    })();
  </script>
</body></html>
"""
    html = (html.replace("__H__", str(height))
                .replace("__SRC__", src)
                .replace("__ZIN__", L["zoom_in"])
                .replace("__ZOUT__", L["zoom_out"])
                .replace("__RST__", L["reset"])
                .replace("__HINT__", L["drag_hint"]))
    st.components.v1.html(html, height=height + 8)


def inject_css(language=None):
    rtl = "rtl" if language == "ar" else "ltr"
    align = "right" if rtl == "rtl" else "left"
    st.markdown(f"""
    <style>
      .block-container {{ padding-top: 1.5rem; padding-bottom: 2rem;
                            max-width: 1200px; }}
      h1 {{ font-weight: 700; letter-spacing:-0.02em; }}
      h2, h3 {{ font-weight: 600; letter-spacing:-0.01em; }}
      .source-card {{
          background:#ffffff; border:1px solid #e5e7eb; border-radius:12px;
          padding:18px 22px; max-height:520px; overflow-y:auto;
          font-size:0.97rem; line-height:1.7; color:#1f2937;
          direction:{rtl}; text-align:{align};
          box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      }}
      .question-box {{
          background:linear-gradient(135deg,#fff7ed 0%, #ffedd5 100%);
          border:none; border-left:4px solid #f97316;
          border-radius:10px;
          padding:16px 22px; font-size:1.08rem; font-weight:600; color:#9a3412;
          direction:{rtl}; text-align:{align};
          box-shadow: 0 1px 3px rgba(249,115,22,0.1);
      }}
      .question-box b {{ display:block; font-size:0.78rem; text-transform:uppercase;
                          letter-spacing:0.08em; margin-bottom:6px;
                          color:#c2410c; font-weight:700; }}
      .progress-pill {{
          background:#eff6ff; color:#1e40af; padding:5px 14px;
          border-radius:20px; font-weight:600; font-size:0.88rem;
          display:inline-block; border:1px solid #dbeafe;
      }}
      .info-pill {{
          background:#f3f4f6; color:#374151; padding:4px 12px;
          border-radius:20px; font-weight:500; font-size:0.82rem;
          display:inline-block; border:1px solid #e5e7eb;
      }}
      .stProgress > div > div > div > div {{
          background:linear-gradient(90deg, #3b82f6 0%, #6366f1 100%);
      }}
      .stTextInput > div > div > input {{
          border-radius:8px; border:1px solid #d1d5db;
          padding:10px 14px; font-size:0.97rem;
      }}
      .stTextInput > div > div > input:focus {{
          border-color:#3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.1);
      }}
      .stButton > button {{ border-radius:8px; font-weight:600; }}
      hr {{ border:none; border-top:1px solid #e5e7eb; margin: 1.5rem 0; }}
    </style>""", unsafe_allow_html=True)


def login_screen():
    if "ui_lang" not in st.session_state:
        st.session_state.ui_lang = "en"
    ui = st.session_state.ui_lang
    L = UI[ui]
    inject_css()

    cols = st.columns([3, 1])
    with cols[0]:
        st.title(L["app_title"])
        st.caption(L["app_subtitle"])
    with cols[1]:
        st.write("")
        choice = st.selectbox(
            L["ui_lang_caption"], list(LANGS.keys()),
            format_func=lambda k: LANGS[k],
            index=list(LANGS.keys()).index(ui),
            key="ui_lang_select",
            label_visibility="visible",
        )
        if choice != ui:
            st.session_state.ui_lang = choice
            st.rerun()

    st.write("")
    st.info(L["login_intro"])

    with st.form("login"):
        name = st.text_input(L["login_name"],
                              value=st.session_state.get("participant", ""))
        if st.form_submit_button(L["login_continue"], type="primary"):
            nm = name.strip()
            if not nm:
                st.error(L["login_err_empty"])
            else:
                assignment = lookup_participant_assignment(nm)
                if assignment is None:
                    st.error(L["login_err_unknown"])
                elif not assignment.get("condition"):
                    st.error(L["login_err_no_cond"])
                else:
                    st.session_state.participant = nm
                    st.session_state.language    = assignment["language"]
                    st.session_state.condition   = assignment["condition"]
                    st.session_state.ui_lang     = assignment["language"]
                    st.session_state.page        = "consent"
                    st.rerun()


def consent_screen():
    lang = st.session_state.language
    L = UI[lang]
    inject_css(lang)
    st.markdown(f"### {L['consent_title']}")
    st.markdown(L["consent_body"])
    st.write("")
    if st.button(L["consent_btn"], type="primary", width='stretch'):
        st.session_state.page = "practice"
        st.session_state.practice_done = False
        st.session_state.q_start = None
        st.rerun()


def practice_screen():
    lang = st.session_state.language
    L = UI[lang]
    inject_css(lang)
    p = PRACTICE[lang]
    st.markdown(f"#### 🎯 {L['practice_label']}")

    if st.session_state.q_start is None:
        st.session_state.q_start = time.time()

    st.markdown(f'<div class="question-box"><b>{L["question"]}</b>'
                f'{p["question"]}</div>', unsafe_allow_html=True)
    st.write("")
    st.markdown(f"**{L['passage']}**")
    st.markdown(f'<div class="source-card">{p["passage"]}</div>',
                 unsafe_allow_html=True)
    st.write("")
    with st.form("practice"):
        ans = st.text_input(L["answer"])
        if st.form_submit_button(L["submit"], type="primary",
                                   width='stretch'):
            elapsed = round(time.time() - st.session_state.q_start, 2)
            st.success(L["practice_done"] + f"  (TTA = {elapsed}s)")
            st.session_state.practice_done = True
            st.session_state.page = "trial"
            st.session_state.q_start = None
            time.sleep(1.2)
            st.rerun()


def trial_screen():
    participant = st.session_state.participant
    lang = st.session_state.language
    cond = st.session_state.condition
    L = UI[lang]
    inject_css(lang)

    storage = get_storage()
    queue = build_queue(lang, participant, cond)
    done = storage.list_done_qids(participant, lang)
    remaining = [q for q in queue if (q["sample_id"], q["qid"]) not in done]
    total = len(queue); done_n = total - len(remaining)

    hcols = st.columns([3, 2])
    with hcols[0]:
        st.markdown(f"### {L['app_title']}")
        cond_label = CONDITION_LABEL.get(lang, CONDITION_LABEL["en"]).get(cond, cond)
        st.caption(f"👤 {participant}  •  {LANGS[lang]}  •  {cond_label}")
    with hcols[1]:
        st.write("")
        st.markdown(
            f'<div style="text-align:right; padding-top:8px;">'
            f'<span class="progress-pill">{L["progress"]}: '
            f'{done_n} {L["trial_of"]} {total}</span></div>',
            unsafe_allow_html=True)

    st.progress(done_n / total if total else 0)
    st.write("")

    if not remaining:
        st.success(f"✅ {L['done']}")
        st.balloons()
        return

    item = remaining[0]
    item_cond = item["condition"]

    timer_key = f"start_{item['sample_id']}_{item['qid']}"
    if timer_key not in st.session_state:
        st.session_state[timer_key] = time.time()
    started_at = st.session_state[timer_key]

    st.markdown(f'<div class="question-box"><b>{L["question"]}</b>'
                 f'{item["question"]}</div>', unsafe_allow_html=True)
    st.write("")

    if item_cond == "passage":
        st.markdown(
            f'<span class="info-pill">{L["passage"]} · '
            f'{item["word_count"]} {L["words"]}</span>',
            unsafe_allow_html=True)
        st.write("")
        st.markdown(f'<div class="source-card">'
                    f'{item["original_text"].replace(chr(10), "<br>")}</div>',
                    unsafe_allow_html=True)
    elif item_cond == "mindmap":
        st.markdown(
            f'<span class="info-pill">{L["mindmap"]} · '
            f'{MODEL_LABEL[item["model"]]}</span>',
            unsafe_allow_html=True)
        st.write("")
        render_mindmap_image(lang, item["sample_id"], item["model"],
                              height=500, L=L)
    else:  # both
        c1, c2 = st.columns([1, 1.15])
        with c1:
            st.markdown(
                f'<span class="info-pill">{L["passage"]} · '
                f'{item["word_count"]} {L["words"]}</span>',
                unsafe_allow_html=True)
            st.write("")
            st.markdown(f'<div class="source-card" style="max-height:480px;">'
                         f'{item["original_text"].replace(chr(10), "<br>")}</div>',
                         unsafe_allow_html=True)
        with c2:
            st.markdown(
                f'<span class="info-pill">{L["mindmap"]} · '
                f'{MODEL_LABEL[item["model"]]}</span>',
                unsafe_allow_html=True)
            st.write("")
            render_mindmap_image(lang, item["sample_id"], item["model"],
                                  height=500, L=L)

    st.write("")
    with st.form(f"trial_{item['sample_id']}_{item['qid']}"):
        ans = st.text_input(L["answer"],
                             key=f"ans_{item['sample_id']}_{item['qid']}")
        if st.form_submit_button(L["submit"], type="primary",
                                   width='stretch'):
            elapsed = round(time.time() - started_at, 2)
            row = {
                "ts":           datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "participant":  participant, "language": lang,
                "sample_id":    item["sample_id"], "text_id": item["text_id"],
                "qid":          item["qid"],
                "condition":    item_cond, "model": item["model"],
                "question":     item["question"],
                "gold_answer":  item["gold_answer"],
                "given_answer": ans.strip(),
                "tta_seconds":  elapsed,
            }
            try: storage.save(row)
            except Exception as e:
                st.error(f"Could not save: {e}"); st.stop()
            del st.session_state[timer_key]
            st.rerun()


def compute_paired_ttests(df):
    """Run paired t-tests per (language, condition_pair)."""
    try:
        from scipy import stats
    except Exception:
        return None

    rows = []
    pairs = [("passage", "mindmap"), ("passage", "both"), ("mindmap", "both")]
    for lang in sorted(df["language"].unique()):
        sub = df[df["language"] == lang]
        for c1, c2 in pairs:
            d1 = sub[sub["condition"] == c1]
            d2 = sub[sub["condition"] == c2]
            if len(d1) == 0 or len(d2) == 0:
                continue
            # Average over participants in case of multiple per cell
            d1g = d1.groupby(["sample_id", "qid"])["tta_seconds"].mean().reset_index()
            d2g = d2.groupby(["sample_id", "qid"])["tta_seconds"].mean().reset_index()
            m = d1g.merge(d2g, on=["sample_id", "qid"], suffixes=("_1", "_2"))
            if len(m) < 3:
                rows.append({
                    "language": lang, "comparison": f"{c1} vs {c2}",
                    "n_pairs": len(m), "mean_1": float("nan"),
                    "mean_2": float("nan"), "diff": float("nan"),
                    "speedup_pct": float("nan"), "t": float("nan"),
                    "p": float("nan"),
                })
                continue
            x1 = pd.to_numeric(m["tta_seconds_1"], errors="coerce").values
            x2 = pd.to_numeric(m["tta_seconds_2"], errors="coerce").values
            mask = ~(pd.isna(x1) | pd.isna(x2))
            x1, x2 = x1[mask], x2[mask]
            if len(x1) < 3:
                continue
            t, p = stats.ttest_rel(x1, x2)
            speedup = (x1.mean() - x2.mean()) / x1.mean() * 100 if x1.mean() else float("nan")
            rows.append({
                "language":    lang,
                "comparison":  f"{c1} vs {c2}",
                "n_pairs":     len(x1),
                "mean_1":      round(float(x1.mean()), 2),
                "mean_2":      round(float(x2.mean()), 2),
                "diff":        round(float(x1.mean() - x2.mean()), 2),
                "speedup_pct": round(float(speedup), 1),
                "t":           round(float(t), 3),
                "p":           round(float(p), 5),
            })
    return pd.DataFrame(rows)


def admin_screen():
    inject_css()
    st.title("🛠 Admin Dashboard")
    st.caption("Comprehension-Time Study — researcher view")

    expected = "changeme"
    try: expected = st.secrets["admin_password"]
    except Exception: pass
    pwd = st.text_input("Admin password", type="password")
    if pwd != expected:
        if pwd: st.error("Wrong password.")
        st.stop()

    storage = get_storage()
    backend_name = getattr(storage, "backend_name", "?")

    # =====================================================================
    # STORAGE STATUS BANNER — most important info, shown first
    # =====================================================================
    if backend_name == "Google Sheets":
        st.success(
            f"✅ **Storage: Google Sheets** — your data is safely stored in the "
            f"cloud sheet and will NOT be lost on app restart."
        )
    else:
        st.error(
            "⚠️ **DATA LOSS WARNING — Storage: SQLite (local file)**\n\n"
            "Your TTA responses are saved to a **local file** that is **wiped every time**:\n"
            "- The Streamlit Cloud container restarts (≈ every 30 min of inactivity)\n"
            "- The app is rebooted or redeployed\n"
            "- The container is migrated to another machine\n\n"
            "**Action required:** Switch to Google Sheets storage by adding "
            "`gcp_service_account` and `gsheet_url` to your `.streamlit/secrets.toml` "
            "(see expander below). Until then, **download a backup CSV/JSON "
            "after every participant session** (see the 💾 Export & backup tab)."
        )
        with st.expander("📖 How to enable Google Sheets storage"):
            st.markdown(
                "**Step 1.** Create a Google Cloud project and enable the Google "
                "Sheets API + Google Drive API.\n\n"
                "**Step 2.** Create a Service Account → generate a JSON key.\n\n"
                "**Step 3.** Create a Google Sheet and share it with the service "
                "account email (Editor permission).\n\n"
                "**Step 4.** In Streamlit Cloud, go to your app → ⚙ Settings → "
                "Secrets, and add:\n"
            )
            st.code('''admin_password = "..."
gsheet_url = "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"

[gcp_service_account]
type           = "service_account"
project_id     = "..."
private_key_id = "..."
private_key    = """-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"""
client_email   = "...@....iam.gserviceaccount.com"
client_id      = "..."
auth_uri       = "https://accounts.google.com/o/oauth2/auth"
token_uri      = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url        = "..."
''', language="toml")
            st.markdown(
                "**Step 5.** Reboot the app. The banner above will turn green.\n\n"
                "Until then, please use the 💾 Export & backup tab below to download "
                "data regularly."
            )

    st.divider()
    st.caption(f"Storage backend: **{backend_name}**")

    tab_part, tab_data, tab_metrics, tab_stats, tab_export, tab_help = st.tabs([
        "👥 Participants",
        "🗂 Responses",
        "📈 Metrics",
        "📊 Statistical tests",
        "💾 Export & backup",
        "ℹ️ Methodology",
    ])

    with tab_part:
        em_all = _load_participant_map()
        em_dyn = storage.list_participants()
        em_static = {k: v for k, v in em_all.items() if k not in em_dyn}

        if em_static:
            st.caption("📌 **Permanent** — defined in secrets / participants.json")
            df = pd.DataFrame([
                (n, LANGS.get(v["language"], v["language"]),
                 CONDITION_LABEL_EN.get(v["condition"], v["condition"]))
                for n, v in sorted(em_static.items())
            ], columns=["Name", "Language", "Condition"])
            st.dataframe(df, width='stretch',
                          height=min(35 + 35 * len(df), 260))
        if em_dyn:
            st.caption("✏️ **Dynamic** — added through this dashboard")
            df = pd.DataFrame([
                (n, LANGS.get(v["language"], v["language"]),
                 CONDITION_LABEL_EN.get(v["condition"], v["condition"]))
                for n, v in sorted(em_dyn.items())
            ], columns=["Name", "Language", "Condition"])
            st.dataframe(df, width='stretch',
                          height=min(35 + 35 * len(df), 260))
            tgt = st.selectbox("Remove a dynamic participant:",
                                [""] + sorted(em_dyn.keys()),
                                format_func=lambda x: "— select —" if x == "" else x)
            if tgt and st.button(f"🗑 Delete `{tgt}`"):
                storage.delete_participant(tgt); st.rerun()
        if not em_all:
            st.info("No participants yet. Add one below.")

        st.divider()
        st.subheader("➕ Add participant")
        st.caption("Each participant is assigned ONE language and ONE "
                   "condition. They will answer all 50 questions in that "
                   "single condition (matching Jain et al. 2024 protocol).")
        with st.form("add_part", clear_on_submit=True):
            cols = st.columns([3, 2, 2, 1])
            with cols[0]:
                nm = st.text_input("Name", placeholder="e.g. khaled")
            with cols[1]:
                lg = st.selectbox("Language", list(LANGS.keys()),
                                   format_func=lambda k: LANGS[k])
            with cols[2]:
                cd = st.selectbox("Condition", CONDITIONS,
                                   format_func=lambda c: CONDITION_LABEL_EN[c])
            with cols[3]:
                st.write(""); st.write("")
                add = st.form_submit_button("Add", type="primary",
                                              width='stretch')
            if add:
                if not nm.strip(): st.error("Please enter a name.")
                else:
                    storage.save_participant(nm, lg, cd)
                    st.success(f"Added **{nm}** → {LANGS[lg]} / "
                               f"{CONDITION_LABEL_EN[cd]}.")
                    st.rerun()

        st.divider()
        st.subheader("Recommended balanced design (9 participants)")
        st.markdown("""
For a balanced replication of Jain et al. (2024):

| Language | Passage | Mindmap | Both |
|----------|---------|---------|------|
| English  | 1 person | 1 person | 1 person |
| Arabic   | 1 person | 1 person | 1 person |
| Turkish  | 1 person | 1 person | 1 person |

Total: **9 participants** — one per (language × condition) cell.
Each participant answers 50 questions in their assigned condition.
        """)

    df = storage.fetch_all_df()
    if df is not None and len(df) > 0:
        df["tta_seconds"] = pd.to_numeric(df["tta_seconds"], errors="coerce")
        df["sample_id"]    = pd.to_numeric(df["sample_id"], errors="coerce")
        df["lang_name"]    = df["language"].map(LANGS).fillna(df["language"])
        df["model_name"]   = df["model"].map(MODEL_LABEL).fillna(df["model"])

        def _correct(g, gold):
            if pd.isna(g) or pd.isna(gold): return False
            g, gold = str(g).strip().lower(), str(gold).strip().lower()
            if not g or not gold: return False
            return gold in g or g in gold
        df["correct"] = df.apply(lambda r: _correct(r.get("given_answer"),
                                                       r.get("gold_answer")),
                                   axis=1)

    with tab_data:
        if df is None or len(df) == 0:
            st.info("No responses yet.")
        else:
            fcols = st.columns([1, 1, 1, 2])
            with fcols[0]:
                f_lang = st.multiselect("Language", sorted(df["lang_name"].unique()))
            with fcols[1]:
                f_cond = st.multiselect("Condition", sorted(df["condition"].unique()))
            with fcols[2]:
                f_model = st.multiselect("Model", sorted(df["model_name"].unique()))
            with fcols[3]:
                f_part = st.multiselect("Participant", sorted(df["participant"].unique()))

            dv = df.copy()
            if f_lang:  dv = dv[dv["lang_name"].isin(f_lang)]
            if f_cond:  dv = dv[dv["condition"].isin(f_cond)]
            if f_model: dv = dv[dv["model_name"].isin(f_model)]
            if f_part:  dv = dv[dv["participant"].isin(f_part)]

            cols = st.columns(2)
            with cols[0]: st.metric("Rows shown", f"{len(dv):,}")
            with cols[1]: st.metric("Total rows", f"{len(df):,}")

            st.dataframe(dv, width='stretch', height=420)

            st.divider()
            st.subheader("⚠️ Delete responses")
            del_cols = st.columns([2, 2, 1])
            with del_cols[0]:
                del_part = st.selectbox(
                    "Participant",
                    [""] + sorted(df["participant"].unique()),
                    format_func=lambda x: "— select —" if x == "" else x)
            with del_cols[1]:
                del_lang = st.selectbox(
                    "Language (optional)",
                    [""] + ["en", "ar", "tr"],
                    format_func=lambda x: "all" if x == "" else LANGS[x])
            with del_cols[2]:
                st.write(""); st.write("")
                if st.button("🗑 Delete", type="secondary"):
                    if del_part:
                        storage.delete_participant_data(
                            del_part, del_lang or None)
                        st.success(f"Deleted responses for {del_part}"
                                    + (f" / {LANGS.get(del_lang, del_lang)}"
                                        if del_lang else ""))
                        st.rerun()

    with tab_metrics:
        if df is None or len(df) == 0:
            st.info("No data to display yet.")
        else:
            st.subheader("📊 Descriptive: TTA per (Language × Condition)")
            agg = (df.groupby(["lang_name", "condition"])
                     .agg(n=("tta_seconds", "count"),
                          mean_tta=("tta_seconds", "mean"),
                          std_tta=("tta_seconds", "std"),
                          median_tta=("tta_seconds", "median"),
                          accuracy=("correct", "mean"))
                     .round(2).reset_index()
                     .rename(columns={"lang_name": "Language",
                                        "condition": "Condition"}))
            agg["accuracy"] = (agg["accuracy"] * 100).round(1)
            st.dataframe(agg, width='stretch',
                          height=min(35 + 35 * len(agg), 360))

            st.subheader("Speedup table (matches Jain et al. 2024)")
            pivot = (df.groupby(["lang_name", "condition"])["tta_seconds"]
                       .mean().unstack().round(2))
            pivot.columns.name = None
            if "passage" in pivot.columns and "mindmap" in pivot.columns:
                pivot["Δ (passage − mindmap)"] = (
                    pivot["passage"] - pivot["mindmap"]).round(2)
                pivot["Speedup % (vs passage)"] = (
                    (pivot["passage"] - pivot["mindmap"]) / pivot["passage"] * 100
                ).round(1)
            st.dataframe(pivot, width='stretch',
                          height=min(35 + 35 * len(pivot), 320))
            st.caption("Compare to Jain et al. (2024) reported 31.9% speedup for "
                       "English mind maps.")

            st.subheader("Per-model breakdown — mindmap & both")
            mm_only = df[df["condition"].isin(["mindmap", "both"])]
            if len(mm_only):
                magg = (mm_only.groupby(["lang_name", "condition", "model_name"])
                          .agg(n=("tta_seconds", "count"),
                               mean_tta=("tta_seconds", "mean"),
                               accuracy=("correct", "mean"))
                          .round(2).reset_index())
                magg["accuracy"] = (magg["accuracy"] * 100).round(1)
                st.dataframe(magg, width='stretch',
                              height=min(35 + 35 * len(magg), 320))

            st.subheader("Per-participant progress")
            prog = (df.groupby(["participant", "lang_name", "condition"])
                      .size().reset_index(name="responses"))
            prog["target"] = DEFAULT_TARGET_TRIALS
            prog["%_done"] = (prog["responses"]
                                / prog["target"] * 100).round(1)
            prog = prog.sort_values("%_done", ascending=False)
            st.dataframe(prog, width='stretch',
                          height=min(35 + 35 * len(prog), 360))

    with tab_stats:
        if df is None or len(df) == 0:
            st.info("No data yet — statistical tests need responses first.")
        else:
            st.subheader("Paired t-tests (matched by sample_id × qid)")
            st.markdown("""
For each language we run a **paired t-test**: each question is matched
across conditions (different participants saw the same question in
different conditions, but the question itself is the pairing unit).

This replicates the analysis logic of Jain et al. (2024) §8.7.
            """)
            ttests = compute_paired_ttests(df)
            if ttests is None:
                st.warning("scipy is not installed — install with "
                           "`pip install scipy` to enable t-tests.")
            elif len(ttests) == 0:
                st.info("Not enough matched data yet.")
            else:
                disp = ttests.copy()
                disp["language"] = disp["language"].map(LANGS).fillna(disp["language"])
                disp["sig"] = disp["p"].apply(
                    lambda p: "***" if p < 0.001 else (
                              "**" if p < 0.01 else (
                              "*" if p < 0.05 else "ns")))
                st.dataframe(disp, width='stretch',
                              height=min(35 + 35 * len(disp), 400))
                st.caption("Significance: *** p<.001, ** p<.01, * p<.05, "
                           "ns = not significant. n_pairs = number of matched "
                           "questions used in the test.")

            st.divider()
            st.subheader("Reporting template")
            st.code("""
Mean TTA (seconds) per (language × condition) is reported in Table X.
Mind maps reduced TTA by an average of XX.X% relative to text-only
presentation, comparable to the 31.9% reported by Jain et al. (2024).
Paired t-tests, treating each question as a matched observation across
conditions, confirmed the difference was statistically significant in
all three languages:
  English:  t(NN) = X.XX, p < .001
  Arabic:   t(NN) = X.XX, p < .001
  Turkish:  t(NN) = X.XX, p < .001
            """, language="text")

    with tab_export:
        if df is None or len(df) == 0:
            st.info("No data to export yet.")
        else:
            # Reminder banner inside export tab
            if backend_name == "Google Sheets":
                st.info(
                    "Your data is on Google Sheets — it's already safe in the cloud. "
                    "Use these downloads for offline analysis or as additional snapshots."
                )
            else:
                st.warning(
                    "⚠️ **Reminder:** SQLite is the only copy of your data. "
                    "Download a backup **after every participant session** "
                    "and save it to your own machine (Google Drive / Dropbox / local disk)."
                )

            st.subheader("Download data")
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            cols = st.columns(3)
            with cols[0]:
                st.download_button(
                    "⬇️ Full responses CSV",
                    df.to_csv(index=False).encode("utf-8"),
                    f"tta_responses_{stamp}.csv", "text/csv",
                    width='stretch')
            with cols[1]:
                summary = (df.groupby(["lang_name", "condition"])
                              .agg(n=("tta_seconds", "count"),
                                   mean_tta=("tta_seconds", "mean"),
                                   accuracy=("correct", "mean"))
                              .round(3).reset_index())
                st.download_button(
                    "⬇️ Summary CSV",
                    summary.to_csv(index=False).encode("utf-8"),
                    f"tta_summary_{stamp}.csv", "text/csv",
                    width='stretch')
            with cols[2]:
                st.download_button(
                    "⬇️ Full responses JSON",
                    df.to_json(orient="records", force_ascii=False, indent=2)
                       .encode("utf-8"),
                    f"tta_responses_{stamp}.json", "application/json",
                    width='stretch')

            st.divider()
            st.subheader("T-test results CSV")
            ttests = compute_paired_ttests(df)
            if ttests is not None and len(ttests):
                st.download_button(
                    "⬇️ Paired t-tests CSV",
                    ttests.to_csv(index=False).encode("utf-8"),
                    f"tta_ttests_{stamp}.csv", "text/csv",
                    width='stretch')

            st.divider()
            st.subheader("Raw database backup")
            if backend_name == "Google Sheets":
                st.info("Using Google Sheets — data already lives in the "
                        "cloud sheet. Use Google Sheets → File → Make a copy "
                        "for additional backups.")
            else:
                db_bytes = storage.get_db_bytes()
                if db_bytes:
                    st.caption(
                        f"⚠️ Local SQLite snapshot ({len(db_bytes)//1024} KB). "
                        f"This is your **only copy** — Streamlit Cloud loses "
                        f"local files on container restart. Download regularly!"
                    )
                    st.download_button(
                        f"⬇️ Raw SQLite database file ({len(db_bytes)//1024} KB) — "
                        f"emergency restore copy",
                        db_bytes,
                        f"tta_backup_{stamp}.db",
                        "application/x-sqlite3",
                        width='stretch')

    with tab_help:
        st.subheader("Study design (matches Jain et al. 2024)")
        st.markdown("""
**Between-subjects design with within-question matching.**

- Each participant is assigned ONE language and ONE condition by the admin
- Each participant answers all 50 questions in that single condition
- The same question is viewed in all three conditions across different
  participants (no participant sees the same question twice)
- This matches the protocol of Jain et al. (2024) §8.7

**Recommended minimum: 9 participants total**
- 3 per language (English, Arabic, Turkish)
- 1 per condition × language cell
- = 9 × 50 = 450 total responses

**Within mindmap & both conditions:**
- Model (Gemini vs Qwen) is alternated per question (~25/25)
- This allows secondary model comparison without a separate cell
        """)

        st.subheader("How question distribution works")
        st.markdown("""
**Per-language pool sizes:**

- English — 243 questions across 50 records
- Arabic — 208 questions across 50 records
- Turkish — 131 questions across 50 records

**Pool selection (deterministic):**

1. Pool shuffled with a *language-level* seed (`pool|<language>`)
2. First 50 trials kept — these are the **same 50 questions** used by
   all participants in that language
3. Each participant's queue contains those same 50 questions, all
   assigned to their single condition
4. Within mindmap/both, model alternates gem/qwen
5. Trial order shuffled with participant-level seed
        """)

        st.subheader("How metrics are computed")
        st.markdown("""
**TTA.** Wall-clock seconds from question render to Submit click.

**Accuracy.** Substring match between answer and gold (case-insensitive).

**Speedup.**
```
speedup_pct = (mean_TTA_passage − mean_TTA_mindmap) / mean_TTA_passage × 100
```

**Paired t-test:** for each language, each (sample_id, qid) is matched
across two conditions (different participants saw it in different
conditions). The paired t-test operates on these N matched pairs.
        """)


def main():
    st.set_page_config(page_title="Comprehension-Time Study",
                        page_icon="⏱", layout="wide",
                        initial_sidebar_state="collapsed")
    if st.query_params.get("admin") == "1":
        admin_screen(); return
    if "page" not in st.session_state:
        st.session_state.page = "login"
    p = st.session_state.page
    if p == "login":      login_screen()
    elif p == "consent":  consent_screen()
    elif p == "practice": practice_screen()
    else:                 trial_screen()


if __name__ == "__main__":
    main()

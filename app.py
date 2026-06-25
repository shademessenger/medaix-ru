#!/usr/bin/env python3
import difflib
import json
import os
import re
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "data.sqlite3"
HOST = "127.0.0.1"
PORT = int(os.environ.get("AI_PORT", "8555"))

WORD_RE = re.compile(r"[a-zA-Z\u0430-\u044f\u0410-\u042f\u0451\u04010-9]+")
RU_YA = "\u042f"
UNKNOWN = (
    "\u042f \u043f\u043e\u043a\u0430 \u043d\u0435 \u0437\u043d\u0430\u044e "
    "\u0442\u043e\u0447\u043d\u043e\u0433\u043e \u043e\u0442\u0432\u0435\u0442\u0430. "
    "\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043f\u043e\u0434\u0445\u043e"
    "\u0434\u044f\u0449\u0438\u0439 \u0444\u0430\u043a\u0442 \u0438\u043b\u0438 "
    "\u043f\u0430\u0440\u0443 \u0432\u043e\u043f\u0440\u043e\u0441-\u043e\u0442"
    "\u0432\u0435\u0442 \u0432\u043e \u0432\u043a\u043b\u0430\u0434\u043a\u0435 "
    "\u043e\u0431\u0443\u0447\u0435\u043d\u0438\u044f, \u0438 \u044f "
    "\u0437\u0430\u043f\u043e\u043c\u043d\u044e \u044d\u0442\u043e."
)

STOPWORDS = {
    "что", "што", "чо", "че", "чё", "как", "куда", "если", "когда", "почему",
    "можно", "надо", "нужно", "делать", "сделать", "помоги", "помогите",
    "меня", "мне", "мой", "моя", "мои", "тебя", "это", "или", "при", "для",
    "без", "под", "над", "про", "есть", "быть", "был", "была", "уже",
    "очень", "сильно", "немного", "вроде", "типа", "плиз", "пожалуйста",
    "скажите", "подскажите", "вопрос", "ответ", "симптом", "симптомы",
    "what", "when", "where", "why", "how", "the", "and", "with", "without",
    "symptom", "symptoms", "cause", "causes", "treatment", "treatments",
    "medical", "term", "topic", "attack", "disease", "disorder",
}

ALIASES = {
    "грудь": ["груди", "грудной", "грудная", "грудина", "грудине"],
    "сердце": ["кардио", "инфаркт", "сердечный", "сердечная"],
    "боль": ["болит", "болитт", "ноет", "колит", "режет", "тянет", "ломит", "жжет", "жжёт"],
    "одышка": ["дышать", "дыхание", "задыхаюсь", "удушье", "нехватка воздуха", "тяжело дышать"],
    "инсульт": ["перекосило", "речь", "онемела", "онемение", "паралич", "лицо", "рука"],
    "температура": ["темпа", "жар", "лихорадка", "озноб"],
    "диарея": ["понос", "жидкий стул"],
    "рвота": ["тошнит", "тошнота", "вырвало", "рвет", "рвёт"],
    "аллергия": ["сыпь", "отек", "отёк", "квинке", "анафилаксия", "чешется"],
    "давление": ["ад", "гипертония", "гипертензия", "тонометр"],
    "сахар": ["глюкоза", "диабет", "инсулин"],
    "рана": ["порез", "царапина", "кровь", "кровотечение"],
    "ожог": ["обжег", "обжёг", "обожглась", "обжегся", "кипяток"],
    "лекарство": ["таблетка", "таблетки", "препарат", "медикамент", "доза"],
    "антибиотик": ["антибиотики", "амоксиклав", "амоксициллин", "азитромицин"],
    "парацетамол": ["ацетаминофен", "панадол", "тайленол"],
    "ибупрофен": ["нурофен", "нпвп"],
    "беременность": ["беременна", "беременная", "плод", "шевеления"],
    "травма": ["ударился", "упал", "ушиб", "перелом", "вывих"],
    "голова": ["головная", "мигрень", "затылок", "висок"],
    "живот": ["брюхо", "желудок", "кишечник", "аппендицит"],
    "моча": ["писать", "мочеиспускание", "цистит", "почки"],
}

INTENT_RULES = [
    ({"боль", "грудь"}, ["боль в груди", "боль, давление"]),
    ({"сердце", "грудь"}, ["боль в груди", "боль, давление"]),
    ({"инсульт"}, ["признаки инсульта"]),
    ({"одышка"}, ["сильной одышке", "что делать при сильной одышке"]),
    ({"аллергия"}, ["анафилаксия аллергия", "опасные признаки аллергии"]),
    ({"рана", "кровотечение"}, ["сильное кровотечение"]),
    ({"ожог"}, ["ожог первая помощь"]),
    ({"отравление"}, ["отравление что делать"]),
    ({"температура"}, ["температура у взрослого"]),
    ({"диарея", "рвота"}, ["рвота и понос"]),
    ({"живот", "боль"}, ["боль в животе"]),
    ({"голова", "боль"}, ["головная боль"]),
    ({"судороги"}, ["судороги первая помощь"]),
    ({"травма"}, ["травма головы", "перелом вывих травма"]),
    ({"антибиотик"}, ["антибиотики как принимать"]),
    ({"парацетамол"}, ["парацетамол ацетаминофен"]),
    ({"ибупрофен"}, ["ибупрофен нпвп"]),
    ({"давление"}, ["давление высокое"]),
    ({"сахар"}, ["низкий сахар", "высокий сахар"]),
    ({"беременность"}, ["беременность тревожные"]),
    ({"моча"}, ["боль при мочеиспускании"]),
    ({"кашель"}, ["кашель простуда"]),
    ({"горло"}, ["боль в горле"]),
]

CANONICAL = {}
for key, values in ALIASES.items():
    CANONICAL[key] = key
    for value in values:
        for part in value.split():
            CANONICAL[part] = key


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            create table if not exists knowledge (
                id integer primary key autoincrement,
                kind text not null,
                question text not null default '',
                answer text not null,
                created_at integer not null
            )
            """
        )
        conn.execute("create index if not exists idx_knowledge_kind on knowledge(kind)")


def normalize_text(text):
    text = (text or "").lower().replace("\u0451", "\u0435")
    text = text.replace("ё", "е")
    text = re.sub(r"([a-zA-Z\u0430-\u044f\u0410-\u042f])\1{2,}", r"\1\1", text)
    return text


def raw_tokens(text):
    return [w for w in WORD_RE.findall(normalize_text(text)) if len(w) > 1]


def stem_ru(word):
    for suffix in (
        "иями", "ями", "ами", "ого", "ему", "ыми", "ими", "ая", "яя", "ое",
        "ее", "ые", "ие", "ый", "ий", "ой", "ам", "ям", "ах", "ях", "ов",
        "ев", "ом", "ем", "ью", "ия", "ие", "ии", "ей", "ую", "юю", "а",
        "я", "ы", "и", "у", "ю", "е", "о",
    ):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def token_set(text):
    result = set()
    words = raw_tokens(text)
    for word in words:
        if word in STOPWORDS or len(word) <= 2:
            continue
        canon = CANONICAL.get(word, word)
        result.add(canon)
        result.add(stem_ru(canon))
        if word != canon:
            result.add(stem_ru(word))
    joined = " ".join(words)
    for canon, aliases in ALIASES.items():
        if canon in joined or any(alias in joined for alias in aliases):
            result.add(canon)
    return result


def fuzzy_overlap(query_tokens, hay_tokens):
    score = 0
    for qt in query_tokens:
        if len(qt) < 5:
            continue
        best = difflib.get_close_matches(qt, hay_tokens, n=1, cutoff=0.78)
        if best:
            score += 1
    return score


def score(query, row):
    q_tokens = token_set(query)
    if row["kind"] == "term":
        hay_text = row["question"] or ""
    else:
        hay_text = (row["question"] or "") + " " + row["answer"]
    h_tokens = token_set(hay_text)
    if not q_tokens or not h_tokens:
        return 0
    overlap = len(q_tokens & h_tokens)
    fuzzy = fuzzy_overlap(q_tokens, list(h_tokens))
    q_norm = normalize_text(query).strip()
    question_norm = normalize_text(row["question"] or "")
    exact_bonus = 25 if question_norm and q_norm in question_norm else 0
    if row["kind"] == "term":
        parts = [p.strip() for p in question_norm.split(";") if p.strip()]
        term_names = [p.replace("medical term ", "", 1).strip() for p in parts[:2]]
        if any(name and name in q_norm for name in term_names):
            exact_bonus += 30
    qa_bonus = 8 if row["kind"] == "qa" else 0
    term_penalty = -6 if row["kind"] == "term" else 0
    return overlap * 14 + fuzzy * 5 + exact_bonus + qa_bonus + term_penalty


def intent_match(message, rows):
    q_tokens = token_set(message)
    if not q_tokens:
        return None
    for required, hints in INTENT_RULES:
        if required <= q_tokens:
            for hint in hints:
                hint_norm = normalize_text(hint)
                for row in rows:
                    question = normalize_text(row["question"] or "")
                    if row["kind"] == "qa" and hint_norm in question:
                        return row
                for row in rows:
                    answer = normalize_text(row["answer"] or "")
                    if row["kind"] == "qa" and hint_norm in answer:
                        return row
    return None


def wants_definition(message):
    words = raw_tokens(message)
    informative = [w for w in words if w not in STOPWORDS]
    text = normalize_text(message)
    question_markers = {
        "можно", "нужно", "надо", "как", "когда", "сколько", "доза", "дозировка",
        "пить", "принимать", "опасно", "вред", "передозировка", "побочка",
        "побочные", "беременность", "ребенку", "детям", "печень", "почки",
        "совместимость", "алкоголь", "что делать", "болит", "боль",
    }
    definition_markers = {"что такое", "определение", "это что", "что значит"}
    if any(marker in text for marker in definition_markers):
        return True
    if any(marker in text for marker in question_markers):
        return False
    return 1 <= len(informative) <= 3


def term_definition_match(message, rows):
    if not wants_definition(message):
        return None
    q_tokens = token_set(message)
    if not q_tokens:
        return None
    candidates = []
    for row in rows:
        if row["kind"] != "term":
            continue
        value = score(message, row)
        if value >= 12:
            candidates.append((value, row))
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x[0], reverse=True)[0][1]


def best_answer(message):
    with db() as conn:
        rows = conn.execute(
            "select id, kind, question, answer, created_at from knowledge order by id desc"
        ).fetchall()
    term_row = term_definition_match(message, rows)
    if term_row:
        other_ranked = sorted(
            ((score(message, row), row) for row in rows if row["id"] != term_row["id"]),
            key=lambda x: x[0],
            reverse=True,
        )
        matches = [term_row] + [row for value, row in other_ranked if value >= 10][:4]
        return {
            "answer": term_row["answer"],
            "matches": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "question": row["question"],
                    "answer": row["answer"],
                }
                for row in matches
            ],
        }
    forced = intent_match(message, rows)
    if forced:
        other_ranked = sorted(
            ((score(message, row), row) for row in rows if row["id"] != forced["id"]),
            key=lambda x: x[0],
            reverse=True,
        )
        matches = [forced] + [row for value, row in other_ranked if value >= 10][:4]
        return {
            "answer": forced["answer"],
            "matches": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "question": row["question"],
                    "answer": row["answer"],
                }
                for row in matches
            ],
        }
    ranked = sorted(((score(message, row), row) for row in rows), key=lambda x: x[0], reverse=True)
    matches = [row for value, row in ranked if value >= 10][:5]
    if not matches:
        return {"answer": UNKNOWN, "matches": []}

    top = matches[0]
    answer = top["answer"]
    if top["kind"] == "fact":
        answer = "\u0412\u043e\u0442 \u0447\u0442\u043e \u044f \u043d\u0430\u0448\u0435\u043b \u0432 \u043e\u0431\u0443\u0447\u0430\u044e\u0449\u0435\u0439 \u0431\u0430\u0437\u0435: " + answer
    elif top["kind"] == "term":
        answer = (
            answer
            + "\n\n\u041c\u043e\u0433\u0443 \u043d\u0430\u0439\u0442\u0438 "
            "\u043f\u043e\u0445\u043e\u0436\u0438\u0435 \u0437\u0430\u043f\u0438\u0441\u0438, "
            "\u0435\u0441\u043b\u0438 \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c "
            "\u0441\u0438\u043c\u043f\u0442\u043e\u043c\u044b \u0438\u043b\u0438 "
            "\u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442."
        )
    return {
        "answer": answer,
        "matches": [
            {
                "id": row["id"],
                "kind": row["kind"],
                "question": row["question"],
                "answer": row["answer"],
            }
            for row in matches
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "TrainableAI/1.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, include_body=True):
        body = (APP_DIR / "index.html").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/ai", "/ai/"):
            return self.send_html()
        if path == "/ai/api/knowledge":
            query = (parse_qs(parsed.query).get("q", [""])[0] or "").strip()
            with db() as conn:
                if query:
                    like = "%" + query + "%"
                    rows = conn.execute(
                        """
                        select id, kind, question, answer, created_at
                        from knowledge
                        where question like ? or answer like ? or cast(id as text) = ?
                        order by id desc
                        """,
                        (like, like, query.lstrip("#")),
                    ).fetchall()
                    rows = [row for _, row in sorted(((score(query, row), row) for row in rows), key=lambda x: x[0], reverse=True)][:100]
                else:
                    rows = conn.execute(
                        "select id, kind, question, answer, created_at from knowledge order by id desc limit 100"
                    ).fetchall()
            return self.send_json({"items": [dict(row) for row in rows]})
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path in ("/ai", "/ai/"):
            return self.send_html(include_body=False)
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
        except Exception:
            return self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)

        if path == "/ai/api/chat":
            message = (payload.get("message") or "").strip()
            if not message:
                return self.send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
            return self.send_json(best_answer(message))

        if path == "/ai/api/train":
            kind = payload.get("kind") if payload.get("kind") in ("fact", "qa", "term") else "fact"
            question = (payload.get("question") or "").strip()
            answer = (payload.get("answer") or "").strip()
            if kind == "qa" and not question:
                return self.send_json({"error": "question is required for qa"}, HTTPStatus.BAD_REQUEST)
            if not answer:
                return self.send_json({"error": "answer/fact is required"}, HTTPStatus.BAD_REQUEST)
            with db() as conn:
                cur = conn.execute(
                    "insert into knowledge(kind, question, answer, created_at) values(?,?,?,?)",
                    (kind, question, answer, int(time.time())),
                )
            return self.send_json({"ok": True, "id": cur.lastrowid})

        if path == "/ai/api/delete":
            item_id = int(payload.get("id") or 0)
            with db() as conn:
                conn.execute("delete from knowledge where id = ?", (item_id,))
            return self.send_json({"ok": True})

        if path == "/ai/api/update":
            item_id = int(payload.get("id") or 0)
            kind = payload.get("kind") if payload.get("kind") in ("fact", "qa", "term") else "fact"
            question = (payload.get("question") or "").strip()
            answer = (payload.get("answer") or "").strip()
            if not item_id:
                return self.send_json({"error": "id is required"}, HTTPStatus.BAD_REQUEST)
            if kind == "qa" and not question:
                return self.send_json({"error": "question is required for qa"}, HTTPStatus.BAD_REQUEST)
            if not answer:
                return self.send_json({"error": "answer/fact is required"}, HTTPStatus.BAD_REQUEST)
            with db() as conn:
                cur = conn.execute(
                    "update knowledge set kind = ?, question = ?, answer = ? where id = ?",
                    (kind, question, answer, item_id),
                )
            if cur.rowcount == 0:
                return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self.send_json({"ok": True})

        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Trainable AI listening on http://{HOST}:{PORT}/ai")
    httpd.serve_forever()

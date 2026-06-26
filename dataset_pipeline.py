#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import ssl
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set
from urllib.parse import quote_plus
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "kb_data"
STATE_PATH = DATA_DIR / "state.json"
APP_DB_PATH = ROOT / "data.sqlite3"
INCOMING_DB_PATH = ROOT / "incoming_data.sqlite3"

DEFAULT_BATCH_SIZE = 250
MIN_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500
HTTP_TIMEOUT = 45

NAME_RE = re.compile(r"\s+")
SAFE_ID_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class CategorySpec:
    key: str
    file_name: str
    target_count: int
    required_metadata: tuple
    description: str


CATEGORY_SPECS: Dict[str, CategorySpec] = {
    "anatomy": CategorySpec(
        key="anatomy",
        file_name="anatomy.jsonl",
        target_count=1000,
        required_metadata=("type", "system"),
        description="Human anatomy: organs, bones, vessels, muscles, nerves, body parts, systems.",
    ),
    "substances": CategorySpec(
        key="substances",
        file_name="substances.jsonl",
        target_count=10000,
        required_metadata=("mechanism", "indications", "contraindications", "atc_class"),
        description="Active pharmacological substances.",
    ),
    "drugs": CategorySpec(
        key="drugs",
        file_name="drugs.jsonl",
        target_count=100000,
        required_metadata=("active_ingredient", "manufacturer"),
        description="Trade drug names mapped to active ingredients.",
    ),
    "companies": CategorySpec(
        key="companies",
        file_name="companies.jsonl",
        target_count=5000,
        required_metadata=("country", "therapeutic_areas"),
        description="Pharmaceutical companies.",
    ),
    "diseases": CategorySpec(
        key="diseases",
        file_name="diseases.jsonl",
        target_count=10000,
        required_metadata=("icd10", "symptoms", "treatment_category", "affected_system"),
        description="Diseases and disorders.",
    ),
    "specialties": CategorySpec(
        key="specialties",
        file_name="specialties.jsonl",
        target_count=0,
        required_metadata=("scope",),
        description="Recognized medical specialties.",
    ),
    "instruments": CategorySpec(
        key="instruments",
        file_name="instruments.jsonl",
        target_count=500,
        required_metadata=("purpose", "usage_area"),
        description="Medical instruments and devices.",
    ),
}

SPECIALTIES_SEED = [
    ("Allergy and Immunology", "Diagnosis and treatment of allergies, asthma, immunodeficiency, and immune-mediated disease."),
    ("Anesthesiology", "Perioperative medicine, pain control, airway management, and anesthesia for procedures."),
    ("Cardiology", "Heart and vascular disease evaluation, imaging, rhythm disorders, and medical cardiovascular care."),
    ("Cardiothoracic Surgery", "Operative treatment of heart, lung, mediastinal, and thoracic diseases."),
    ("Clinical Genetics", "Inherited disorders, genomic counseling, risk assessment, and diagnostic interpretation."),
    ("Critical Care Medicine", "Management of life-threatening illness, organ support, and intensive care."),
    ("Dermatology", "Diseases of skin, hair, nails, mucosa, and cutaneous procedures."),
    ("Emergency Medicine", "Acute illness and trauma assessment, stabilization, and urgent treatment."),
    ("Endocrinology", "Hormonal and metabolic disorders including diabetes, thyroid, pituitary, and adrenal disease."),
    ("Family Medicine", "Comprehensive primary care across lifespan, prevention, chronic disease, and common acute illness."),
    ("Forensic Medicine", "Medical examination related to injury, death investigation, and legal evidence."),
    ("Gastroenterology", "Diseases of esophagus, stomach, intestines, liver, pancreas, and biliary tract."),
    ("General Practice", "Broad front-line outpatient care, preventive care, and coordination of referrals."),
    ("General Surgery", "Operative and perioperative care for abdominal, soft tissue, endocrine, and emergency surgical disease."),
    ("Geriatrics", "Care of older adults with frailty, multimorbidity, cognition, and functional decline."),
    ("Gynecologic Oncology", "Cancer prevention, surgery, and systemic coordination for gynecologic malignancies."),
    ("Gynecology", "Female reproductive health, menstrual disorders, pelvic disease, and preventive screening."),
    ("Hematology", "Blood disorders including anemia, clotting disease, marrow disorders, and cellular diagnostics."),
    ("Hematology and Oncology", "Combined care of blood disorders and cancer."),
    ("Hepatology", "Liver disease including hepatitis, cirrhosis, portal hypertension, and liver failure."),
    ("Hospital Medicine", "Inpatient adult medical management, transitions of care, and acute ward-based treatment."),
    ("Infectious Diseases", "Bacterial, viral, fungal, and parasitic disease diagnosis, treatment, and prevention."),
    ("Internal Medicine", "Adult medicine focused on diagnosis, chronic disease management, and complex multisystem illness."),
    ("Interventional Cardiology", "Catheter-based diagnosis and treatment of coronary and structural heart disease."),
    ("Interventional Radiology", "Image-guided minimally invasive diagnosis and procedures."),
    ("Medical Microbiology", "Laboratory diagnosis of infectious organisms and antimicrobial testing."),
    ("Medical Oncology", "Systemic cancer therapy including chemotherapy, targeted therapy, and immunotherapy."),
    ("Neonatology", "Care of newborns, especially premature and critically ill infants."),
    ("Nephrology", "Kidney disease, electrolyte disorders, hypertension, and dialysis care."),
    ("Neurology", "Disorders of brain, spinal cord, peripheral nerves, and neuromuscular system."),
    ("Neurosurgery", "Operative treatment of brain, spine, peripheral nerve, and cerebrovascular disorders."),
    ("Nuclear Medicine", "Diagnostic and therapeutic use of radiopharmaceuticals."),
    ("Obstetrics", "Pregnancy, childbirth, maternal-fetal monitoring, and peripartum care."),
    ("Obstetrics and Gynecology", "Combined specialty covering pregnancy and female reproductive health."),
    ("Occupational Medicine", "Health effects of workplace exposures, fitness for duty, and work-related disease."),
    ("Ophthalmology", "Eye diseases, vision disorders, ocular surgery, and visual system diagnostics."),
    ("Oral and Maxillofacial Surgery", "Surgical treatment of facial, jaw, oral, and dental-related conditions."),
    ("Orthopedic Surgery", "Bones, joints, ligaments, tendons, fractures, and musculoskeletal surgery."),
    ("Otolaryngology", "Ear, nose, throat, head, and neck disorders and surgery."),
    ("Pain Medicine", "Evaluation and management of acute, chronic, and procedural pain."),
    ("Palliative Medicine", "Symptom control, serious illness support, and goal-concordant care."),
    ("Pathology", "Tissue, cytology, autopsy, and laboratory-based disease diagnosis."),
    ("Pediatric Cardiology", "Congenital and acquired heart disease in children."),
    ("Pediatric Endocrinology", "Hormonal, growth, and metabolic disorders in children."),
    ("Pediatric Gastroenterology", "Digestive and liver disease in infants, children, and adolescents."),
    ("Pediatric Hematology-Oncology", "Blood disorders and cancer in children."),
    ("Pediatric Infectious Diseases", "Infectious disease diagnosis and treatment in children."),
    ("Pediatric Nephrology", "Kidney and urinary disorders in children."),
    ("Pediatric Neurology", "Neurologic and neurodevelopmental disorders in children."),
    ("Pediatric Pulmonology", "Respiratory disease and chronic lung conditions in children."),
    ("Pediatric Surgery", "Operative treatment of congenital and acquired surgical disease in children."),
    ("Pediatrics", "Medical care of infants, children, and adolescents."),
    ("Physical Medicine and Rehabilitation", "Function restoration, disability care, rehabilitation, and musculoskeletal recovery."),
    ("Plastic Surgery", "Reconstructive and aesthetic surgery of skin, soft tissue, hand, and craniofacial structures."),
    ("Preventive Medicine", "Population health, screening, occupational and public-health-oriented prevention."),
    ("Psychiatry", "Mental disorders, psychopharmacology, psychotherapy, and behavioral health."),
    ("Pulmonology", "Lung and airway disease including asthma, COPD, interstitial and sleep-related disorders."),
    ("Radiation Oncology", "Cancer treatment using ionizing radiation."),
    ("Radiology", "Diagnostic imaging including X-ray, CT, MRI, ultrasound, and image interpretation."),
    ("Reproductive Endocrinology and Infertility", "Fertility disorders, assisted reproduction, and reproductive hormone disease."),
    ("Rheumatology", "Autoimmune, inflammatory, and connective tissue disease."),
    ("Sleep Medicine", "Sleep disorders such as apnea, insomnia, hypersomnia, and circadian disease."),
    ("Sports Medicine", "Exercise-related injury, performance-related musculoskeletal care, and return-to-play guidance."),
    ("Transfusion Medicine", "Blood banking, component therapy, compatibility testing, and apheresis."),
    ("Trauma Surgery", "Emergency operative care for severe injury and surgical critical care."),
    ("Urology", "Urinary tract disease and male reproductive system disorders."),
    ("Vascular Surgery", "Operative and endovascular treatment of arterial, venous, and lymphatic disease."),
]


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "categories": {
                        key: {
                            "file": spec.file_name,
                            "target_count": spec.target_count,
                            "imported_count": 0,
                            "last_batch": 0,
                        }
                        for key, spec in CATEGORY_SPECS.items()
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    for spec in CATEGORY_SPECS.values():
        path = DATA_DIR / spec.file_name
        if not path.exists():
            path.write_text("", encoding="utf-8")


def load_state() -> dict:
    ensure_storage()
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_name(value: str) -> str:
    value = (value or "").strip().lower()
    value = NAME_RE.sub(" ", value)
    return value


def slugify(value: str) -> str:
    value = normalize_name(value)
    value = SAFE_ID_RE.sub("-", value).strip("-")
    return value or "item"


def file_path_for(category: str) -> Path:
    return DATA_DIR / CATEGORY_SPECS[category].file_name


def reset_category_storage(category: str) -> dict:
    ensure_storage()
    ensure_app_db()
    file_path_for(category).write_text("", encoding="utf-8")
    state = load_state()
    state["categories"][category]["imported_count"] = 0
    state["categories"][category]["last_batch"] = 0
    save_state(state)
    with app_db() as conn:
        deleted = conn.execute(
            "delete from knowledge where external_id like ?",
            (f"{category}:%",),
        ).rowcount
    return {"category": category, "deleted_records": deleted}


def app_db() -> sqlite3.Connection:
    conn = sqlite3.connect(APP_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_app_db() -> None:
    with app_db() as conn:
        conn.execute(
            """
            create table if not exists knowledge (
                id integer primary key autoincrement,
                external_id text,
                kind text not null,
                question text not null default '',
                answer text not null,
                created_at integer not null,
                updated_at integer not null default 0
            )
            """
        )
        conn.execute("create index if not exists idx_knowledge_kind on knowledge(kind)")
        columns = {
            row["name"]
            for row in conn.execute("pragma table_info(knowledge)")
        }
        if "external_id" not in columns:
            conn.execute("alter table knowledge add column external_id text")
        if "updated_at" not in columns:
            conn.execute("alter table knowledge add column updated_at integer not null default 0")
        conn.execute("create unique index if not exists idx_knowledge_external_id on knowledge(external_id)")


def iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{line_no}: invalid json: {exc}") from exc


def existing_names(category: Optional[str] = None) -> Set[str]:
    names: Set[str] = set()
    categories = [category] if category else list(CATEGORY_SPECS)
    for key in categories:
        for item in iter_jsonl(file_path_for(key)):
            names.add(normalize_name(item["name"]))
            for alias in item.get("aliases", []):
                names.add(normalize_name(alias))
    return names


def stable_id(category: str, name: str) -> str:
    slug = slugify(name)
    digest = hashlib.sha1(f"{category}:{normalize_name(name)}".encode("utf-8")).hexdigest()[:10]
    return f"{category}:{slug}:{digest}"


def clean_aliases(aliases: Iterable[str], name: str) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    base = normalize_name(name)
    for alias in aliases or []:
        alias = NAME_RE.sub(" ", str(alias or "").strip())
        if not alias:
            continue
        normalized = normalize_name(alias)
        if not normalized or normalized == base or normalized in seen:
            continue
        seen.add(normalized)
        result.append(alias)
    return result


def validate_entry(category: str, entry: dict) -> dict:
    if category not in CATEGORY_SPECS:
        raise ValueError(f"unsupported category: {category}")
    spec = CATEGORY_SPECS[category]
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")

    name = str(entry.get("name") or "").strip()
    if not name:
        raise ValueError("missing required field: name")

    description = str(entry.get("description") or "").strip()
    if not description:
        raise ValueError("missing required field: description")

    metadata = entry.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("missing required field: metadata")

    for field_name in spec.required_metadata:
        value = metadata.get(field_name)
        if value is None:
            raise ValueError(f"missing metadata field: {field_name}")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"empty metadata field: {field_name}")
        if isinstance(value, list) and not value:
            raise ValueError(f"empty metadata field: {field_name}")

    aliases = clean_aliases(entry.get("aliases", []), name)
    entry_id = str(entry.get("id") or stable_id(category, name))

    return {
        "id": entry_id,
        "category": category,
        "name": name,
        "aliases": aliases,
        "description": description,
        "metadata": metadata,
    }


def record_question(entry: dict) -> str:
    parts = [entry["name"], *entry.get("aliases", [])]
    cleaned: List[str] = []
    seen: Set[str] = set()
    for part in parts:
        part = str(part or "").strip()
        if not part:
            continue
        key = normalize_name(part)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(part)
    return "; ".join(cleaned)


def record_answer(entry: dict) -> str:
    metadata = entry.get("metadata", {})
    lines = [entry["description"].strip()]
    for key, value in metadata.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()
        if not text:
            continue
        label = key.replace("_", " ")
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def sync_entries_to_records(entries: Iterable[dict]) -> int:
    ensure_app_db()
    synced = 0
    with app_db() as conn:
        for entry in entries:
            try:
                entry = validate_entry(entry["category"], entry)
            except Exception:
                continue
            question = record_question(entry)
            answer = record_answer(entry)
            now = int(time.time())
            row = conn.execute(
                "select id, question, answer from knowledge where external_id = ?",
                (entry["id"],),
            ).fetchone()
            if row:
                if row["question"] != question or row["answer"] != answer:
                    conn.execute(
                        "update knowledge set question = ?, answer = ?, updated_at = ? where id = ?",
                        (question, answer, now, row["id"]),
                    )
                synced += 1
                continue

            alias_like = "%" + entry["name"] + "%"
            same_question = conn.execute(
                "select id from knowledge where kind = 'term' and (question = ? or question like ?)",
                (question, alias_like),
            ).fetchone()
            if same_question:
                conn.execute(
                    "update knowledge set external_id = coalesce(external_id, ?), question = ?, answer = ?, updated_at = ? where id = ?",
                    (entry["id"], question, answer, now, same_question["id"]),
                )
                synced += 1
                continue

            conn.execute(
                "insert into knowledge(external_id, kind, question, answer, created_at, updated_at) values(?,?,?,?,?,?)",
                (entry["id"], "term", question, answer, now, now),
            )
            synced += 1
    return synced


def sync_snapshot_db_to_app_db(snapshot_path: Path) -> dict:
    ensure_app_db()
    if not snapshot_path.exists():
        return {"imported": 0, "updated": 0, "skipped": 0, "status": "missing"}

    imported = 0
    updated = 0
    skipped = 0
    with sqlite3.connect(snapshot_path) as source:
        source.row_factory = sqlite3.Row
        rows = source.execute(
            "select id, kind, question, answer, created_at from knowledge order by id asc"
        ).fetchall()
    with app_db() as conn:
        for row in rows:
            kind = str(row["kind"] or "").strip()
            question = str(row["question"] or "").strip()
            answer = str(row["answer"] or "").strip()
            created_at = int(row["created_at"] or 0)
            if kind not in {"fact", "qa", "term"} or not answer:
                skipped += 1
                continue
            current = conn.execute(
                "select id, question, answer, kind from knowledge where id = ?",
                (row["id"],),
            ).fetchone()
            now = int(time.time())
            if current:
                if (
                    current["question"] != question
                    or current["answer"] != answer
                    or current["kind"] != kind
                ):
                    conn.execute(
                        "update knowledge set kind = ?, question = ?, answer = ?, updated_at = ? where id = ?",
                        (kind, question, answer, now, row["id"]),
                    )
                    updated += 1
                else:
                    skipped += 1
                continue

            duplicate = conn.execute(
                "select id from knowledge where kind = ? and question = ? and answer = ?",
                (kind, question, answer),
            ).fetchone()
            if duplicate:
                skipped += 1
                continue

            conn.execute(
                "insert into knowledge(id, external_id, kind, question, answer, created_at, updated_at) values(?,?,?,?,?,?,?)",
                (row["id"], None, kind, question, answer, created_at or now, now),
            )
            imported += 1
    return {"imported": imported, "updated": updated, "skipped": skipped, "status": "success"}


def sync_jsonl_to_app_db() -> dict:
    ensure_storage()
    ensure_app_db()
    imported = 0
    updated = 0
    skipped = 0
    with app_db() as conn:
        for category in CATEGORY_SPECS:
            for raw_entry in iter_jsonl(file_path_for(category)):
                try:
                    entry = validate_entry(category, raw_entry)
                except Exception:
                    skipped += 1
                    continue
                question = record_question(entry)
                answer = record_answer(entry)
                now = int(time.time())
                row = conn.execute(
                    "select id, question, answer from knowledge where external_id = ?",
                    (entry["id"],),
                ).fetchone()
                if row:
                    if row["question"] != question or row["answer"] != answer:
                        conn.execute(
                            "update knowledge set question = ?, answer = ?, updated_at = ? where id = ?",
                            (question, answer, now, row["id"]),
                        )
                        updated += 1
                    else:
                        skipped += 1
                    continue
                duplicate = conn.execute(
                    "select id from knowledge where kind = 'term' and question = ?",
                    (question,),
                ).fetchone()
                if duplicate:
                    conn.execute(
                        "update knowledge set external_id = ?, answer = ?, updated_at = ? where id = ?",
                        (entry["id"], answer, now, duplicate["id"]),
                    )
                    updated += 1
                    continue
                conn.execute(
                    "insert into knowledge(external_id, kind, question, answer, created_at, updated_at) values(?,?,?,?,?,?)",
                    (entry["id"], "term", question, answer, now, now),
                )
                imported += 1
    return {"imported": imported, "updated": updated, "skipped": skipped, "status": "success"}


def run_startup_sync(remove_incoming: bool = False) -> dict:
    ensure_storage()
    ensure_app_db()
    snapshot_result = sync_snapshot_db_to_app_db(INCOMING_DB_PATH)
    jsonl_result = sync_jsonl_to_app_db()
    if remove_incoming and INCOMING_DB_PATH.exists():
        try:
            INCOMING_DB_PATH.unlink()
        except OSError:
            pass
    return {
        "snapshot": snapshot_result,
        "jsonl": jsonl_result,
        "status": "success" if snapshot_result["status"] != "failed" and jsonl_result["status"] == "success" else "failed",
    }


def append_entries(category: str, entries: Iterable[dict]) -> int:
    normalized_existing = existing_names(category)
    accepted: List[dict] = []
    for raw in entries:
        entry = validate_entry(category, raw)
        name_key = normalize_name(entry["name"])
        if name_key in normalized_existing:
            continue
        alias_conflict = False
        for alias in entry["aliases"]:
            if normalize_name(alias) in normalized_existing:
                alias_conflict = True
                break
        if alias_conflict:
            continue
        normalized_existing.add(name_key)
        normalized_existing.update(normalize_name(alias) for alias in entry["aliases"])
        accepted.append(entry)

    if not accepted:
        return 0

    path = file_path_for(category)
    with path.open("a", encoding="utf-8") as handle:
        for entry in accepted:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    sync_entries_to_records(accepted)

    state = load_state()
    state["categories"][category]["imported_count"] += len(accepted)
    state["categories"][category]["last_batch"] += 1
    save_state(state)
    return len(accepted)


def import_seed_array(path: Path, category: str, batch_size: int) -> int:
    raw_items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list):
        raise ValueError("seed file must contain a JSON array")

    converted: List[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("question") or item.get("name") or "").strip()
        answer = str(item.get("answer") or item.get("description") or "").strip()
        if not name or not answer:
            continue
        aliases = [part.strip() for part in name.split(";") if part.strip()]
        primary_name = aliases[0] if aliases else name
        metadata = {"source_file": path.name, "source_kind": item.get("kind", "seed")}

        if category == "diseases":
            metadata.update(
                {
                    "icd10": item.get("icd10", "unknown"),
                    "symptoms": item.get("symptoms", "unknown"),
                    "treatment_category": item.get("treatment_category", "reference"),
                    "affected_system": item.get("affected_system", "unknown"),
                }
            )
        elif category == "anatomy":
            metadata.update(
                {
                    "type": item.get("type", "reference"),
                    "system": item.get("system", "unknown"),
                }
            )
        elif category == "substances":
            metadata.update(
                {
                    "mechanism": item.get("mechanism", "unknown"),
                    "indications": item.get("indications", "unknown"),
                    "contraindications": item.get("contraindications", "unknown"),
                    "atc_class": item.get("atc_class", "unknown"),
                }
            )
        elif category == "drugs":
            metadata.update(
                {
                    "active_ingredient": item.get("active_ingredient", "unknown"),
                    "manufacturer": item.get("manufacturer", "unknown"),
                }
            )
        elif category == "companies":
            metadata.update(
                {
                    "country": item.get("country", "unknown"),
                    "therapeutic_areas": item.get("therapeutic_areas", ["unknown"]),
                }
            )
        elif category == "specialties":
            metadata.update({"scope": item.get("scope", "unknown")})
        elif category == "instruments":
            metadata.update(
                {
                    "purpose": item.get("purpose", "unknown"),
                    "usage_area": item.get("usage_area", "unknown"),
                }
            )

        converted.append(
            {
                "category": category,
                "name": primary_name,
                "aliases": aliases[1:],
                "description": answer,
                "metadata": metadata,
            }
        )

    accepted_total = 0
    for index in range(0, len(converted), batch_size):
        accepted_total += append_entries(category, converted[index : index + batch_size])
    return accepted_total


def load_import_file(path: Path) -> List[dict]:
    if path.suffix.lower() == ".jsonl":
        return list(iter_jsonl(path))
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("json import file must contain an array")
        return payload
    raise ValueError("supported import formats: .json, .jsonl")


def print_stats() -> None:
    state = load_state()
    rows = []
    for key, spec in CATEGORY_SPECS.items():
        info = state["categories"][key]
        imported = int(info["imported_count"])
        target = spec.target_count
        progress = "n/a" if target == 0 else f"{(imported / target * 100):.2f}%"
        rows.append(
            {
                "category": key,
                "file": spec.file_name,
                "imported": imported,
                "target": target,
                "progress": progress,
                "last_batch": info["last_batch"],
            }
        )
    sys.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")


def http_get_json(url: str, insecure: bool = False) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "medaix-ru-dataset-pipeline/1.0"})
    context = ssl._create_unverified_context() if insecure else None
    with urlopen(request, timeout=HTTP_TIMEOUT, context=context) as response:
        return json.load(response)


def http_get_text(url: str, insecure: bool = False) -> str:
    request = Request(url, headers={"Accept": "*/*", "User-Agent": "medaix-ru-dataset-pipeline/1.0"})
    context = ssl._create_unverified_context() if insecure else None
    with urlopen(request, timeout=HTTP_TIMEOUT, context=context) as response:
        return response.read().decode("utf-8", "replace")


def first_text(value) -> str:
    if isinstance(value, list):
        for item in value:
            item = str(item or "").strip()
            if item:
                return item
        return ""
    return str(value or "").strip()


def listify(value) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def openfda_records(endpoint: str, limit: int, skip: int = 0, search: Optional[str] = None, insecure: bool = False) -> Iterator[dict]:
    page_size = min(MAX_BATCH_SIZE, 100)
    fetched = 0
    current_skip = skip
    while fetched < limit:
        batch_limit = min(page_size, limit - fetched)
        url = f"https://api.fda.gov/{endpoint}.json?limit={batch_limit}&skip={current_skip}"
        if search:
            url += "&search=" + quote_plus(search)
        payload = http_get_json(url, insecure=insecure)
        results = payload.get("results", [])
        if not results:
            break
        for item in results:
            yield item
            fetched += 1
        current_skip += len(results)
        if len(results) < batch_limit:
            break


def build_substance_entry(record: dict) -> Optional[dict]:
    openfda = record.get("openfda") or {}
    generic_name = first_text(openfda.get("generic_name")) or first_text(openfda.get("substance_name"))
    if not generic_name:
        return None
    pharm_class = first_text(openfda.get("pharm_class_epc")) or first_text(openfda.get("pharm_class_cs")) or "unknown"
    indications = first_text(record.get("indications_and_usage")) or first_text(record.get("purpose")) or "unknown"
    contraindications = first_text(record.get("contraindications")) or first_text(record.get("do_not_use")) or "unknown"
    mechanism = pharm_class if pharm_class != "unknown" else "unknown"
    aliases = listify(openfda.get("substance_name"))
    description = indications if indications != "unknown" else f"Reference entry for active substance {generic_name}."
    return {
        "name": generic_name,
        "aliases": aliases,
        "description": description,
        "metadata": {
            "mechanism": mechanism,
            "indications": indications,
            "contraindications": contraindications,
            "atc_class": "unknown",
            "source": "openfda.drug.label",
        },
    }


def build_drug_entry(record: dict) -> Optional[dict]:
    openfda = record.get("openfda") or {}
    brand_name = first_text(openfda.get("brand_name"))
    active = first_text(openfda.get("generic_name")) or first_text(openfda.get("substance_name"))
    if not brand_name or not active:
        return None
    manufacturer = first_text(openfda.get("manufacturer_name")) or "unknown"
    aliases = listify(openfda.get("product_ndc"))
    return {
        "name": brand_name,
        "aliases": aliases,
        "description": f"Trade drug name mapped from openFDA labeling data for {active}.",
        "metadata": {
            "active_ingredient": active,
            "manufacturer": manufacturer,
            "source": "openfda.drug.label",
        },
    }


def build_company_entry(record: dict) -> Optional[dict]:
    openfda = record.get("openfda") or {}
    company = first_text(openfda.get("manufacturer_name"))
    if not company:
        return None
    areas = listify(openfda.get("pharm_class_epc")) or listify(openfda.get("product_type")) or ["unknown"]
    return {
        "name": company,
        "aliases": [],
        "description": f"Pharmaceutical company referenced in openFDA drug labeling records.",
        "metadata": {
            "country": "unknown",
            "therapeutic_areas": areas,
            "source": "openfda.drug.label",
        },
    }


def ndc_records(limit: int, skip: int = 0, insecure: bool = False) -> Iterator[dict]:
    page_size = min(MAX_BATCH_SIZE * 2, 1000)
    fetched = 0
    current_skip = skip
    while fetched < limit:
        batch_limit = min(page_size, limit - fetched)
        payload = http_get_json(
            f"https://api.fda.gov/drug/ndc.json?limit={batch_limit}&skip={current_skip}",
            insecure=insecure,
        )
        results = payload.get("results", [])
        if not results:
            break
        for item in results:
            yield item
            fetched += 1
        current_skip += len(results)
        if len(results) < batch_limit:
            break


def build_ndc_substance_entries(record: dict) -> List[dict]:
    results: List[dict] = []
    ingredients = record.get("active_ingredients") or []
    if ingredients:
        for item in ingredients:
            name = str((item or {}).get("name") or "").strip()
            strength = str((item or {}).get("strength") or "").strip()
            if not name:
                continue
            description = f"Active substance from FDA NDC product listings. Strength reference: {strength or 'unknown'}."
            results.append(
                {
                    "name": name,
                    "aliases": [],
                    "description": description,
                    "metadata": {
                        "mechanism": "unknown",
                        "indications": str(record.get("generic_name") or record.get("brand_name") or "unknown"),
                        "contraindications": "unknown",
                        "atc_class": "unknown",
                        "strength": strength or "unknown",
                        "source": "openfda.drug.ndc",
                    },
                }
            )
        return results
    generic_name = str(record.get("generic_name") or "").strip()
    if generic_name:
        results.append(
            {
                "name": generic_name,
                "aliases": [],
                "description": "Active substance or generic product name from FDA NDC product listings.",
                "metadata": {
                    "mechanism": "unknown",
                    "indications": str(record.get("brand_name") or "unknown"),
                    "contraindications": "unknown",
                    "atc_class": "unknown",
                    "source": "openfda.drug.ndc",
                },
            }
        )
    return results


def build_ndc_drug_entry(record: dict) -> Optional[dict]:
    brand_name = str(record.get("brand_name") or "").strip()
    if not brand_name:
        return None
    active_ingredients = [str((x or {}).get("name") or "").strip() for x in (record.get("active_ingredients") or [])]
    active_ingredients = [x for x in active_ingredients if x]
    if not active_ingredients:
        generic_name = str(record.get("generic_name") or "").strip()
        active_ingredients = [generic_name] if generic_name else []
    if not active_ingredients:
        return None
    labeler = str(record.get("labeler_name") or "").strip() or "unknown"
    form = str(record.get("dosage_form") or "").strip()
    route = ", ".join(record.get("route") or []) if isinstance(record.get("route"), list) else str(record.get("route") or "").strip()
    description = f"FDA NDC listed drug product. Dosage form: {form or 'unknown'}. Route: {route or 'unknown'}."
    aliases = [str(record.get("product_ndc") or "").strip(), str(record.get("product_id") or "").strip()]
    aliases = [x for x in aliases if x]
    return {
        "name": brand_name,
        "aliases": aliases,
        "description": description,
        "metadata": {
            "active_ingredient": ", ".join(active_ingredients),
            "manufacturer": labeler,
            "source": "openfda.drug.ndc",
            "dosage_form": form or "unknown",
            "route": route or "unknown",
        },
    }


def build_ndc_company_entry(record: dict) -> Optional[dict]:
    company = str(record.get("labeler_name") or "").strip()
    if not company:
        return None
    therapeutic = str(record.get("product_type") or "").strip() or "unknown"
    dosage_form = str(record.get("dosage_form") or "").strip() or "unknown"
    route = ", ".join(record.get("route") or []) if isinstance(record.get("route"), list) else str(record.get("route") or "").strip() or "unknown"
    return {
        "name": company,
        "aliases": [],
        "description": f"FDA NDC labeler organization appearing in marketed product listings. Common dosage form reference: {dosage_form}.",
        "metadata": {
            "country": "unknown",
            "therapeutic_areas": [therapeutic],
            "source": "openfda.drug.ndc",
            "main_routes": [route],
        },
    }


def rxnorm_allconcepts(tty: str, limit: int, insecure: bool = False) -> Iterator[dict]:
    payload = http_get_json(f"https://rxnav.nlm.nih.gov/REST/allconcepts.json?tty={quote_plus(tty)}", insecure=insecure)
    concepts = payload.get("minConceptGroup", {}).get("minConcept", [])
    for concept in concepts[:limit]:
        yield concept


def build_rxnorm_substance_entry(concept: dict) -> Optional[dict]:
    name = str(concept.get("name") or "").strip()
    tty = str(concept.get("tty") or "").strip()
    if not name or tty not in {"IN", "PIN", "MIN"}:
        return None
    return {
        "name": name,
        "aliases": [concept.get("rxcui")] if concept.get("rxcui") else [],
        "description": f"RxNorm {tty} concept.",
        "metadata": {
            "mechanism": "unknown",
            "indications": "unknown",
            "contraindications": "unknown",
            "atc_class": "unknown",
            "source": "rxnorm.allconcepts",
            "rxcui": str(concept.get("rxcui") or ""),
            "tty": tty,
        },
    }


def build_rxnorm_brand_entry(concept: dict) -> Optional[dict]:
    name = str(concept.get("name") or "").strip()
    tty = str(concept.get("tty") or "").strip()
    if not name or tty != "BN":
        return None
    return {
        "name": name,
        "aliases": [concept.get("rxcui")] if concept.get("rxcui") else [],
        "description": f"RxNorm brand name concept.",
        "metadata": {
            "active_ingredient": "unknown",
            "manufacturer": "unknown",
            "source": "rxnorm.allconcepts",
            "rxcui": str(concept.get("rxcui") or ""),
            "tty": tty,
        },
    }


def iter_icd_nodes(element: ET.Element) -> Iterator[ET.Element]:
    for child in element:
        if child.tag.endswith("diag"):
            yield child
            yield from iter_icd_nodes(child)


def extract_icd_text(node: ET.Element, tag_name: str) -> str:
    for child in node:
        if child.tag.endswith(tag_name):
            return "".join(child.itertext()).strip()
    return ""


def cdc_icd10_entries(limit: int, url: str, insecure: bool = False) -> Iterator[dict]:
    xml_text = http_get_text(url, insecure=insecure)
    root = ET.fromstring(xml_text)
    count = 0
    for node in iter_icd_nodes(root):
        code = extract_icd_text(node, "name")
        desc = extract_icd_text(node, "desc")
        if not code or not desc:
            continue
        yield {
            "name": desc,
            "aliases": [code],
            "description": f"ICD-10-CM {code}: {desc}",
            "metadata": {
                "icd10": code,
                "symptoms": "unknown",
                "treatment_category": "reference",
                "affected_system": "unknown",
                "source": "cdc.icd10cm",
            },
        }
        count += 1
        if count >= limit:
            break


def clinicaltables_icd10_entries(limit: int, batch_size: int) -> Iterator[dict]:
    fetched = 0
    offset = 0
    query_terms = "a"
    while fetched < limit:
        count = min(batch_size, limit - fetched)
        url = (
            "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"
            f"?sf=code,name&terms={quote_plus(query_terms)}&count={count}&offset={offset}"
        )
        payload = http_get_json(url)
        rows = payload[3] if isinstance(payload, list) and len(payload) > 3 else []
        if not rows:
            break
        for code, name in rows:
            if not code or not name:
                continue
            yield {
                "name": str(name).strip(),
                "aliases": [str(code).strip()],
                "description": f"ICD-10-CM {code}: {name}",
                "metadata": {
                    "icd10": str(code).strip(),
                    "symptoms": "unknown",
                    "treatment_category": "reference",
                    "affected_system": "unknown",
                    "source": "nlm.clinicaltables.icd10cm",
                },
            }
            fetched += 1
            if fetched >= limit:
                break
        offset += len(rows)
        if len(rows) < count:
            break


def ols_uberon_entries(limit: int, page_size: int, insecure: bool = False) -> Iterator[dict]:
    fetched = 0
    page = 0
    while fetched < limit:
        url = f"https://www.ebi.ac.uk/ols4/api/ontologies/uberon/terms?page={page}&size={page_size}"
        payload = http_get_json(url, insecure=insecure)
        terms = payload.get("_embedded", {}).get("terms", [])
        if not terms:
            break
        for term in terms:
            name = str(term.get("label") or "").strip()
            if not name:
                continue
            description = first_text(term.get("description")) or f"Uberon anatomy term {name}."
            aliases = listify(term.get("synonyms"))
            system = "unknown"
            if "organ" in name.lower():
                kind = "organ"
            elif "nerve" in name.lower():
                kind = "nerve"
            elif "bone" in name.lower():
                kind = "bone"
            elif "muscle" in name.lower():
                kind = "muscle"
            elif "vessel" in name.lower() or "arter" in name.lower() or "vein" in name.lower():
                kind = "vessel"
            else:
                kind = "anatomy"
            yield {
                "name": name,
                "aliases": aliases,
                "description": description,
                "metadata": {
                    "type": kind,
                    "system": system,
                    "source": "ols4.uberon",
                    "iri": str(term.get("iri") or ""),
                },
            }
            fetched += 1
            if fetched >= limit:
                break
        page += 1


def device_classification_entries(limit: int, skip: int = 0, insecure: bool = False) -> Iterator[dict]:
    page_size = min(MAX_BATCH_SIZE * 2, 1000)
    fetched = 0
    current_skip = skip
    while fetched < limit:
        batch_limit = min(page_size, limit - fetched)
        payload = http_get_json(
            f"https://api.fda.gov/device/classification.json?limit={batch_limit}&skip={current_skip}",
            insecure=insecure,
        )
        results = payload.get("results", [])
        if not results:
            break
        for item in results:
            name = str(item.get("device_name") or "").strip()
            definition = str(item.get("definition") or "").strip()
            specialty = str(item.get("medical_specialty_description") or item.get("medical_specialty") or "").strip()
            if not name:
                continue
            yield {
                "name": name,
                "aliases": [str(item.get("product_code") or "").strip(), str(item.get("regulation_number") or "").strip()],
                "description": definition or f"FDA medical device classification entry for {name}.",
                "metadata": {
                    "purpose": definition or "classification reference",
                    "usage_area": specialty or "unknown",
                    "source": "openfda.device.classification",
                    "device_class": str(item.get("device_class") or "unknown"),
                },
            }
            fetched += 1
            if fetched >= limit:
                break
        current_skip += len(results)
        if len(results) < batch_limit:
            break


def wikidata_anatomy_entries(limit: int, offset: int = 0) -> Iterator[dict]:
    fetched = 0
    page_size = min(limit, 200)
    current_offset = offset
    while fetched < limit:
        current_limit = min(page_size, limit - fetched)
        query = f"""
SELECT ?item ?itemLabel ?itemDescription WHERE {{
  ?item wdt:P31/wdt:P279* wd:Q4936952 .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {current_limit}
OFFSET {current_offset}
"""
        url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
        request = Request(url, headers={"Accept": "application/sparql-results+json", "User-Agent": "medaix-ru-dataset-pipeline/1.0"})
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            payload = json.load(response)
        rows = payload.get("results", {}).get("bindings", [])
        if not rows:
            break
        for row in rows:
            name = row.get("itemLabel", {}).get("value", "").strip()
            desc = row.get("itemDescription", {}).get("value", "").strip()
            iri = row.get("item", {}).get("value", "").strip()
            if not name:
                continue
            lower = name.lower()
            if "bone" in lower:
                kind = "bone"
            elif "nerve" in lower:
                kind = "nerve"
            elif "arter" in lower or "vein" in lower or "vessel" in lower:
                kind = "vessel"
            elif "muscle" in lower:
                kind = "muscle"
            elif "organ" in lower:
                kind = "organ"
            else:
                kind = "body_part"
            yield {
                "name": name,
                "aliases": [iri] if iri else [],
                "description": desc or f"Anatomical structure: {name}.",
                "metadata": {
                    "type": kind,
                    "system": "unknown",
                    "source": "wikidata.anatomy",
                },
            }
            fetched += 1
            if fetched >= limit:
                break
        current_offset += len(rows)


def specialty_entries() -> Iterator[dict]:
    for name, description in SPECIALTIES_SEED:
        yield {
            "name": name,
            "aliases": [],
            "description": description,
            "metadata": {
                "scope": "medical specialty",
                "source": "seed.specialties",
            },
        }


def import_iterable(category: str, rows: Iterable[dict], batch_size: int) -> int:
    accepted_total = 0
    buffer: List[dict] = []
    for row in rows:
        buffer.append(row)
        if len(buffer) >= batch_size:
            accepted_total += append_entries(category, buffer)
            buffer = []
    if buffer:
        accepted_total += append_entries(category, buffer)
    return accepted_total


def validate_all() -> None:
    for category in CATEGORY_SPECS:
        seen: Set[str] = set()
        for entry in iter_jsonl(file_path_for(category)):
            validated = validate_entry(category, entry)
            key = normalize_name(validated["name"])
            if key in seen:
                raise ValueError(f"{category}: duplicate name: {validated['name']}")
            seen.add(key)
    sys.stdout.write("{\"ok\": true}\n")


def cmd_init(_: argparse.Namespace) -> None:
    ensure_storage()
    print_stats()


def cmd_stats(_: argparse.Namespace) -> None:
    print_stats()


def cmd_validate(_: argparse.Namespace) -> None:
    validate_all()


def cmd_import(args: argparse.Namespace) -> None:
    batch_size = args.batch_size
    payload = load_import_file(Path(args.input))
    accepted_total = 0
    for index in range(0, len(payload), batch_size):
        accepted_total += append_entries(args.category, payload[index : index + batch_size])
    sys.stdout.write(json.dumps({"accepted": accepted_total}, ensure_ascii=False) + "\n")


def cmd_import_seed(args: argparse.Namespace) -> None:
    imported = import_seed_array(Path(args.input), args.category, args.batch_size)
    sys.stdout.write(json.dumps({"accepted": imported}, ensure_ascii=False) + "\n")


def cmd_make_batch(args: argparse.Namespace) -> None:
    spec = CATEGORY_SPECS[args.category]
    state = load_state()
    imported = state["categories"][args.category]["imported_count"]
    next_batch = state["categories"][args.category]["last_batch"] + 1
    payload = {
        "category": args.category,
        "file": spec.file_name,
        "target_count": spec.target_count,
        "imported_count": imported,
        "remaining_count": max(spec.target_count - imported, 0) if spec.target_count else None,
        "batch_number": next_batch,
        "batch_size": args.batch_size,
        "constraints": {
            "model": {
                "id": "string",
                "category": "string",
                "name": "string",
                "aliases": [],
                "description": "string",
                "metadata": {},
            },
            "dedup_key": "normalized lowercase name",
            "required_metadata": list(spec.required_metadata),
            "max_items": args.batch_size,
            "min_items": MIN_BATCH_SIZE,
        },
        "notes": spec.description,
    }
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def cmd_fetch_openfda(args: argparse.Namespace) -> None:
    accepted = {
        "substances": 0,
        "drugs": 0,
        "companies": 0,
    }
    records = list(openfda_records("drug/label", limit=args.limit, skip=args.skip, search=args.search, insecure=args.insecure))
    accepted["substances"] = import_iterable(
        "substances",
        (entry for entry in (build_substance_entry(record) for record in records) if entry),
        args.batch_size,
    )
    accepted["drugs"] = import_iterable(
        "drugs",
        (entry for entry in (build_drug_entry(record) for record in records) if entry),
        args.batch_size,
    )
    accepted["companies"] = import_iterable(
        "companies",
        (entry for entry in (build_company_entry(record) for record in records) if entry),
        args.batch_size,
    )
    sys.stdout.write(json.dumps(accepted, ensure_ascii=False) + "\n")


def cmd_fetch_ndc(args: argparse.Namespace) -> None:
    accepted = {"substances": 0, "drugs": 0, "companies": 0}
    substance_rows = []
    drug_rows = []
    company_rows = []
    for record in ndc_records(args.limit, skip=args.skip, insecure=args.insecure):
        substance_rows.extend(build_ndc_substance_entries(record))
        drug = build_ndc_drug_entry(record)
        company = build_ndc_company_entry(record)
        if drug:
            drug_rows.append(drug)
        if company:
            company_rows.append(company)
    accepted["substances"] = import_iterable("substances", substance_rows, args.batch_size)
    accepted["drugs"] = import_iterable("drugs", drug_rows, args.batch_size)
    accepted["companies"] = import_iterable("companies", company_rows, args.batch_size)
    sys.stdout.write(json.dumps(accepted, ensure_ascii=False) + "\n")


def cmd_fetch_rxnorm(args: argparse.Namespace) -> None:
    substances = import_iterable(
        "substances",
        (entry for entry in (build_rxnorm_substance_entry(concept) for concept in rxnorm_allconcepts("IN+PIN+MIN", args.limit, insecure=args.insecure)) if entry),
        args.batch_size,
    )
    drugs = import_iterable(
        "drugs",
        (entry for entry in (build_rxnorm_brand_entry(concept) for concept in rxnorm_allconcepts("BN", args.limit, insecure=args.insecure)) if entry),
        args.batch_size,
    )
    sys.stdout.write(json.dumps({"substances": substances, "drugs": drugs}, ensure_ascii=False) + "\n")


def cmd_fetch_icd10(args: argparse.Namespace) -> None:
    accepted = import_iterable(
        "diseases",
        cdc_icd10_entries(args.limit, args.url, insecure=args.insecure),
        args.batch_size,
    )
    sys.stdout.write(json.dumps({"diseases": accepted}, ensure_ascii=False) + "\n")


def cmd_fetch_icd10_ct(args: argparse.Namespace) -> None:
    accepted = import_iterable(
        "diseases",
        clinicaltables_icd10_entries(args.limit, args.count_per_request),
        args.batch_size,
    )
    sys.stdout.write(json.dumps({"diseases": accepted}, ensure_ascii=False) + "\n")


def cmd_fetch_uberon(args: argparse.Namespace) -> None:
    accepted = import_iterable(
        "anatomy",
        ols_uberon_entries(args.limit, args.page_size, insecure=args.insecure),
        args.batch_size,
    )
    sys.stdout.write(json.dumps({"anatomy": accepted}, ensure_ascii=False) + "\n")


def cmd_fetch_devices(args: argparse.Namespace) -> None:
    accepted = import_iterable(
        "instruments",
        device_classification_entries(args.limit, skip=args.skip, insecure=args.insecure),
        args.batch_size,
    )
    sys.stdout.write(json.dumps({"instruments": accepted}, ensure_ascii=False) + "\n")


def cmd_fetch_wikidata_anatomy(args: argparse.Namespace) -> None:
    accepted = import_iterable(
        "anatomy",
        wikidata_anatomy_entries(args.limit, offset=args.offset),
        args.batch_size,
    )
    sys.stdout.write(json.dumps({"anatomy": accepted}, ensure_ascii=False) + "\n")


def cmd_import_specialties(args: argparse.Namespace) -> None:
    accepted = import_iterable("specialties", specialty_entries(), args.batch_size)
    sys.stdout.write(json.dumps({"specialties": accepted}, ensure_ascii=False) + "\n")


def cmd_sync_server(args: argparse.Namespace) -> None:
    result = run_startup_sync(remove_incoming=args.remove_incoming)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


def cmd_reset_category(args: argparse.Namespace) -> None:
    sys.stdout.write(json.dumps(reset_category_storage(args.category), ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Medical knowledge base dataset pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init")
    init_parser.set_defaults(func=cmd_init)

    stats_parser = sub.add_parser("stats")
    stats_parser.set_defaults(func=cmd_stats)

    validate_parser = sub.add_parser("validate")
    validate_parser.set_defaults(func=cmd_validate)

    import_parser = sub.add_parser("import")
    import_parser.add_argument("--category", choices=sorted(CATEGORY_SPECS), required=True)
    import_parser.add_argument("--input", required=True)
    import_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    import_parser.set_defaults(func=cmd_import)

    seed_parser = sub.add_parser("import-seed")
    seed_parser.add_argument("--category", choices=sorted(CATEGORY_SPECS), required=True)
    seed_parser.add_argument("--input", required=True)
    seed_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    seed_parser.set_defaults(func=cmd_import_seed)

    batch_parser = sub.add_parser("make-batch")
    batch_parser.add_argument("--category", choices=sorted(CATEGORY_SPECS), required=True)
    batch_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    batch_parser.set_defaults(func=cmd_make_batch)

    fda_parser = sub.add_parser("fetch-openfda")
    fda_parser.add_argument("--limit", type=int, default=500)
    fda_parser.add_argument("--skip", type=int, default=0)
    fda_parser.add_argument("--search")
    fda_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    fda_parser.add_argument("--insecure", action="store_true")
    fda_parser.set_defaults(func=cmd_fetch_openfda)

    ndc_parser = sub.add_parser("fetch-ndc")
    ndc_parser.add_argument("--limit", type=int, default=1000)
    ndc_parser.add_argument("--skip", type=int, default=0)
    ndc_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ndc_parser.add_argument("--insecure", action="store_true")
    ndc_parser.set_defaults(func=cmd_fetch_ndc)

    rxnorm_parser = sub.add_parser("fetch-rxnorm")
    rxnorm_parser.add_argument("--limit", type=int, default=500)
    rxnorm_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    rxnorm_parser.add_argument("--insecure", action="store_true")
    rxnorm_parser.set_defaults(func=cmd_fetch_rxnorm)

    icd_parser = sub.add_parser("fetch-icd10")
    icd_parser.add_argument("--limit", type=int, default=500)
    icd_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    icd_parser.add_argument(
        "--url",
        default="https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/2025/icd10cm-tabular-2025.xml",
    )
    icd_parser.add_argument("--insecure", action="store_true")
    icd_parser.set_defaults(func=cmd_fetch_icd10)

    icd_ct_parser = sub.add_parser("fetch-icd10-ct")
    icd_ct_parser.add_argument("--limit", type=int, default=10000)
    icd_ct_parser.add_argument("--count-per-request", type=int, default=500)
    icd_ct_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    icd_ct_parser.set_defaults(func=cmd_fetch_icd10_ct)

    uberon_parser = sub.add_parser("fetch-uberon")
    uberon_parser.add_argument("--limit", type=int, default=500)
    uberon_parser.add_argument("--page-size", type=int, default=100)
    uberon_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    uberon_parser.add_argument("--insecure", action="store_true")
    uberon_parser.set_defaults(func=cmd_fetch_uberon)

    device_parser = sub.add_parser("fetch-devices")
    device_parser.add_argument("--limit", type=int, default=1000)
    device_parser.add_argument("--skip", type=int, default=0)
    device_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    device_parser.add_argument("--insecure", action="store_true")
    device_parser.set_defaults(func=cmd_fetch_devices)

    wikidata_anatomy_parser = sub.add_parser("fetch-wikidata-anatomy")
    wikidata_anatomy_parser.add_argument("--limit", type=int, default=1000)
    wikidata_anatomy_parser.add_argument("--offset", type=int, default=0)
    wikidata_anatomy_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    wikidata_anatomy_parser.set_defaults(func=cmd_fetch_wikidata_anatomy)

    specialties_parser = sub.add_parser("import-specialties")
    specialties_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    specialties_parser.set_defaults(func=cmd_import_specialties)

    sync_parser = sub.add_parser("sync-server")
    sync_parser.add_argument("--remove-incoming", action="store_true")
    sync_parser.set_defaults(func=cmd_sync_server)

    reset_parser = sub.add_parser("reset-category")
    reset_parser.add_argument("--category", choices=sorted(CATEGORY_SPECS), required=True)
    reset_parser.set_defaults(func=cmd_reset_category)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batch_size = getattr(args, "batch_size", DEFAULT_BATCH_SIZE)
    if batch_size < MIN_BATCH_SIZE or batch_size > MAX_BATCH_SIZE:
        raise SystemExit(f"batch-size must be between {MIN_BATCH_SIZE} and {MAX_BATCH_SIZE}")
    args.func(args)


if __name__ == "__main__":
    main()

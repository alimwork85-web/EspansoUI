"""قراءة ملف اختصارات Espanso (base.yml) وكتابته بأمان.

المحلل هنا صارم عمدًا: يفهم الشكل البسيط فقط (trigger و replace وبعض الخيارات
البسيطة). إن وجد في الملف شيئًا لا يفهمه يرفض الكتابة بدل أن يخرّب الملف.

© 2026 Ali Alhumedi - MIT License
"""

import json
import os
import re
import shutil
import time

APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", APPDATA)

# يمكن توجيه البرنامج إلى ملف آخر للاختبار دون لمس الملف الحقيقي
MATCH_FILE = os.environ.get(
    "ESPANSO_UI_FILE", os.path.join(APPDATA, "espanso", "match", "base.yml")
)
BACKUP_DIR = os.environ.get(
    "ESPANSO_UI_BACKUPS", os.path.join(LOCALAPPDATA, "EspansoUI", "backups")
)
MAX_BACKUPS = 50

# الخيارات البسيطة التي نحافظ عليها كما هي إن وُجدت في الملف
EXTRA_KEYS = ("word", "left_word", "right_word", "propagate_case", "label")

HEADER = "# هذا الملف تديره أداة EspansoUI - يمكنك تعديله منها بدل التعديل اليدوي\n"


class UnsupportedFile(Exception):
    """الملف يحتوي على صيغة لا نفهمها، فلا نكتب فوقه."""


# ---------- القراءة ----------

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0", " ": " "}


def _decode_double(body, line_no):
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            i += 1
            if i >= len(body):
                raise UnsupportedFile(f"السطر {line_no}: نص غير مكتمل")
            e = body[i]
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
            elif e == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 1:i + 5]):
                out.append(chr(int(body[i + 1:i + 5], 16)))
                i += 4
            else:
                raise UnsupportedFile(f"السطر {line_no}: رمز هروب غير مدعوم \\{e}")
        elif ch == '"':
            raise UnsupportedFile(f"السطر {line_no}: علامة تنصيص في غير مكانها")
        else:
            out.append(ch)
        i += 1
    s = "".join(out)
    # JSON يكتب الحروف المركبة بزوجين 😀 فنعيد تركيبها
    return s.encode("utf-16", "surrogatepass").decode("utf-16")


def _parse_scalar(raw, line_no):
    raw = raw.strip()
    if not raw:
        raise UnsupportedFile(f"السطر {line_no}: قيمة فارغة أو متعددة الأسطر غير مدعومة")
    if raw.startswith('"'):
        m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"\s*(#.*)?', raw)
        if not m:
            raise UnsupportedFile(f"السطر {line_no}: نص بين علامتي تنصيص غير صحيح")
        return _decode_double(m.group(1), line_no)
    if raw.startswith("'"):
        m = re.fullmatch(r"'((?:[^']|'')*)'\s*(#.*)?", raw)
        if not m:
            raise UnsupportedFile(f"السطر {line_no}: نص بين علامتي تنصيص مفردة غير صحيح")
        return m.group(1).replace("''", "'")
    if raw[0] in "|>[{&*!%@`":
        raise UnsupportedFile(f"السطر {line_no}: صيغة متقدمة غير مدعومة ({raw[0]})")
    value = re.sub(r"\s+#.*$", "", raw).strip()
    if value in ("true", "false"):
        return value == "true"
    return value


def parse(text):
    """يحوّل نص الملف إلى قائمة اختصارات [{trigger, replace, ...}]."""
    matches, current, seen_root = [], None, False
    for no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise UnsupportedFile(f"السطر {no}: مسافة Tab في بداية السطر")
        if re.fullmatch(r"matches:\s*(#.*)?", line):
            seen_root = True
            continue
        if not seen_root:
            raise UnsupportedFile(f"السطر {no}: يوجد قسم غير 'matches' ({stripped[:30]})")
        m = re.fullmatch(r"(\s*)- (\w+):(.*)", line)
        if m:
            current = {}
            matches.append(current)
            key, val = m.group(2), m.group(3)
        else:
            m = re.fullmatch(r"(\s+)(\w+):(.*)", line)
            if not m or current is None:
                raise UnsupportedFile(f"السطر {no}: لم أفهم هذا السطر ({stripped[:30]})")
            key, val = m.group(2), m.group(3)
        if key not in ("trigger", "replace") + EXTRA_KEYS:
            raise UnsupportedFile(f"السطر {no}: الخيار '{key}' غير مدعوم في الواجهة")
        if key in current:
            raise UnsupportedFile(f"السطر {no}: الخيار '{key}' مكرر")
        current[key] = _parse_scalar(val, no)
    for i, item in enumerate(matches, 1):
        if not isinstance(item.get("trigger"), str) or not isinstance(item.get("replace"), str):
            raise UnsupportedFile(f"الاختصار رقم {i} ينقصه trigger أو replace")
    return matches


def load():
    if not os.path.exists(MATCH_FILE):
        return []
    with open(MATCH_FILE, encoding="utf-8-sig") as f:
        return parse(f.read())


# ---------- الكتابة ----------

def _q(value):
    # النص بصيغة JSON صالح في YAML أيضًا، ويحمي من أي رمز خاص
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def dump(matches):
    lines = [HEADER, "matches:\n"]
    for item in matches:
        lines.append(f"  - trigger: {_q(item['trigger'])}\n")
        lines.append(f"    replace: {_q(item['replace'])}\n")
        for key in EXTRA_KEYS:
            if key in item:
                lines.append(f"    {key}: {_q(item[key])}\n")
    return "".join(lines)


def validate(matches):
    """يرجع رسالة خطأ بالعربية، أو None إن كانت القائمة سليمة."""
    seen = set()
    for i, item in enumerate(matches, 1):
        t, r = item.get("trigger"), item.get("replace")
        if not isinstance(t, str) or not t.strip():
            return f"الاختصار رقم {i} فارغ"
        if not isinstance(r, str) or r == "":
            return f"النص الكامل للاختصار «{t}» فارغ"
        if t in seen:
            return f"الاختصار «{t}» مكرر"
        seen.add(t)
    return None


def _backup():
    if not os.path.exists(MATCH_FILE):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = time.strftime("base-%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}.yml.bak"
    dest = os.path.join(BACKUP_DIR, name)
    shutil.copy2(MATCH_FILE, dest)
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".bak"))
    for f in old[:-MAX_BACKUPS]:
        os.remove(os.path.join(BACKUP_DIR, f))
    return dest


def save(matches):
    err = validate(matches)
    if err:
        raise ValueError(err)
    clean = []
    for item in matches:
        entry = {"trigger": item["trigger"], "replace": item["replace"]}
        for key in EXTRA_KEYS:
            if key in item:
                entry[key] = item[key]
        clean.append(entry)
    text = dump(clean)
    # تحقق قبل الكتابة: ما سنكتبه يُقرأ ويعطي البيانات نفسها
    if parse(text) != clean:
        raise ValueError("فشل التحقق من الملف قبل حفظه، لم يتغير شيء")
    # لا نكتب فوق ملف لا نفهمه
    load()
    _backup()
    os.makedirs(os.path.dirname(MATCH_FILE), exist_ok=True)
    tmp = MATCH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, MATCH_FILE)
    return clean

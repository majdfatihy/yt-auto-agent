"""Guard public reel titles against greetings, sentence fragments, and off-topic copy."""
import re
import unicodedata

BANNED_PREFIXES = (
    "مقتطف", "مقطع من التفريغ", "مرحبا", "مرحباً", "اهلا", "أهلا",
    "السلام عليكم", "ومعتبرش", "وبعد كده", "بعد كده", "اسمه طبعا",
    "اسمه طبعاً", "هتكمل عملية", "هنا بيقول", "خلينا نشوف",
    "ازيكوا", "معاكم", "يعني", "المبلغ اللي", "هنقول النهارده"
)
FILLER = {
    "شرح", "طريقة", "كيفية", "خطوات", "معلومات", "فيديو", "شورتس",
    "علشان", "واللي", "اللي", "ازاي", "إزاي", "الي", "إلى", "على",
    "من", "في", "هذا", "هذه", "عشان", "هنا", "هنعمل", "هو",
    "انا", "احنا", "ممكن", "مع", "ماذا", "الذي", "حاجة",
    "اول", "أول", "ثم", "بعد", "كل", "مش", "كان", "ان", "إن",
}
AR_MARKS = re.compile(r"[\u064b-\u065f\u0670]")


def _norm(word):
    word = AR_MARKS.sub("", unicodedata.normalize("NFKC", str(word))).lower()
    word = word.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))
    word = re.sub(r"[^\w]", "", word)
    if word.startswith("وال") and len(word) >= 7:
        word = word[3:]
    elif word.startswith("ال") and len(word) >= 6:
        word = word[2:]
    return word


def valid_title(title, transcript=None):
    if not isinstance(title, str):
        return False
    clean = " ".join(title.split()).strip(" .،:-")
    if not 15 <= len(clean) <= 100 or "\n" in title or "http" in clean.lower():
        return False
    if re.match(r"^#?\d", clean):
        return False
    no_marks = AR_MARKS.sub("", clean).translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"})).lower()
    if any(no_marks.startswith(AR_MARKS.sub("", b).translate(str.maketrans({"أ":"ا", "إ":"ا", "آ":"ا"})).lower())
           for b in BANNED_PREFIXES):
        return False
    tokens = [_norm(x) for x in clean.split()]
    substantive = [x for x in tokens if len(x) >= 4 and x not in FILLER]
    if len(substantive) < 2:
        return False
    if transcript:
        source = {_norm(x) for x in transcript.split()}
        source.discard("")
        # A title must contain at least one substantive word spoken in the segment,
        # to reject general and off-topic AI titles.
        if not any(x in source for x in substantive):
            return False
    return True

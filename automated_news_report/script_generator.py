# script_generator.py — Dynamic context-aware news narrative generator
# Target voiceover: 25–35 seconds  (~65–90 words @ 150 wpm)
#
# Pipeline:
#   clean -> spaCy doc -> detect_context -> generate_hook (doc-driven)
#   -> build_story (scored body) -> generate_ending ->  regulate word count

import sys

# PHASE 4: Environment lock
import os as _os_env_check
if "VIRTUAL_ENV" not in _os_env_check.environ and ".venv" not in sys.executable and "venv" not in sys.executable:
    print(f"[WARN] Running outside a virtual environment: {sys.executable}")

print(f"[ENV OK] {sys.executable}")

import heapq
import re
import spacy
import random
import requests

def clean_caption_text(text):
    text = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\b\d+[A-Z]\b', '', text)
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def fix_streaming_duplicates(text):
    words = text.split()
    cleaned = []
    
    i = 0
    while i < len(words):
        # If current word is prefix of next -> skip current
        if i < len(words) - 1 and words[i+1].startswith(words[i]):
            i += 1
            continue
            
        cleaned.append(words[i])
        i += 1
        
    return " ".join(cleaned)

def remove_exact_duplicates(text):
    words = text.split()
    result = []
    prev = None
    
    for w in words:
        if w != prev:
            result.append(w)
        prev = w
        
    return " ".join(result)

def is_weak_hook(line):
    bad_starts = ("as ", "in ", "today", "what", "this", "recently")
    return line.lower().startswith(bad_starts)

def validate_script(script, target_words):
    words = script.split()
    if len(words) < int(target_words * 0.7):
        return False

    first_line = script.split(".")[0]
    if len(first_line.split()) < 6:
        return False

    if first_line.lower().startswith(("today", "yesterday", "president")):
        return False

    if not any(char.isupper() for char in first_line if char.isalpha()):
        return False

    # PHASE 14: Reject scripts full of generic filler phrases
    _FILLER_PHRASES = [
        "global angle", "power of nature", "power of natural",
        "highlights the need", "serves as a reminder",
        "underscores the importance", "raising questions about",
        "experts warn", "analysts say", "the world watches",
        "as the situation", "it remains to be seen",
        "the coming days will", "the international community",
    ]
    _sentences = script.split(".")
    _filler_count = sum(
        1 for sent in _sentences
        if any(filler in sent.lower() for filler in _FILLER_PHRASES)
    )
    # If more than 2 sentences are generic filler, reject and regenerate
    if _filler_count >= 2:
        print(f"[ScriptGen] ⚠️  Script has {_filler_count} generic filler sentences — regenerating")
        return False

    return True

# ── Constants ─────────────────────────────────────────────────────────────────
SPEECH_RATE_WPS = 2.5  # slightly faster = more words needed to fill time

MAX_VIDEO_SEC = 55
MIN_VIDEO_SEC = 45

# ── spaCy model ───────────────────────────────────────────────────────────────
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    raise OSError(
        "[ScriptGen] spaCy model not found. Run:\n"
        "  python -m spacy download en_core_web_sm"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Text utilities
# ══════════════════════════════════════════════════════════════════════════════

def _clean(text: str) -> str:
    """Normalise whitespace and remove duplicate punctuation."""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\.{2,}", ".", text)                    # ellipsis -> single dot
    text = re.sub(r"([.!?])\s*([.!?])+", r"\1", text)    # !! / .! -> single
    text = re.sub(r"\s+([.,!?])", r"\1", text)            # space before punct
    return text.strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _sentence_list(text: str) -> list[str]:
    """Split on sentence-ending punctuation; keep each sentence intact."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _dedupe_sentences(sentences: list[str]) -> list[str]:
    """Remove exact and near-duplicate sentences (first-10-word fingerprint)."""
    seen: set[str] = set()
    out: list[str] = []
    for s in sentences:
        key = " ".join(s.lower().split()[:10])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Context / emotion detection
# ══════════════════════════════════════════════════════════════════════════════

def detect_context(doc) -> str:
    """
    Score all context categories, return the highest-scoring one.
    Uses additive scoring so war stories with political figures
    are not misclassified as 'politics'.
    """
    text = doc.text.lower()

    # Violence override — if active violence words are present,
    # tense detection is never suppressed
    VIOLENCE_OVERRIDE = {
        "violating", "violation", "violated", "breached", "breach",
        "attack", "attacked", "attacking", "fired", "firing", "shelling",
        "killed", "wounded", "casualties", "troops", "military",
        "invasion", "invaded", "airstrike", "bombardment", "artillery",
        "separatist", "offensive", "counteroffensive", "clash", "clashes",
        "conflict", "war", "hostage", "bomb", "explosion", "ceasefire",
        "gunfire", "shooting", "shooter", "assault", "siege",
    }
    has_violence = any(v in text for v in VIOLENCE_OVERRIDE)

    # Neutralisers — only ceremony/commemoration suppress tense, NOT diplomacy
    TENSE_NEUTRALISERS = {
        "state visit", "ceremony", "ceremonial", "inauguration",
        "swearing-in", "commemorate", "memorial", "tribute",
        "trade deal", "trade agreement",
    }
    tense_neutralised = (not has_violence) and any(w in text for w in TENSE_NEUTRALISERS)

    # Scoring rules: {label: [(keywords, weight_per_hit)]}
    CONTEXT_SCORES = {
        "tense": [
            (["war", "conflict", "invasion", "airstrike", "bomb", "hostage",
              "ceasefire violation", "military action"], 3),
            (["attack", "attacked", "fired", "firing", "shelling", "troops",
              "casualties", "wounded", "killed", "clash", "clashes",
              "shooting", "armed", "offensive", "siege"], 2),
            (["terror", "terrorism", "security breach", "police", "arrest",
              "threat level", "separatist", "gunfire"], 1),
        ],
        "serious": [
            (["earthquake", "flood", "tsunami", "wildfire", "hurricane",
              "death toll", "disaster", "famine", "epidemic", "pandemic"], 3),
            (["crisis", "rescue", "evacuation", "emergency", "storm",
              "tornado", "avalanche", "landslide"], 2),
        ],
        "politics": [
            (["prime minister", "president", "election", "parliament",
              "congress", "senate", "legislation", "cabinet", "minister"], 2),
            (["policy", "vote", "ballot", "summit", "diplomatic",
              "sanctions", "treaty", "accord"], 1),
        ],
        "positive": [
            (["win", "victory", "record", "celebration", "award",
              "milestone", "breakthrough", "historic"], 2),
            (["success", "achievement", "champion", "triumph"], 1),
        ],
        "informative": [
            (["artificial intelligence", "space", "nasa", "innovation",
              "discovery", "research", "quantum", "nuclear energy"], 2),
            (["technology", "ai", "robot", "software", "satellite",
              "launch", "cyber", "digital"], 1),
        ],
    }

    scores = {label: 0 for label in CONTEXT_SCORES}

    for label, rules in CONTEXT_SCORES.items():
        for keywords, weight in rules:
            for kw in keywords:
                if kw in text:
                    scores[label] += weight

    # Suppress tense if neutralised (and no violence)
    if tense_neutralised:
        scores["tense"] = 0

    # Tense always wins over politics if tied or close (war > diplomacy)
    if scores["tense"] > 0 and scores["tense"] >= scores["politics"] - 1:
        scores["politics"] = max(0, scores["politics"] - 2)

    best_label = max(scores, key=scores.get)
    best_score = scores[best_label]

    print(f"[ScriptGen] Context scores: {scores}")
    print(f"[ScriptGen] Context detected: '{best_label}' (score={best_score})")

    return best_label if best_score > 0 else "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Word-count regulators
# ══════════════════════════════════════════════════════════════════════════════

def estimate_duration_from_text(text):
    words = len(text.split())
    return words / SPEECH_RATE_WPS

def calculate_target_words(input_text: str) -> int:
    # Phase 10: Increased minimums to hit 40-59s naturally.
    # At 2.5 words/sec, 50s = 125 words.
    base_words = len(input_text.split())
    max_words = int(65 * SPEECH_RATE_WPS)  # ~162 words
    min_words = int(48 * SPEECH_RATE_WPS)  # ~120 words
    return max(min(base_words, max_words), min_words)

def _normalize_ollama_output(text: str) -> str:
    text = text.strip()
    if not text:
        return text

    # ── Strip known LLM meta-commentary prefixes ──────────────────────
    # These patterns appear when llama3 explains itself before the script.
    # We strip everything up to and including the colon on the same line.
    LEAK_PATTERNS = [
        r"(?i)^here[\s\S]*?(?:script|output|response)[:\s]+",
        r"(?i)^sure[\s\S]*?(?:script|output|response)[:\s]+",
        r"(?i)^okay[\s\S]*?(?:script|output|response)[:\s]+",
        r"(?i)^below is[\s\S]*?(?:script|output|response)[:\s]+",
        r"(?i)^this is[\s\S]*?(?:script|output|response)[:\s]+",
        r"(?i)^i(?:'ve| have) written[\s\S]*?[:\s]+",
        r"(?i)^(?:here|below)[^\n]*?[\d]+[- ]word[^\n]*\n",
    ]

    import re as _re
    for pattern in LEAK_PATTERNS:
        text = _re.sub(pattern, "", text, count=1).strip()

    # ── Strip existing markers ────────────────────────────────────────
    for marker in [
        "Thinking Process:", "Final Answer:", "Output:",
        "Answer:", "Response:", "Script:", "Note:"
    ]:
        if marker in text:
            text = text.split(marker)[-1].strip()

    # ── Strip any leading line that has no terminal punctuation
    # and is under 12 words — these are always header/label lines ────
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if (first
                and not first.endswith((".", "!", "?", '"', "'"))
                and len(first.split()) <= 12):
            lines.pop(0)
        else:
            break
    text = "\n".join(lines).strip()

    # PHASE 14: Strip structural labels Llama3 sometimes inserts despite instructions
    import re as _label_re
    # Patterns like "Global angle:", "Hook:", "1. Hook —", "Context:", etc.
    text = _label_re.sub(
        r'(?m)^(?:Hook|Context|Scale|Location|Eyewitness|Impact|Response|'
        r'Ongoing|Close|Global angle|Background|Key fact|Reaction|'
        r'Escalation|Closing|Summary|Narrative|Body|Intro|Outro)'
        r'\s*[-:—]\s*',
        '',
        text
    )
    # Strip numbered prefixes: "1. " "2) " "1:" etc.
    text = _label_re.sub(r'(?m)^\s*\d{1,2}[.):\-]\s+', '', text)
    # Clean double spaces after stripping
    text = _label_re.sub(r'\s{2,}', ' ', text).strip()

    return text


def _run_ollama_model(prompt: str, model: str) -> tuple[str, str, int]:
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": 512,    # enough for 8 × 14-word sentences
            "temperature": 0.60,   # lower = more factual, less hallucination
            "top_p": 0.85,
            "repeat_penalty": 1.15,  # prevents repeating same location names
            "stop": ["\n\n\n", "Note:", "Note that", "Remember:", "Disclaimer:"],
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=120)
        if response.status_code != 200:
            return "", f"HTTP {response.status_code}", response.status_code
        data = response.json()
        stdout = data.get("response", "").strip()
        return stdout, "", 0
    except Exception as e:
        return "", str(e), 1


def generate_script_with_ollama(input_text, target_words, context: str = "neutral"):
    HOOK_INSTRUCTIONS = {
        "tense": "HOOK RULE: Create URGENCY and TENSION. Communicate immediate danger or crisis.",
        "serious": "HOOK RULE: Communicate GRAVITY and human cost. Lead with the most impactful fact.",
        "politics": "HOOK RULE: Communicate STAKES and SIGNIFICANCE without drama. Lead with key decision makers.",
        "informative": "HOOK RULE: Spark CURIOSITY about a discovery or development. Lead with the most surprising fact.",
        "positive": "HOOK RULE: Communicate ACHIEVEMENT and SIGNIFICANCE. Lead with the milestone.",
        "neutral": "HOOK RULE: Be CLEAR and DIRECT. State the news immediately.",
    }

    hook_instruction = HOOK_INSTRUCTIONS.get(context, HOOK_INSTRUCTIONS["neutral"])

    # -- Extract named entities from input_text for injection -------------
    import re as _re
    _ner_doc   = nlp(input_text)
    _persons   = [e.text for e in _ner_doc.ents if e.label_ == "PERSON"][:6]
    _orgs      = [e.text for e in _ner_doc.ents if e.label_ == "ORG"][:4]
    _gpes      = [e.text for e in _ner_doc.ents if e.label_ == "GPE"][:3]

    _entity_block = ""
    if _persons:
        _entity_block += f"NAMED INDIVIDUALS (must appear in script): {', '.join(_persons)}\n"
    if _orgs:
        _entity_block += f"KEY ORGANISATIONS (must appear in script): {', '.join(_orgs)}\n"
    if _gpes:
        _entity_block += f"LOCATIONS: {', '.join(_gpes)}\n"

    prompt = (
        "You are writing a 60-second YouTube Shorts news narration. "
        "Your job is to make the viewer feel like they're THERE — urgent, specific, vivid.\n\n"
        "MANDATORY: Use ALL of these real names and numbers from the article:\n"
        f"{_entity_block}\n"
        "EXTRACT THESE FROM THE ARTICLE (use them — do not invent):\n"
        "- Exact death/casualty numbers with specific locations\n"
        "- Named officials and their exact actions/quotes\n"
        "- Specific incident details (what happened, where, how)\n"
        "- Any viral or human-interest moments from the article\n"
        "- Any rescue operations or government response\n"
        "- Any warnings or ongoing risks mentioned\n\n"
        "WRITE 8 COMPLETE SENTENCES following this EXACT structure:\n"
        "1. HOOK — One shocking fact or number that grabs attention immediately\n"
        "2. SCALE — The full scope of damage (deaths, locations, destruction)\n"
        "3. LOCATION — The hardest-hit area with its specific casualty count\n"
        "4. EYEWITNESS — A specific person, their story, or a vivid detail from the article\n"
        "5. IMPACT — What is destroyed, cut off, or damaged (infrastructure, bridges, villages)\n"
        "6. RESPONSE — Official action: what the government/CM/authorities did or said\n"
        "7. ONGOING — Current risk, rescue operations, or what is happening right now\n"
        "8. CLOSE — Powerful final line that gives scale or stakes\n\n"
        "STRICT RULES:\n"
        "- EVERY sentence must name a SPECIFIC place, person, or number from the article\n"
        "- NO vague lines like 'global angle', 'power of nature', 'experts warn'\n"
        "- NO section labels or headers like '1.' '2.' 'Hook:' 'Context:'\n"
        "- NO sentence starting with: As / While / Although / Despite / Following / Amid\n"
        "- NO sentence ending with: a preposition / an adjective / 'and' / 'leaving'\n"
        "- Each sentence: subject + verb + specific object. 10-14 words.\n"
        "- Write like a BBC anchor reading breaking news, not a textbook\n"
        "- Use present or past tense — never passive voice\n\n"
        f"{hook_instruction}\n\n"
        f"LENGTH: {target_words} to {target_words + 40} words total.\n\n"
        "RETURN ONLY the 8 narration sentences — no labels, no headers, no preamble.\n\n"
        f"ARTICLE:\n{input_text}"
    )




    model_candidates = ["llama3"]

    for model in model_candidates:
        stdout, stderr, rc = _run_ollama_model(prompt, model)
        if stdout:
            normalized = _normalize_ollama_output(stdout)
            if normalized:
                print(f"[Ollama] Using model: {model}")
                return normalized
    return ""

def clean_tts_text(text):
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'\s*\.\s*', '. ', text)
    return text.strip()

def enforce_context(script):
    first_line = script.split('.')[0]
    has_proper_noun = any(word[0].isupper() for word in first_line.split() if len(word) > 2 and word not in ("The", "A", "An", "In", "On", "At", "It", "This", "That"))
    if not has_proper_noun:
        print("[CONTEXT WARNING] First line may lack a clear subject or location.")
    return script

def _strip_incomplete_tail(script: str) -> str:
    sentences = _sentence_list(script)
    if len(sentences) <= 3: return script
    cleaned = list(sentences)
    while len(cleaned) > 3:
        last = cleaned[-1].strip()
        if not last.endswith((".", "!", "?")):
            cleaned.pop()
            continue
        break
    return " ".join(cleaned)


def _spacy_fallback_script(text: str, doc, context: str) -> str:
    """
    Extractive fallback when Ollama is unavailable.
    Uses frequency-scored sentence selection via spaCy.
    """
    import heapq

    sentences = [sent.text.strip() for sent in doc.sents if len(sent.text.split()) >= 6]
    if not sentences:
        return text

    word_freq = {}
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "and", "or", "but",
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
        "it", "this", "that", "be", "been", "being", "have", "has", "had",
    }
    for token in doc:
        word = token.text.lower()
        if word not in stopwords and token.is_alpha:
            word_freq[word] = word_freq.get(word, 0) + 1

    max_freq = max(word_freq.values()) if word_freq else 1
    for word in word_freq:
        word_freq[word] /= max_freq

    sent_scores = {}
    for sent in sentences:
        for word in sent.lower().split():
            if word in word_freq:
                sent_scores[sent] = sent_scores.get(sent, 0) + word_freq[word]

    top_sents = heapq.nlargest(8, sent_scores, key=sent_scores.get)
    ordered = [s for s in sentences if s in top_sents]

    # PHASE 14: Keep only sentences that are reasonable length for TTS
    ordered = [s for s in ordered if 6 <= len(s.split()) <= 20][:7]

    CONTEXT_HOOKS = {
        "tense":       "Developing story — this is raising serious concerns.",
        "serious":     "A significant situation is unfolding.",
        "politics":    "Major political developments are emerging.",
        "positive":    "A notable achievement has been announced.",
        "informative": "Here is what you need to know.",
        "neutral":     "Here is the latest news.",
    }
    hook = CONTEXT_HOOKS.get(context, "Here is the latest news.")

    script = hook + " " + " ".join(ordered)
    return _clean(script)


def _fact_check_entities(script: str, original_text: str) -> str:
    """
    Verify named entities in the generated script appear in the original article.
    Removes sentences that contain hallucinated person names.
    """
    original_lower = original_text.lower()

    # Phase 9: Non-reverting fact checker.
    # Keep the valid sentences and only remove those that fail verification.
    valid_sentences = []
    removed_count = 0

    sentences = _sentence_list(script)
    for sent in sentences:
        is_hallucination = False
        
        # Check if any PERSON or long ORG from the script is NOT in the original
        sent_doc = nlp(sent)
        sent_entities = [e.text for e in sent_doc.ents if e.label_ in ("PERSON", "ORG") and len(e.text.split()) >= 2]
        
        for entity in sent_entities:
            # Phase 13: Expanded allowlist for humanitarian and geopolitical entities
            _ALWAYS_VALID = {
                # Geopolitical entities
                "iran", "israel", "gaza", "russia", "ukraine", "us", "china",
                "india", "pakistan", "taiwan", "united states", "united kingdom",
                "european union", "nato", "european",
                # Major news organizations
                "bbc", "reuters", "ap", "afp", "al jazeera", "ndtv", "cnn",
                "associated press", "agence france-presse",
                # UN system and humanitarian organizations — NEVER hallucinations
                "united nations", "un", "unicef", "who", "wfp", "unhcr",
                "world food programme", "world health organization",
                "international committee", "red cross", "icrc",
                "international monetary fund", "imf", "world bank",
                "human rights watch", "amnesty international",
                "doctors without borders", "médecins sans frontières",
                "oxfam", "save the children", "care international",
                # Aviation
                "air india", "boeing", "airbus", "indigo",
                # Common Indian institutions
                "supreme court", "high court", "dgca",
                # Middle East entities
                "hamas", "hezbollah", "idf", "icc", "opec",
                # Major country leaders (likely real)
                "secretary-general", "prime minister", "president",
            }
            # Also allow if entity contains known valid words
            if any(v in entity.lower() for v in [
                "united", "international", "world", "global", "national",
                "minister", "secretary", "committee", "organization", "programme",
                "india", "air", "israel", "iran", "gaza", "red cross",
            ]) or entity.lower() in _ALWAYS_VALID:
                continue
            
            # Check if entity (or its main words) exists in original
            words_to_check = [w.lower() for w in entity.split() if len(w) > 3]
            if words_to_check:
                matches = sum(1 for w in words_to_check if w in original_lower)
                # PHASE 13: Only flag as hallucination if NO words appear
                # in original. < 50% match is too aggressive for short articles.
                if matches == 0:
                    is_hallucination = True
                    break
        
        if is_hallucination:
            print(f"[FACT CHECK] Removed hallucinated sentence: '{sent[:50]}...'")
            removed_count += 1
        else:
            valid_sentences.append(sent)

    # PHASE 12: Fact-checker threshold = 3 (was 6 — too high, gutted short articles).
    # Never restore hallucinations just because few valid sentences remain.
    if len(valid_sentences) >= 3:
        print(f"[FACT CHECK] Keeping {len(valid_sentences)} clean sentences.")
        return " ".join(valid_sentences)

    if len(valid_sentences) >= 1:
        print(f"[FACT CHECK] Only {len(valid_sentences)} sentence(s) — "
              f"keeping clean version anyway (hallucinations not restored).")
        return " ".join(valid_sentences)

    print(f"[FACT CHECK] Nothing survived — keeping original (hallucinations present).")
    return script


def summarise(text: str) -> str:
    text = _clean(text)
    if not text: return ""

    doc = nlp(text)
    context = detect_context(doc)

    target_words = calculate_target_words(text)

    script = ""
    for _ in range(3):
        try:
            script = generate_script_with_ollama(text, target_words, context=context)
            if validate_script(script, target_words):
                break
        except Exception as ollama_exc:
            print(f"[ScriptGen] Ollama attempt failed: {ollama_exc}")
            script = ""

    script = script.strip()
    if not script:
        print("[ScriptGen] Ollama unavailable — using spaCy extractive fallback.")
        script = _spacy_fallback_script(text, doc, context)

    if not script:
        print("[ScriptGen] Both Ollama and spaCy fallback failed. Using article title.")
        script = text.split("\n")[0].replace("TITLE:", "").strip()

    # Cleaners
    script = clean_caption_text(script)
    script = fix_streaming_duplicates(script)
    script = remove_exact_duplicates(script)
    script = enforce_context(script)
    script = clean_tts_text(script)
    script = _strip_incomplete_tail(script)
    script = _scrub_artifacts(script)
    script = _fact_check_entities(script, text)

    # Ensure every sentence ends with a period
    _sentences = _sentence_list(script)
    _sentences = [s.strip() + "." if not s.strip().endswith((".", "!", "?")) else s.strip() for s in _sentences]
    script = " ".join(_sentences)

    return script


def _call_ollama_api(prompt: str) -> str:
    """Helper to call Ollama via existing _run_ollama_model."""
    stdout, stderr, rc = _run_ollama_model(prompt, "llama3")
    return stdout

def generate_dynamic_cta(headline: str, context: str) -> dict:
    """
    Ask Ollama to write a news-specific 3-line outro CTA.

    Returns a dict with keys:
        main_line   - punchy 6-10 word question about THIS story
        sub_line    - 4-8 word engagement prompt
        engage_line - 5-8 word follow/subscribe CTA

    Falls back to context-matched defaults when Ollama is unavailable.
    """
    DEFAULTS = {
        "tense": {
            "main_line":   "Could this spark a global conflict?",
            "sub_line":    "Share your view in the comments.",
            "engage_line": "Subscribe for daily breaking news.",
        },
        "war": {
            "main_line":   "Is the world on the edge of all-out war?",
            "sub_line":    "Tell us what you think below.",
            "engage_line": "Follow for live conflict updates.",
        },
        "politics": {
            "main_line":   "Will this decision change history?",
            "sub_line":    "Share your take in the comments.",
            "engage_line": "Subscribe for daily political coverage.",
        },
        "serious": {
            "main_line":   "Who is responsible for this tragedy?",
            "sub_line":    "Share this to raise awareness.",
            "engage_line": "Follow for the latest developments.",
        },
        "positive": {
            "main_line":   "Is this the breakthrough we have been waiting for?",
            "sub_line":    "Tell us your thoughts below.",
            "engage_line": "Subscribe for more inspiring stories.",
        },
        "informative": {
            "main_line":   "How will this change your daily life?",
            "sub_line":    "Share your point of view below.",
            "engage_line": "Follow for daily science and tech news.",
        },
        "business": {
            "main_line":   "Could this crash the global economy?",
            "sub_line":    "Drop your analysis in the comments.",
            "engage_line": "Subscribe for daily market updates.",
        },
        "disaster": {
            "main_line":   "How prepared are we for the next disaster?",
            "sub_line":    "Share this with your community.",
            "engage_line": "Follow for emergency updates.",
        },
    }
    default = dict(DEFAULTS.get(context, {
        "main_line":   "What do you think about this?",
        "sub_line":    "Share your thoughts below.",
        "engage_line": "Follow for daily news updates.",
    }))
    HARD_CLOSE = "Share your point of view or thoughts in the comments below, and subscribe for daily news videos."
    default["hard_close"] = HARD_CLOSE

    if not headline:
        return default

    # Extract key entities from headline to anchor the CTA
    import re as _cta_re
    _hl_words = [w for w in _cta_re.findall(r"[A-Z][a-z]+", headline or "")][:4]
    _entity_anchor = ", ".join(_hl_words) if _hl_words else "this story"

    # Build a list of off-topic concepts to ban
    OFF_TOPIC_BAN = {
        "tense": "COVID, pandemic, climate change, sports, entertainment",
        "war": "COVID, pandemic, sports, economy, technology",
        "politics": "COVID, sports, entertainment, technology",
        "serious": "COVID, sports, entertainment, economy",
        "informative": "COVID, sports, entertainment",
        "positive": "COVID, pandemic, war, conflict",
        "neutral": "COVID, pandemic",
    }
    ban_list = OFF_TOPIC_BAN.get(context, "COVID, pandemic")

    prompt = (
        "You are a social media news editor writing the OUTRO for a vertical news video.\n\n"
        f"THIS VIDEO IS SPECIFICALLY ABOUT: {headline}\n"
        f"KEY PEOPLE/PLACES IN THIS STORY: {_entity_anchor}\n"
        f"STORY EMOTIONAL TONE: {context}\n\n"
        "STRICT RULES:\n"
        "- Your question MUST reference the specific story above — not a generic topic.\n"
        f"- BANNED TOPICS (do NOT mention): {ban_list}\n"
        "- Do NOT use the phrase 'Stay Updated', 'Stay Informed', or 'What do you think'.\n"
        "- The MAIN_LINE must name a specific element from THIS story.\n"
        "- Keep every line under 12 words.\n\n"
        "Write exactly these three lines:\n"
        "MAIN_LINE: [punchy 6-10 word question or statement directly about THIS story]\n"
        "SUB_LINE: [4-8 word engagement prompt]\n"
        "ENGAGE_LINE: [5-8 word follow/subscribe CTA]\n\n"
        "Return ONLY those three lines. No preamble. No notes. No explanation."
    )

    try:
        raw = _call_ollama_api(prompt)
    except Exception:
        raw = ""

    if not raw:
        print("[CTA] Ollama unavailable - using default CTA")
        return default

    result = dict(default)
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith("MAIN_LINE:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if 4 < len(val) < 120:
                result["main_line"] = val
        elif line.startswith("SUB_LINE:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if 4 < len(val) < 80:
                result["sub_line"] = val
        elif line.startswith("ENGAGE_LINE:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if 4 < len(val) < 80:
                result["engage_line"] = val

    # Always append the hard engagement closer to engage_line
    result["hard_close"] = HARD_CLOSE

    print(f"[CTA] main='{result['main_line']}'")
    print(f"[CTA] sub='{result['sub_line']}'")
    print(f"[CTA] engage='{result['engage_line']}'")
    return result

def _scrub_artifacts(script: str) -> str:
    """
    Final cleanup pass on the generated script.
    Removes four categories of Ollama output artifacts:

    1. Sentences fused by merge artifacts
       e.g. "...to the According to sources,."
       Fix: detect ". [Capital]" or ", [Capital attribution]" boundaries
       inside a sentence and truncate at the first clean ending.

    2. Duplicate attribution phrases
       e.g. "According to sources" appearing more than once.
       Fix: keep only the first occurrence.

    3. Sentences ending on a bare country/org abbreviation
       e.g. "...instructed the US."
       Fix: remove such sentences entirely.

    4. Sentences under 5 words that start with a bare verb/preposition
       (orphaned fragments like "Military to pause" or "Is seeking").
       Fix: discard them.
    """
    import re as _re

    # Safety: never scrub a script under 80 words — it is already too short
    # and aggressive scrubbing would leave nothing usable.
    if len(script.split()) < 35:
        print(f"[SCRUB] Script too short ({len(script.split())} words) — skipping scrub")
        return script

    sentences = _sentence_list(script)
    if not sentences:
        return script

    # Attribution phrases — deduplicated across the script
    ATTRIB_PHRASES = [
        "according to sources",
        "according to reports",
        "sources say",
        "sources indicate",
        "reports suggest",
        "officials say",
        "officials confirm",
        "according to",
    ]

    # Endings that signal a truncated sentence (country/org codes)
    BAD_ENDING_TOKENS = {
        "us", "uk", "eu", "un", "nato", "uae",
        "seeking", "to", "the", "a", "an", "of",
        "civilian", "military", "government", "official",
        "operation", "operations", "infrastructure",
        "situation", "development", "evidence",
    }

    # Orphan fragment starters (bare verb/preposition, no subject)
    ORPHAN_STARTERS = {
        "military", "forces", "to", "seeking", "citing",
        "indicating", "including", "following", "is", "are",
        "was", "were",
    }

    seen_attribs: set = set()
    cleaned: list = []

    for sent in sentences:
        sl = sent.lower().rstrip(" .!?,;:")

        # Rule 1 — Detect and truncate internal merge boundary
        # Pattern: "...word. According" or "...word, According"
        _parts = _re.split(r'\.\s+(?=[A-Z])', sent)
        if len(_parts) > 1:
            # Keep only first clean part
            sent = _parts[0].strip()
            if not sent.endswith((".", "!", "?")):
                sent += "."
            print(f"[SCRUB] Merge artifact truncated: ...{sent[-30:]}")

        # Rule 2 — Deduplicate attribution phrases
        _found_attrib = next(
            (a for a in ATTRIB_PHRASES if a in sent.lower()), None
        )
        if _found_attrib:
            if _found_attrib in seen_attribs:
                print(f"[SCRUB] Duplicate attribution removed: '{sent[:50]}'")
                continue
            seen_attribs.add(_found_attrib)

        # Rule 3 — Reject sentences ending on bad token
        _last_tok = sent.rstrip(".!?\"' ").split()[-1].lower() if sent.split() else ""
        if _last_tok in BAD_ENDING_TOKENS:
            print(f"[SCRUB] Bad ending token '{_last_tok}' — removed: '{sent[:50]}'")
            continue

        # Rule 4 — Reject short orphan fragments
        _words = sent.split()
        if (len(_words) < 6
                and _words
                and _words[0].lower().rstrip(".,") in ORPHAN_STARTERS):
            print(f"[SCRUB] Orphan fragment removed: '{sent[:50]}'")
            continue

        # Rule 6 — Remove generic filler / section-header sentences
        _FILLER_PATTERNS = [
            "global angle", "power of nature", "power of natural",
            "highlights the need for", "serves as a reminder",
            "serves as a stark reminder", "underscores the importance",
            "this serves to", "it serves as",
            "raises questions about", "it remains to be seen",
            "the coming days will", "the international community",
            "as a whole", "as we know", "as mentioned",
        ]
        _sent_lower = sent.lower()
        if any(fp in _sent_lower for fp in _FILLER_PATTERNS):
            print(f"[SCRUB] Generic filler removed: '{sent[:60]}'")
            continue

        # Rule 5 - Replace informal/tabloid words with professional equivalents.
        INFORMAL_REPLACEMENTS = {
            " whopping ":       " significant ",
            " massive ":        " major ",
            " huge ":           " substantial ",
            " shocking ":       " significant ",
            " bombshell ":      " major development ",
            " jaw-dropping ":   " notable ",
            " eye-watering ":   " substantial ",
            " mind-blowing ":   " remarkable ",
            " skyrocketed ":    " increased sharply ",
            " plummeted ":      " fell sharply ",
            " slammed ":        " criticized ",
            " blasted ":        " strongly criticized ",
            " sparks fury ":    " draws criticism ",
            " outrage ":        " criticism ",
            "!":                ".",
        }
        for informal, formal in INFORMAL_REPLACEMENTS.items():
            if informal.lower() in sent.lower():
                sent = sent.replace(informal, formal)
                sent = sent.replace(informal.strip().capitalize(), formal.strip().capitalize())

        cleaned.append(sent)

    result = " ".join(cleaned).strip()
    return result if result else script   # never return empty


if __name__ == "__main__":
    samples = {
        "tech": "Artificial intelligence is transforming many industries. Companies around the world are investing heavily in AI research.",
        "war": "Military forces launched airstrikes overnight. Dozens of casualties have been reported by officials.",
    }

    for label, text in samples.items():
        print(f"\nSAMPLE: {label.upper()}")
        result = summarise(text)
        print(f"Result: {result}")

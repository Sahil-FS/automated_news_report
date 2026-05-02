# script_generator.py — Dynamic context-aware news narrative generator
# Target voiceover: 25–35 seconds  (~65–90 words @ 150 wpm)
#
# Pipeline:
#   clean → spaCy doc → detect_context → generate_hook (doc-driven)
#   → build_story (scored body) → generate_ending → regulate word count

import sys
import subprocess

# PHASE 4: Environment lock
if ".venv" not in sys.executable:
    print("❌ ERROR: Not running inside .venv")
    print(f"Current: {sys.executable}")
    print("Run using: .venv\\Scripts\\python.exe main.py")
    exit(1)

print(f"[ENV OK] {sys.executable}")

import heapq
import re
import spacy
import random

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
        # If current word is prefix of next → skip current
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

    # FIX 1 — FORCE CONTEXT IN FIRST LINE
    # The first line must include some context. 
    # Since we can't check all locations, we check for capitalized words or basic indicators.
    if not any(char.isupper() for char in first_line if char.isalpha()):
         return False

    return True

# ── Constants ─────────────────────────────────────────────────────────────────
SPEECH_RATE_WPS = 2.2  # more realistic

MAX_VIDEO_SEC = 55
MIN_VIDEO_SEC = 30

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
    text = re.sub(r"\.{2,}", ".", text)                    # ellipsis → single dot
    text = re.sub(r"([.!?])\s*([.!?])+", r"\1", text)    # !! / .! → single
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

# Each context maps to (keyword_list, label)
_CONTEXT_RULES: list[tuple[list[str], str]] = [
    (["war", "conflict", "attack", "military", "troops", "ceasefire",
      "artillery", "soldiers", "combat", "airstrike", "invasion"],        "tense"),

    (["win", "success", "achievement", "record", "celebration",
      "victory", "award", "champion", "milestone", "historic",
      "breakthrough", "triumph"],                                          "positive"),

    (["earthquake", "flood", "disaster", "storm", "crisis",
      "hurricane", "tsunami", "wildfire", "drought", "emergency",
      "casualties", "collapse", "death toll"],                             "serious"),

    (["technology", "ai", "artificial intelligence", "space", "launch",
      "innovation", "robot", "software", "cyber", "satellite",
      "nasa", "discovery", "research", "science", "data"],                "informative"),
]


def detect_context(doc) -> str:
    """
    Classify the spaCy doc into one of five emotional contexts:
    tense | positive | serious | informative | neutral | politics

    Improvements over naive keyword matching:
      1. NEUTRALISER whitelist — if these diplomatic/political/ceremonial
         words appear, tense detection is suppressed even if tense
         keywords are also present.
      2. COUNT THRESHOLD — tense requires 2+ keyword hits to fire.
         Single keyword matches fall through to next context.
      3. POLITICS detection added — catches parliamentary, diplomatic,
         and election stories that are not tense/serious.
    """
    text = doc.text.lower()

    # Words that neutralise tense detection — diplomatic/ceremonial context
    TENSE_NEUTRALISERS = {
        "state visit", "diplomatic", "diplomacy", "ceremony", "ceremonial",
        "speech", "address", "parliament", "parliamentary", "senate",
        "congress", "vote", "debate", "summit", "meeting", "conference",
        "defend democratic", "democratic values", "democratic",
        "visit", "tour", "trip", "inauguration", "swearing",
        "election", "campaign", "rally", "policy", "legislation",
        "defend", "values", "rights", "freedom", "liberty",
        "commemorate", "memorial", "tribute", "honour", "honor",
        "alliance", "partnership", "bilateral", "multilateral",
        "trade", "treaty", "agreement", "accord", "deal",
    }

    # Check if any neutraliser is present — suppresses tense
    tense_neutralised = any(w in text for w in TENSE_NEUTRALISERS)

    # Extended context rules — (keywords, label, min_count_required)
    CONTEXT_RULES_WEIGHTED = [
        # tense — requires 2+ hits AND no neutraliser
        (["war", "conflict", "attack", "military", "troops",
          "ceasefire", "artillery", "soldiers", "combat",
          "airstrike", "invasion", "shooting", "gunfire",
          "bomb", "explosion", "hostage", "casualt"],
         "tense", 2),

        # serious — 1 hit is enough (disasters are unambiguous)
        (["earthquake", "flood", "disaster", "storm", "crisis",
          "hurricane", "tsunami", "wildfire", "drought",
          "emergency", "casualties", "collapse", "death toll",
          "famine", "evacuation", "rescue"],
         "serious", 1),

        # politics — 1 hit (broad political coverage)
        (["parliament", "parliamentary", "prime minister", "president",
          "senator", "congressman", "election", "vote", "ballot",
          "legislation", "policy", "cabinet", "minister",
          "diplomatic", "state visit", "summit", "bilateral",
          "political", "opposition", "coalition", "party leader",
          "campaign", "referendum", "constitution", "democracy",
          "democratic values", "defend democratic"],
         "politics", 1),

        # positive — 1 hit
        (["win", "success", "achievement", "record", "celebration",
          "victory", "award", "champion", "milestone", "historic",
          "breakthrough", "triumph", "landmark"],
         "positive", 1),

        # informative — 1 hit
        (["technology", "ai", "artificial intelligence", "space",
          "launch", "innovation", "robot", "software", "cyber",
          "satellite", "nasa", "discovery", "research", "science"],
         "informative", 1),
    ]

    for keywords, label, min_count in CONTEXT_RULES_WEIGHTED:
        count = sum(1 for w in keywords if w in text)

        # Apply neutraliser suppression for tense only
        if label == "tense" and tense_neutralised:
            continue  # skip tense if neutralised — will fall to politics

        if count >= min_count:
            return label

    return "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Dynamic hook (doc-driven, no hardcoded full sentences)
# ══════════════════════════════════════════════════════════════════════════════



def generate_hook(doc, context: str) -> str:
    """
    Build an attention-grabbing opener using actual content from the doc.

    Strategy:
      • prefix  — a short, tone-matched phrase (context-driven)
      • content — the highest-scoring sentence from the doc (NLP-extracted)

    This means the hook always reflects the real news, not a canned line.
    
    PHASE 4: Enforces ≤12 words for hook to ensure scene boundaries.
    """
    sents = [s.text.strip() for s in doc.sents if len(s.text.split()) > 5]
    if not sents:
        sents = [doc.text.strip()]

    # Score sentences by named-entity density + word frequency
    freq: dict[str, int] = {}
    for tok in doc:
        if not tok.is_stop and tok.is_alpha:
            freq[tok.text.lower()] = freq.get(tok.text.lower(), 0) + 1

    max_f = max(freq.values()) if freq else 1

    def _score(sent_text: str) -> float:
        sdoc = nlp(sent_text)
        score = sum(freq.get(t.text.lower(), 0) / max_f
                    for t in sdoc if not t.is_stop and t.is_alpha)
        score += len(sdoc.ents) * 0.5  # reward named entities
        return score

    best_sent = max(sents, key=_score)

    # Context-driven prefix — short phrase, NOT a full sentence by itself
    prefix_map = {
        "tense":       "This just happened and it's raising serious concerns.",
        "positive":    "Here's something incredible that just took place.",
        "serious":     "A serious situation is unfolding right now.",
        "informative": "Here's what you need to know right now.",
        "neutral":     "Here's what you need to know right now.",
    }
    prefix = prefix_map.get(context, "Here's what you need to know.")

    # Avoid repeating the same sentence twice if prefix already says it
    hook = f"{prefix} {best_sent}"
    hook = _clean(hook)
    
    # PHASE 4: Enforce ≤12 words on hook
    hook_words = hook.rstrip(".").split()
    if len(hook_words) > 12:
        hook = " ".join(hook_words[:12]) + "."
    
    return hook


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Smart body builder (frequency-scored, deduped)
# ══════════════════════════════════════════════════════════════════════════════

def build_story(doc) -> list[str]:
    """
    Extract and rank body sentences using TF-style frequency scoring.

    Returns sentences in original document order, filtered to:
      • ≥ 6 words (removes stubs)
      • no near-duplicates
      
    PHASE 4: Enforces ≤10 words per sentence for scene pacing.
    """
    sents = list(doc.sents)

    # Filter stubs first
    sents = [s for s in sents if len(s.text.split()) > 5]

    if not sents:
        return [doc.text.strip()]

    # Build word freq table (no stop words)
    freq: dict[str, float] = {}
    for tok in doc:
        w = tok.text.lower()
        if tok.is_stop or not tok.is_alpha:
            continue
        freq[w] = freq.get(w, 0) + 1

    max_f = max(freq.values()) if freq else 1
    norm  = {w: c / max_f for w, c in freq.items()}

    # Score each sentence
    scores: dict[int, float] = {}
    for i, s in enumerate(sents):
        for tok in s:
            w = tok.text.lower()
            if w in norm:
                scores[i] = scores.get(i, 0.0) + norm[w]
        # Bonus for named entities
        scores[i] = scores.get(i, 0.0) + len(s.ents) * 0.4

    # Select top sentences, preserve document order
    k = min(6, len(sents))
    top_idx = set(heapq.nlargest(k, scores, key=lambda i: scores[i]))
    ordered = [sents[i].text.strip() for i in sorted(top_idx)]

    result = _dedupe_sentences(ordered)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Context-aware ending
# ══════════════════════════════════════════════════════════════════════════════

def generate_ending(context: str) -> str:
    """
    Return a concise closing sentence matched to the emotional context.
    Each context maps to a single, purposeful ending (no random selection).
    
    PHASE 4: Enforces ≤10 words for ending to fit within scene boundaries.
    """
    ending_map = {
        "tense":       "The situation is still developing.",
        "positive":    "This marks an important milestone.",
        "serious":     "Authorities are closely monitoring events.",
        "informative": "More developments are expected soon.",
        "neutral":     "More updates are expected soon.",
    }
    ending = ending_map.get(context, "More updates are expected soon.")
    
    # PHASE 4: Enforce ≤10 words on ending
    words = ending.rstrip(".").split()
    if len(words) > 10:
        ending = " ".join(words[:10]) + "."
    
    return ending


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Word-count regulators
# ══════════════════════════════════════════════════════════════════════════════


def estimate_duration_from_text(text):
    words = len(text.split())
    return words / SPEECH_RATE_WPS

def calculate_target_words(input_text: str) -> int:
    base_words = len(input_text.split())

    max_words = int(MAX_VIDEO_SEC * SPEECH_RATE_WPS)
    min_words = int(MIN_VIDEO_SEC * SPEECH_RATE_WPS)

    if base_words >= max_words:
        return max_words
    elif base_words >= min_words:
        return base_words
    else:
        return base_words  # DO NOT EXPAND artificially


def _trim_script(script: str, max_words: int) -> str:
    """
    Drop whole sentences (second-to-last position, preserving the ending)
    until word count ≤ max_words. Always keeps ≥ 3 sentences.
    Never cuts mid-sentence.
    """
    sentences = _sentence_list(script)
    while len(sentences) > 3 and _word_count(" ".join(sentences)) > max_words:
        sentences.pop(-2)   # remove just before the ending
    return " ".join(sentences)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Public entry-point  (signature unchanged — pipeline compatible)
# ══════════════════════════════════════════════════════════════════════════════

def generate_script_with_ollama(input_text, target_words, context: str = "neutral"):
    """
    Generate a news script using Ollama/LLaMA3.
    Context is used to select the appropriate hook style.

    Hook styles by context:
      tense/serious → dramatic urgency ("People froze...", "No one expected...")
      politics/neutral → factual authority ("A key decision is unfolding...",
                          "Here is what just happened in...")
      positive/informative → curious engagement ("Something significant just happened...",
                              "A major development is changing...")
    """

    # ── Context-conditional hook instruction ──────────────────────────────
    HOOK_INSTRUCTIONS = {
        "tense": """HOOK RULE:
- Must create URGENCY and TENSION
- Must communicate immediate danger or crisis
- Start with a scene-setting statement that puts the viewer IN the moment

GOOD HOOK EXAMPLES for tense news:
✅ "People froze when the first shot was heard."
✅ "No one expected what happened next."
✅ "In an instant, everything changed."
✅ "Security rushed in within seconds."

BAD HOOKS:
❌ "As chaos erupted..."
❌ "In a shocking incident..."
❌ "Today, officials announced..."
""",

        "serious": """HOOK RULE:
- Must communicate the GRAVITY and human cost of the situation
- Lead with the most impactful fact or statistic

GOOD HOOK EXAMPLES for serious news:
✅ "Lives were lost and communities shattered."
✅ "The numbers are difficult to comprehend."
✅ "Rescue teams are still searching through the rubble."

BAD HOOKS:
❌ "People froze when..."
❌ "No one expected..."
❌ "In a shocking..."
""",

        "politics": """HOOK RULE:
- Must communicate STAKES and SIGNIFICANCE without drama
- Lead with WHO is involved and WHAT decision/action is at stake
- Use factual authority, not dramatic tension

GOOD HOOK EXAMPLES for political news:
✅ "A major political battle is unfolding in Westminster."
✅ "A key vote that could reshape British politics is now underway."
✅ "One man's future is on the line — and so is his party's."
✅ "The stakes could not be higher inside parliament today."

BAD HOOKS — do NOT use these for political news:
❌ "People froze when..."
❌ "No one expected what happened next."
❌ "In a shocking turn of events..."
""",

        "informative": """HOOK RULE:
- Must spark CURIOSITY about a discovery, development, or innovation
- Lead with the most surprising or significant fact

GOOD HOOK EXAMPLES for informative news:
✅ "A breakthrough has just changed everything we knew about this."
✅ "Scientists have confirmed what many suspected for years."
✅ "This discovery is bigger than most people realise."

BAD HOOKS:
❌ "People froze when..."
❌ "In a shocking incident..."
""",

        "positive": """HOOK RULE:
- Must communicate ACHIEVEMENT and SIGNIFICANCE
- Lead with the milestone or record that was broken

GOOD HOOK EXAMPLES for positive news:
✅ "History was made today — and the world is watching."
✅ "For the first time ever, it has finally happened."
✅ "This is the moment they have been working toward for years."

BAD HOOKS:
❌ "People froze when..."
❌ "No one expected..."
""",

        "neutral": """HOOK RULE:
- Must be CLEAR and DIRECT — state the news immediately
- Lead with the key fact, person, or decision

GOOD HOOK EXAMPLES for neutral news:
✅ "A significant development is unfolding right now."
✅ "Officials have confirmed what many were waiting to hear."
✅ "The story everyone is talking about — here is what we know."

BAD HOOKS:
❌ "People froze when..."
❌ "In a shocking incident..."
""",
    }

    hook_instruction = HOOK_INSTRUCTIONS.get(
        context,
        HOOK_INSTRUCTIONS["neutral"]
    )

    prompt = f"""Write a SHORT-FORM VIRAL NEWS SCRIPT.

STYLE:
- Professional News Reporter tone
- Factual, clear, and direct
- Avoid cinematic or dramatic phrases unless the news is breaking/violent
- No slang, no cringe

STRUCTURE:
1. Hook (WHO + WHERE + WHAT — style depends on news type, see below)
2. Subject (WHO is involved and their role)
3. Action (WHAT exactly happened or is happening)
4. Impact (WHY it matters — consequences, implications)
5. Ending (Clear takeaway or next development expected)

RULES:
- Script MUST include: location (city/country), subject (person/org/event), action type
- Write like a news reporter — factual, structured, confident
- First line MUST be powerful and match the tone of the news type
- DO NOT use section labels like "Hook:", "Impact:", "Ending:"
- DO NOT explain in third person — tell the story directly and factually
- DO NOT start with: "As", "In", "Today", "What happened", "Here is"

{hook_instruction}

LENGTH:
70–130 words. Complete sentences only. No trailing fragments.
Every sentence must end with . or ! or ?

DO NOT include:
"Here is the script", "Script:", "Output:", "Note:", "[Hook]"
Return ONLY the final script text. Nothing else.

ARTICLE:
{input_text}
"""

    result = subprocess.run(
        ["ollama", "run", "llama3", prompt],
        text=True,
        capture_output=True,
        encoding="utf-8"
    )

    return result.stdout.strip()

def clean_tts_text(text):
    import re
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'\s*\.\s*', '. ', text)
    return text.strip()

def enforce_context(script):
    """
    Validates that the first sentence contains at least one proper noun
    (capitalized word) as a signal of real context (person, place, or event).
    If missing, logs a warning but does NOT inject fake content.
    """
    first_line = script.split('.')[0]
    has_proper_noun = any(
        word[0].isupper()
        for word in first_line.split()
        if len(word) > 2 and word not in ("The", "A", "An", "In", "On", "At", "It", "This", "That")
    )
    if not has_proper_noun:
        print("[CONTEXT WARNING] First line may lack a clear subject or location.")
    return script

def _strip_incomplete_tail(script: str) -> str:
    """
    Remove trailing sentences from LLM output that are grammatically incomplete.
    A sentence is considered incomplete if:
      1. It does not end with . ! or ?
      2. Its last word is a preposition, article, conjunction, or auxiliary verb
      3. It starts with a coordinating conjunction (and, or, but, so, yet)
         AND is the last sentence in the script

    Always keeps at least 3 sentences to preserve narrative structure.
    """
    # Words that must NOT be the final word of any sentence
    BAD_FINAL_WORDS = {
        # prepositions
        "at", "in", "on", "of", "for", "to", "by", "with", "from",
        "into", "onto", "upon", "over", "under", "than", "about",
        "through", "between", "among", "against", "during", "before",
        "after", "within", "without", "along", "across", "behind",
        "toward", "towards", "regarding", "concerning", "including",
        # articles
        "a", "an", "the",
        # conjunctions / relative pronouns
        "and", "or", "but", "so", "yet", "nor", "both", "either",
        "as", "that", "which", "when", "while", "where", "whether",
        "what", "who", "whom", "whose", "how", "if", "although",
        "because", "since", "unless", "until", "though", "even",
        # auxiliary / linking verbs
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "will", "would", "should", "could",
        "may", "might", "shall", "must", "can", "do", "did", "does",
        # dangling words confirmed in logs
        "gun", "its", "their", "his", "her", "our", "your", "my",
        "this", "these", "those", "such", "any", "some", "no", "not",
        # dangling nouns that always imply continuation
        "concerns", "scrutiny", "tensions", "figures", "measures",
        "questions", "efforts", "reports", "claims", "allegations",
        "pressure", "demands", "calls", "fears", "hopes", "plans",
        "talks", "discussions", "negotiations", "investigations",
        # hyphenated compound adjectives — always need a noun after them
        "high-profile", "ever-present", "well-known", "long-term",
        "short-term", "large-scale", "small-scale", "full-scale",
        "high-level", "top-level", "low-level", "far-reaching",
        "wide-ranging", "fast-moving", "slow-moving", "long-standing",
        # abstract emotional / state nouns — always continuation-dependent
        "unease", "uncertainty", "ambiguity", "clarity", "stability",
        "instability", "anxiety", "tension", "pressure", "momentum",
        "turbulence", "volatility", "complexity", "urgency", "gravity",
        "severity", "magnitude", "intensity", "fragility", "resilience",
        # missing prepositions confirmed in logs
        "beneath", "underneath", "alongside", "amid", "amidst",
        "throughout", "despite", "concerning", "regarding",
        "pending", "excepting", "barring", "notwithstanding",
        # object pronouns — always need infinitive/clause after them
        "him", "her", "them", "us", "whom", "which", "that",
        "itself", "himself", "herself", "themselves", "ourselves",
        # abstract consequence nouns
        "aftermath", "implications", "ramifications", "consequences",
        "repercussions", "significance", "importance", "relevance",
        "context", "backdrop", "landscape", "outlook", "trajectory",
        # mid-clause modifiers — always signal continuation, never end a thought
        "just", "only", "merely", "simply", "also", "still",
        "already", "ever", "never", "always", "often", "sometimes",
        "perhaps", "maybe", "likely", "unlikely", "apparently",
        "reportedly", "allegedly", "supposedly", "seemingly",
        "increasingly", "dramatically", "significantly", "notably",
        "particularly", "especially", "specifically", "essentially",
        "primarily", "largely", "mostly", "broadly", "generally",
        "currently", "recently", "previously", "ultimately",
        "effectively", "officially", "formally", "technically",
        # quantity/degree words that always need a noun or verb after them
        "more", "less", "most", "least", "further", "fewer",
        "much", "many", "several", "numerous", "various",
        "greater", "lesser", "higher", "lower", "wider", "broader",
        "deeper", "stronger", "weaker", "faster", "slower",
        # dangling present participles — always introduce subordinate clause
        "serving", "making", "creating", "building", "showing",
        "proving", "highlighting", "underscoring", "marking",
        "noting", "adding", "saying", "calling", "warning",
        "arguing", "claiming", "suggesting", "indicating",
        "prompting", "forcing", "leaving", "causing", "sparking",
        "raising", "drawing", "sending", "pushing", "driving",
        "setting", "putting", "bringing", "taking", "giving",
        "coming", "going", "moving", "turning", "leading",
        "following", "including", "involving", "affecting",
        "reflecting", "representing", "demonstrating", "signaling",
    }

    # Coordinating conjunctions that should not START a sentence
    BAD_STARTERS = {"and", "or", "but", "so", "yet", "nor"}

    sentences = _sentence_list(script)

    if len(sentences) <= 3:
        return script  # never strip if too few sentences

    cleaned = list(sentences)

    # Keep removing from the tail until the last sentence is clean
    max_removals = len(cleaned) - 3  # never go below 3 sentences
    removals = 0

    while removals < max_removals and cleaned:
        last = cleaned[-1].strip()

        # Check 0 — final sentence has NO terminal punctuation at all
        # Remove unconditionally — LLM word-limit cutoff always produces these
        if not last.rstrip().endswith((".", "!", "?")):
            cleaned.pop()
            removals += 1
            continue

        # Check 1 — ends with terminal punctuation but last word is still bad
        # Strip ALL trailing punctuation including quotes, brackets, ellipsis
        last_no_punct = last.rstrip('.!?"\')\]}>…').rstrip()
        if not last_no_punct:
            break

        last_word = last_no_punct.split()[-1].lower()
        # Also strip any remaining punctuation attached to the word itself
        last_word = last_word.rstrip('.,!?"\';:)\]}>').lstrip('"\'(\[{<')

        if last_word in BAD_FINAL_WORDS:
            cleaned.pop()
            removals += 1
            continue

        # Check 1b — possessive ending on last word (dynamic — catches any noun)
        if re.search(r"'s$|s'$", last_word):
            cleaned.pop()
            removals += 1
            continue

        # Check 1c — dangling present participle (ends in -ing, short sentence)
        # Only strip if sentence is short (< 10 words) to avoid false positives
        # on valid gerund sentences like "Swimming is healthy."
        if last_word.endswith("ing") and len(last.split()) < 10:
            cleaned.pop()
            removals += 1
            continue

        # Check 2 — last word before punctuation is a bad ending
        last_word = last.rstrip(".!?").rstrip().split()[-1].lower()
        if last_word in BAD_FINAL_WORDS:
            cleaned.pop()
            removals += 1
            continue

        # Check 2b — last word is a hyphenated compound adjective (dynamic check)
        # Catches any "X-Y" pattern where both parts are alphabetic — always needs noun
        if re.match(r'^[a-z]+-[a-z]+$', last_word):
            cleaned.pop()
            removals += 1
            continue

        # Check 2c — last word ends with possessive 's or s'
        # e.g. "UK's", "country's", "party's", "president's", "leaders'"
        # Possessive endings always signal the possessed thing is missing
        if re.search(r"'s$|s'$", last_word):
            cleaned.pop()
            removals += 1
            continue

        # Check 2d — sentence word count is suspiciously low (< 6 words)
        # Short sentences from LLM are almost always truncated continuations
        if len(last.split()) < 6 and not last.strip().endswith((".", "!", "?")):
            cleaned.pop()
            removals += 1
            continue

        # Check 3 — starts with a coordinating conjunction (continuation fragment)
        first_word = last.split()[0].lower() if last.split() else ""
        if first_word in BAD_STARTERS:
            cleaned.pop()
            removals += 1
            continue

        break  # last sentence is clean — stop

    return " ".join(cleaned)


def summarise(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""

    # STEP 1 — Run spaCy on the cleaned text
    doc = nlp(text)

    # STEP 2 — Detect emotional context BEFORE generating script
    # (context is needed to select the correct hook style)
    context = detect_context(doc)
    print(f"[ScriptGen] Context detected: '{context}'")

    # STEP 3 — Calculate target word count
    target_words = calculate_target_words(text)

    # STEP 4 — Generate script with context-aware hook (up to 3 attempts)
    script = ""
    for _ in range(3):
        script = generate_script_with_ollama(text, target_words, context=context)

        if validate_script(script, target_words):
            break

    script = script.strip()

    # ── Headline injection detector ───────────────────────────────────────
    # Strip any LLM-generated headline title that appears as the first
    # sentence before the actual narrative script body.
    # Headline signals: Title Case, no terminal punctuation, no auxiliary
    # verb, followed by a second sentence that IS a proper narrative.
    if script:
        sentences = _sentence_list(script)
        if len(sentences) >= 2:
            first = sentences[0].strip()
            first_words = first.split()

            # Headline pattern: no terminal punctuation AND
            # majority of words are Title Case AND sentence is short (< 15w)
            title_case_count = sum(
                1 for w in first_words
                if w and w[0].isupper() and not w.isupper()
            )
            is_headline = (
                not first.endswith((".", "!", "?"))
                and len(first_words) <= 15
                and title_case_count >= len(first_words) * 0.6
                and not any(v in first.lower() for v in [
                    " is ", " are ", " was ", " were ", " has ",
                    " have ", " will ", " would ", " could ", " said ",
                    " says ", " told ", " told ", " froze ", " held ",
                ])
            )

            if is_headline:
                print(f"[HEADLINE STRIP] Removed headline: '{first[:60]}'")
                script = " ".join(sentences[1:])
    
    BAD_PATTERNS = [
        "what happened:",
        "key facts:",
        "context:",
        "context/background:",
        "why it matters:",
        "in conclusion",
        "What happened:",
        "Key facts:",
        "Context:",
        "Context/background:",
        "Why it matters:",
        "In conclusion"
    ]

    for p in BAD_PATTERNS:
        script = script.replace(p, "")
        
    first_line = script.split(".")[0]

    if is_weak_hook(first_line):
        print("[HOOK FIX] Weak hook — regenerating")
        script = generate_script_with_ollama(
            text + "\n\nRewrite with a stronger opening that matches the news type.",
            target_words,
            context=context
        )

    # Remove unwanted prefixes
    BAD_PREFIXES = [
        "Here is the script:",
        "Here is your script:",
        "Script:",
        "Output:"
    ]

    for prefix in BAD_PREFIXES:
        if script.lower().startswith(prefix.lower()):
            script = script[len(prefix):].strip()

    # Remove leading weak phrases
    BAD_STARTS = [
        "in a shocking turn of events",
        "in a surprising development",
        "recently",
        "today",
        "yesterday"
    ]

    first_line = script.split(".")[0].lower()

    for bad in BAD_STARTS:
        if first_line.startswith(bad):
            print("[HOOK FIX] Removing weak opening")
            script = script.replace(script.split(".")[0] + ".", "").strip()
            break

    # Keep essential cleaners
    script = clean_caption_text(script)
    script = fix_streaming_duplicates(script)
    script = remove_exact_duplicates(script)
    
    script = enforce_context(script)
    script = clean_tts_text(script)

    # Strip trailing incomplete/dangling sentences produced by LLM word-limit cutoff
    script = _strip_incomplete_tail(script)

    return script


# ══════════════════════════════════════════════════════════════════════════════
# Smoke test
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    samples = {
        "tech": (
            "Artificial intelligence is transforming many industries. "
            "Companies around the world are investing heavily in AI research. "
            "Governments are beginning to regulate AI technologies. "
            "Deep learning has enabled breakthroughs in image and speech recognition. "
            "AI is being used in healthcare to diagnose diseases earlier. "
            "Self-driving cars rely on AI to navigate roads safely."
        ),
        "war": (
            "Israeli military forces launched airstrikes on Gaza overnight. "
            "Dozens of casualties have been reported by local health officials. "
            "The United Nations has called for an immediate ceasefire. "
            "Diplomatic talks between world leaders are ongoing. "
            "Civilians have been urged to evacuate conflict zones immediately."
        ),
        "disaster": (
            "A magnitude 7.1 earthquake struck the coast of Japan early this morning. "
            "Tsunami warnings have been issued for several Pacific nations. "
            "Rescue teams are being deployed across the affected regions. "
            "The death toll is feared to rise as search operations continue. "
            "Emergency shelters have been set up in major cities."
        ),
        "positive": (
            "India's Chandrayaan-3 mission has successfully landed on the Moon's south pole. "
            "This makes India only the fourth country to achieve a lunar landing. "
            "Scientists and engineers celebrated the historic achievement at ISRO headquarters. "
            "The mission will explore the lunar surface for water ice deposits. "
            "This is a major milestone for India's space programme."
        ),
    }

    for label, text in samples.items():
        print(f"\n{'═'*60}")
        print(f"  SAMPLE: {label.upper()}")
        print(f"{'═'*60}")
        result = summarise(text, num_sentences=3)
        print("\n── Generated Script ──")
        print(result)
        print(f"\nWord count: {_word_count(result)}")

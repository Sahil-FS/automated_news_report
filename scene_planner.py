# modules/scene_planner.py — Split script into scenes and extract keywords

import sys
import re
import spacy

# PHASE 4: Environment lock
if ".venv" not in sys.executable:
    print("❌ ERROR: Not running inside .venv")
    print(f"Current: {sys.executable}")
    print("Run using: .venv\\Scripts\\python.exe main.py")
    exit(1)

def clean_caption_text(text):
    text = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\b\d+[A-Z]\b', '', text)
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

MIN_WORDS = 8
MAX_WORDS = 12
IDEAL_WORDS = 10

# Scene duration bounds
MIN_SCENE_DURATION = 4
MAX_SCENE_DURATION = 6

def extract_context_entities(script: str) -> dict:
    """
    Use spaCy NER to extract named entities from the full script.
    Returns a rich entity dict used by image_fetcher for query anchoring.

    Entity types extracted:
      PERSON  → named individuals (Trump, Biden, Zelensky)
      GPE     → geo-political entities (Washington DC, Ukraine, London)
      ORG     → organisations (FBI, White House, NATO, BBC)
      EVENT   → named events (Correspondents Dinner, G7 Summit)
    """
    doc = nlp(script)

    persons   = []
    locations = []
    orgs      = []
    events    = []

    for ent in doc.ents:
        if ent.label_ == "PERSON":
            tokens = ent.text.split()
            # Discard if: more than 3 tokens, fewer than 2 tokens,
            # or contains a disqualifier word
            PERSON_DISQUALIFIERS = {
                "set", "amid", "amidst", "tensions", "address", "congress",
                "senate", "parliament", "despite", "following", "marking",
                "including", "after", "before", "during", "against", "between",
                "among", "says", "said", "told", "warns", "claims", "urges",
                "calls", "faces", "holds", "takes", "makes", "gives", "puts",
            }
            if len(tokens) > 3 or len(tokens) < 2:
                continue
            if any(t.lower() in PERSON_DISQUALIFIERS for t in tokens):
                continue
            if ent.text not in persons:
                persons.append(ent.text)

        elif ent.label_ == "GPE" and ent.text not in locations:
            locations.append(ent.text)
        elif ent.label_ == "ORG" and ent.text not in orgs:
            orgs.append(ent.text)
        elif ent.label_ == "EVENT" and ent.text not in events:
            events.append(ent.text)

    # Build a concise primary query anchor (most specific available)
    primary_person   = persons[0]   if persons   else ""
    primary_location = locations[0] if locations else ""
    primary_org      = orgs[0]      if orgs      else ""

    # Country context — used to anchor security/politics images to correct nation
    # e.g. "Washington DC" → "United States", "London" → "United Kingdom"
    COUNTRY_MAP = {
        "washington": "United States", "washington dc": "United States",
        "new york": "United States", "los angeles": "United States",
        "london": "United Kingdom", "beijing": "China",
        "moscow": "Russia", "kyiv": "Ukraine", "kiev": "Ukraine",
        "paris": "France", "berlin": "Germany", "tokyo": "Japan",
        "new delhi": "India", "delhi": "India", "mumbai": "India",
        "islamabad": "Pakistan", "kabul": "Afghanistan",
        "tehran": "Iran", "riyadh": "Saudi Arabia",
        "jerusalem": "Israel", "tel aviv": "Israel",
    }
    country_context = ""
    if primary_location:
        country_context = COUNTRY_MAP.get(primary_location.lower(), "")

    # ── ORG-to-country fallback ───────────────────────────────────────────
    # When no GPE was found, derive country from known org/institution names.
    if not country_context:
        ORG_COUNTRY_MAP = {
            # United Kingdom
            "labour":          "United Kingdom",
            "conservative":    "United Kingdom",
            "lib dem":         "United Kingdom",
            "liberal democrat":"United Kingdom",
            "snp":             "United Kingdom",
            "parliament":      "United Kingdom",
            "westminster":     "United Kingdom",
            "downing":         "United Kingdom",
            "commons":         "United Kingdom",
            "lords":           "United Kingdom",
            "bbc":             "United Kingdom",
            "nhs":             "United Kingdom",
            "10 downing":      "United Kingdom",
            # United States
            "democrat":        "United States",
            "republican":      "United States",
            "senate":          "United States",
            "congress":        "United States",
            "pentagon":        "United States",
            "white house":     "United States",
            "fbi":             "United States",
            "cia":             "United States",
            "nsa":             "United States",
            "state department":"United States",
            # European
            "european union":  "Europe",
            "eu commission":   "Europe",
            "nato":            "Europe",
            "bundestag":       "Germany",
            "bundesrat":       "Germany",
            "élysée":          "France",
            "kremlin":         "Russia",
            # Other
            "knesset":         "Israel",
            "likud":           "Israel",
            "hamas":           "Gaza",
            "idf":             "Israel",
            "taliban":         "Afghanistan",
        }
        all_org_text = " ".join(orgs).lower()
        for org_key, country_val in ORG_COUNTRY_MAP.items():
            if org_key in all_org_text:
                country_context = country_val
                print(f"[NER] Country context derived from ORG '{org_key}': '{country_val}'")
                break

        # Last resort — derive from person names (e.g. "Keir Starmer" → UK)
        if not country_context:
            PERSON_COUNTRY_MAP = {
                "starmer":    "United Kingdom",
                "sunak":      "United Kingdom",
                "mandelson":  "United Kingdom",
                "farage":     "United Kingdom",
                "macron":     "France",
                "scholz":     "Germany",
                "putin":      "Russia",
                "zelensky":   "Ukraine",
                "netanyahu":  "Israel",
                "modi":       "India",
                "xi":         "China",
                "trudeau":    "Canada",
                "albanese":   "Australia",
            }
            all_person_text = " ".join(persons).lower()
            for person_key, country_val in PERSON_COUNTRY_MAP.items():
                if person_key in all_person_text:
                    country_context = country_val
                    print(f"[NER] Country context derived from PERSON '{person_key}': '{country_val}'")
                    break

    print(f"[NER] Persons: {persons[:3]}")
    print(f"[NER] Locations: {locations[:3]}")
    print(f"[NER] Orgs: {orgs[:3]}")
    print(f"[NER] Country context: '{country_context}'")

    return {
        # Primary (first/most prominent)
        "location":        primary_location,
        "person":          primary_person,
        "org":             primary_org,
        # Full lists for query variation
        "all_persons":     persons[:3],
        "all_locations":   locations[:3],
        "all_orgs":        orgs[:3],
        "all_events":      events[:2],
        # Derived context
        "country_context": country_context,
    }

try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    raise OSError(
        "[ScenePlanner] spaCy model not found. Run:\n"
        "  python -m spacy download en_core_web_sm"
    )


def _detect_scene_type(sentence: str) -> str:
    """
    Classify sentence into scene type based on whole-word keyword matching.
    Uses regex word boundaries to prevent substring collisions.
    Returns one of: "politics", "war", "technology", "business", "disaster", "general"
    """
    import re

    def _match(keywords: list, text: str) -> bool:
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, text):
                return True
        return False

    s = sentence.lower()

    if _match([
        "government", "president", "minister", "election", "policy",
        "parliament", "legislation", "congress", "senate", "cabinet", "senator"
    ], s):
        return "politics"

    if _match([
        "war", "conflict", "military", "attack", "ceasefire",
        "artillery", "troops", "soldiers", "combat", "gunshot",
        "gunfire", "shooting", "shooter", "assailant", "weapon",
        "armed", "hostage", "bomb", "explosion", "airstrike", "invasion"
    ], s):
        return "war"

    if _match([
        "technology", "artificial intelligence", "software", "startup",
        "innovation", "algorithm", "neural network", "machine learning",
        "robot", "cybersecurity", "semiconductor", "drone technology",
        "digital", "internet", "app store", "smartphone"
    ], s):
        return "technology"

    if _match([
        "market", "economy", "finance", "stock", "business", "trade",
        "commerce", "sales", "investment", "revenue", "profit", "gdp",
        "inflation", "recession", "unemployment", "exports", "imports"
    ], s):
        return "business"

    if _match([
        "flood", "earthquake", "climate", "disaster", "storm",
        "hurricane", "tsunami", "wildfire", "drought", "emergency",
        "tornado", "avalanche", "landslide", "famine", "evacuation"
    ], s):
        return "disaster"

    return "general"

def detect_visual_context(text: str) -> str:
    import re

    def _match(keywords: list, text: str) -> bool:
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, text):
                return True
        return False

    t = text.lower()

    if _match(["war", "conflict", "battle", "military", "attack",
               "shooting", "shooter", "gunfire", "assailant",
               "armed", "weapon", "airstrike", "bomb", "troops"], t):
        return "war"

    if _match(["president", "king", "government", "minister",
               "election", "senator", "parliament", "congress"], t):
        return "politics"

    if _match(["technology", "artificial intelligence", "startup",
               "cybersecurity", "software", "algorithm", "robot"], t):
        return "technology"

    if _match(["people", "family", "community", "victim",
               "witness", "survivor", "crowd", "civilians"], t):
        return "people"

    return "general"


def extract_keywords(text: str) -> str:
    import re

    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())

    STOPWORDS = {
        "the","is","are","was","were","this","that","and","but","for","with",
        "have","has","had","you","your","his","her","its","our","their",
        "from","into","over","under","again","further","then","once",
        "here","there","why","how","all","any","both","each","few","more",
        "most","other","some","such","only","own","same","than","too","very"
    }

    words = [w for w in words if w not in STOPWORDS]

    # pick top meaningful words
    keywords = words[:4]

    return " ".join(keywords)


def _strict_chunk_sentence(sentence: str) -> list:
    """
    Split a long sentence into readable caption-sized chunks.

    Strategy (in order):
      1. If sentence is 14 words or fewer — return as-is (no split needed).
      2. Try to split at a natural conjunction or pause word in the middle
         third of the sentence. Both halves must be >= MIN_WORDS (8 words).
      3. If no clean split point found, split at the midpoint ONLY if both
         halves are >= MIN_WORDS.
      4. If nothing works — return the sentence as-is (never break grammar
         just to hit a word count target).

    Never produces a chunk that ends with: a, an, the, at, in, on, of,
    for, to, by, with, and, but, or, as, that, which, when, while.
    """
    words = sentence.split()

    # Rule 1 — short enough, no split needed
    if len(words) <= 14:
        return [sentence]

    # Words that must NOT appear at the end of a chunk
    BAD_ENDINGS = {
        "a", "an", "the", "at", "in", "on", "of", "for",
        "to", "by", "with", "and", "but", "or", "as",
        "that", "which", "when", "while", "after", "before",
        "from", "into", "onto", "upon", "over", "under",
        "than", "then", "its", "their", "his", "her", "gun",
        "this", "these", "those", "been", "was", "were",
    }

    # Conjunctions / natural pause points to split AT (word goes to second chunk)
    SPLIT_TRIGGERS = [
        "prompting", "forcing", "leaving", "causing", "making",
        "sending", "resulting", "sparking", "triggering",
        "as", "when", "while", "before", "after", "although",
        "because", "despite", "following", "amid",
        "and", "but",
    ]

    # Rule 2 — look for a natural split in the middle third
    lo = max(MIN_WORDS, len(words) // 3)
    hi = min(len(words) - MIN_WORDS, (len(words) * 2) // 3)

    best_split = None
    for i in range(lo, hi + 1):
        word_lower = words[i].lower().rstrip(",.;:")
        if word_lower in SPLIT_TRIGGERS:
            left = words[:i]
            right = words[i:]
            # Validate: neither chunk ends with a bad word
            if (left[-1].lower().rstrip(",.;:") not in BAD_ENDINGS
                    and len(left) >= MIN_WORDS
                    and len(right) >= MIN_WORDS):
                best_split = i
                break  # take the first clean split point

    if best_split is not None:
        left_text  = " ".join(words[:best_split])
        right_text = " ".join(words[best_split:])
        return [left_text, right_text]

    # Rule 2b — comma-based split in the middle third
    # Looks for a word ending with "," in range [lo, hi] as a natural clause boundary.
    # Splits AFTER the comma word so the comma stays with the left chunk.
    # Both halves must be >= MIN_WORDS and the left must not end on a BAD_ENDING.
    for i in range(lo, hi + 1):
        word_no_punct = words[i].rstrip(",.;:")
        prev_word = words[i - 1] if i > 0 else ""
        if prev_word.endswith(","):
            left  = words[:i]
            right = words[i:]
            if (len(left) >= MIN_WORDS
                    and len(right) >= MIN_WORDS
                    and left[-1].lower().rstrip(",.;:") not in BAD_ENDINGS):
                left_text  = " ".join(left)
                right_text = " ".join(right)
                return [left_text, right_text]

    # Rule 3 — midpoint fallback with proper noun boundary guard
    mid = len(words) // 2

    # Words that signal a proper noun is CONTINUING across the split boundary.
    # If the word AFTER the candidate split starts with uppercase and the word
    # BEFORE ends without punctuation, it's likely a proper noun phrase — skip it.
    LOCATION_CONTINUATIONS = {
        "hilton", "hotel", "house", "palace", "building", "tower",
        "street", "avenue", "boulevard", "square", "park", "station",
        "airport", "university", "hospital", "church", "temple",
        "club", "center", "centre", "complex", "district", "county",
        "association", "correspondents", "department", "ministry",
        "committee", "foundation", "institute", "organization",
    }

    for offset in range(0, min(6, len(words) - mid)):
        candidate = mid + offset
        if candidate >= len(words) or candidate == 0:
            continue

        left_last  = words[candidate - 1].lower().rstrip(",.;:")
        right_first = words[candidate].lower().rstrip(",.;:")

        # Guard 1 — left chunk must not end on a BAD_ENDING word
        if left_last in BAD_ENDINGS:
            continue

        # Guard 2 — right chunk must not START with an uppercase proper noun
        # continuation (e.g. "Hilton", "Association", "Club")
        if right_first in LOCATION_CONTINUATIONS:
            continue

        # Guard 3 — if next word starts with uppercase AND previous word has
        # no terminal punctuation → likely same proper noun phrase, skip
        if (words[candidate][0].isupper()
                and not words[candidate - 1].rstrip().endswith((",", ".", ";", ":"))
                and right_first not in ("the", "a", "an", "he", "she", "they",
                                        "it", "his", "her", "their", "its",
                                        "this", "that", "these", "those")):
            continue

        # All guards passed — safe to split here
        if (len(words[:candidate]) >= MIN_WORDS
                and len(words[candidate:]) >= MIN_WORDS):
            return [
                " ".join(words[:candidate]),
                " ".join(words[candidate:])
            ]

    # Rule 4 — no clean split found — return whole sentence unchanged
    return [sentence]


def calculate_scene_count(script: str) -> int:
    """
    Dynamically compute scene count based on estimated speech duration.
    Targets ~5 seconds per scene. Clamps to 6–12 scenes.
    """
    words = len(script.split())
    est_duration = words / 2.5      # seconds at 2.5 words/sec

    scene_count = int(est_duration / 5)

    return max(6, min(scene_count, 12))


def merge_short_scenes(scenes):
    merged = []
    buffer = ""

    for scene in scenes:
        text = scene["text"]
        words = len(text.split())

        if words < MIN_WORDS:
            buffer += " " + text
        else:
            if buffer:
                text = buffer.strip() + " " + text
                buffer = ""

            merged.append({**scene, "text": text})

    if buffer and merged:
        merged[-1]["text"] += " " + buffer.strip()

    return merged

def safe_split(words):
    if len(words) > 12:
        mid = len(words) // 2

        # avoid splitting mid-phrase
        return [
            " ".join(words[:mid]),
            " ".join(words[mid:])
        ]
    return [" ".join(words)]

def _clean_scene_text(text: str) -> str:
    """
    Remove common leading and trailing artifacts from scene text:
      - Leading commas/periods:           ", where..." → "where..."
      - Lone abbreviation + comma:        "DC , where" → "where..."
      - Leading coordinating conjunctions:"and whether" → "whether"
      - Leading subordinating conjunctions that signal fragment:
          "Before security..." → kept only if >= MIN_WORDS after clean
          "After the event..." → kept (has enough content)
      - Trailing commas:                  "...for cover," → "...for cover"
      - Dangling punctuation at start:    ". Next..." → "Next..."
    """
    # Strip leading/trailing whitespace
    text = text.strip()

    # Remove lone 1–5 uppercase letter token followed by comma (e.g. "DC ,")
    text = re.sub(r'^[A-Z]{1,5}\s*,\s*', '', text)

    # Remove leading comma, period, semicolon artifacts
    text = re.sub(r'^[,.\s;:]+', '', text)

    # Remove leading coordinating conjunctions
    COORD_CONJUNCTIONS = r'^(and|or|but|so|yet|nor|both|either)\s+'
    text = re.sub(COORD_CONJUNCTIONS, '', text, flags=re.IGNORECASE)

    # Remove leading prepositions that are resplit artifacts.
    # Pattern: single preposition followed by uppercase word (new sentence join)
    # e.g. "Of Which...", "To A stance...", "From The..."
    # Only strip when the preposition is followed by an uppercase word
    # (signals a raw sentence boundary join, not a valid prepositional phrase)
    LEADING_PREP_ARTIFACT = (
        r'^(of|to|from|with|by|at|in|on|for|into|onto|upon|'
        r'over|under|through|about|after|before|during|'
        r'among|between|against|along|across|behind|toward|'
        r'within|without|beyond)\s+(?=[A-Z])'
    )
    text = re.sub(LEADING_PREP_ARTIFACT, '', text, flags=re.IGNORECASE)

    # Also clean internal "of The", "to A", "of A" joins — where a lowercase
    # preposition/article is followed by an uppercase word mid-sentence.
    # This fixes: "Marking the start of The British monarch..."
    # Pattern: lowercase preposition + space + uppercase article/word
    text = re.sub(r'\bof The\b', 'of the', text)
    text = re.sub(r'\bto A\b', 'to a', text)
    text = re.sub(r'\bto The\b', 'to the', text)
    text = re.sub(r'\bof A\b', 'of a', text)
    text = re.sub(r'\bin The\b', 'in the', text)
    text = re.sub(r'\bby The\b', 'by the', text)
    text = re.sub(r'\bfor The\b', 'for the', text)

    # Remove leading subordinating conjunctions ONLY when the scene is short
    # (< 8 words after strip) — these are definite orphan fragments.
    # Keep them if the scene is long enough to stand alone grammatically.
    SUBORD_CONJUNCTIONS = (
        r'^(before|after|while|although|though|since|because|'
        r'despite|following|amid|amidst|regarding|concerning|'
        r'given|provided|unless|until|whenever|wherever|'
        r'as long as|even though|even if)\s+'
    )
    stripped_test = re.sub(SUBORD_CONJUNCTIONS, '', text, flags=re.IGNORECASE)
    if len(stripped_test.split()) < MIN_WORDS:
        # Fragment too short after removing subordinator → strip it
        text = stripped_test
    # else: keep the subordinating conjunction — scene is self-contained enough

    # Remove trailing comma, semicolon (sentence not complete punctuation)
    text = text.rstrip(" ,;")

    # Final strip
    text = text.strip()

    # Capitalise first letter
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    return text


def plan_scenes(script: str) -> list[dict]:
    """
    Split the summarised script into one scene per sentence and attach a
    keyword for image lookup and scene type classification.
    
    PHASE 2 FIX: Sentences longer than MAX_WORDS_PER_SCENE are split
    at natural conjunctions to keep captions readable.

    Returns a list of dicts:
        [{"text": str, "keyword": str, "type": str}, ...]
    """
    script = clean_caption_text(script)
    context = extract_context_entities(script)

    # Step 1 — collapse spaced single-letter abbreviations like "D. C." → "DC"
    # Handles any pattern: single capital letter + dot + space + single capital letter + dot
    protected = re.sub(r'\b([A-Z])\.\s([A-Z])\.', r'\1\2', script)

    # Step 2 — also collapse triple-letter spaced abbreviations like "U. A. E." → "UAE"
    protected = re.sub(r'\b([A-Z])\.\s([A-Z])\.\s([A-Z])\.', r'\1\2\3', protected)

    # Step 3 — handle known word abbreviations (titles, common terms)
    ABBREV_MAP = {
        "D.C.": "DC",
        "U.S.": "US",
        "U.K.": "UK",
        "U.N.": "UN",
        "E.U.": "EU",
        "U.A.E.": "UAE",
        "Dr.": "Dr",
        "Mr.": "Mr",
        "Mrs.": "Mrs",
        "Ms.": "Ms",
        "Jr.": "Jr",
        "Sr.": "Sr",
        "St.": "St",
        "vs.": "vs",
        "approx.": "approx",
        "govt.": "govt",
        "dept.": "dept",
        "No.": "No",
        "Pres.": "President",
        "Gen.": "General",
        "Lt.": "Lieutenant",
        "Sgt.": "Sergeant",
        "Rep.": "Representative",
        "Sen.": "Senator",
        "Ave.": "Avenue",
        "Blvd.": "Boulevard",
    }
    for abbrev, safe in ABBREV_MAP.items():
        protected = protected.replace(abbrev, safe)

    # Split on sentence-ending punctuation, keeping the punctuation
    raw_sentences = re.split(r"(?<=[.!?])\s+", protected.strip())
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    # Filter out fragments shorter than MIN_WORDS (8 words).
    # Fragments under 8 words are either abbreviation artifacts or sentence stubs
    # that will cause merge_short_scenes() to create oversized bloated scenes.
    sentences = [s for s in sentences if len(s.split()) >= MIN_WORDS]

    scenes = []
    for i, sent in enumerate(sentences):
        # PHASE 2 FIX: Split long sentences
        sentence_fragments = _strict_chunk_sentence(sent)
        
        for j, fragment in enumerate(sentence_fragments):
            if len(fragment.split()) > MAX_WORDS:
                fragment = " ".join(fragment.split()[:MAX_WORDS])
            
            scene_type = _detect_scene_type(fragment)
            scene_context = detect_visual_context(fragment)

            scene_keyword = extract_keywords(fragment)
            if not scene_keyword:
                scene_keyword = "breaking news event"
            
            # PART 3 — SMART IMAGE QUERY
            if context["location"] or context["person"]:
                scene_keyword = f"{context['location']} {context['person']} {scene_keyword} news event realistic"
            else:
                scene_keyword = f"{scene_keyword} breaking news event realistic photo"

            if i == 0 and j == 0:
                scene_keyword = f"{scene_keyword} breaking news dramatic real photo"

            cleaned_fragment = _clean_scene_text(fragment)
            if not cleaned_fragment or len(cleaned_fragment.split()) < 3:
                continue  # skip if cleaning left nothing usable

            scenes.append({
                "text": cleaned_fragment,
                "keyword": scene_keyword,
                "type": scene_type,
                "context": scene_context,
                "entities": context
            })
            print(f"[ScenePlanner] words={len(fragment.split())} | type='{scene_type}' | {fragment}")

    scenes = merge_short_scenes(scenes)

    # ── Scene-level incomplete sentence detector ──────────────────────────
    # Scan every scene. If a scene ends on a BAD_FINAL_WORD, merge it
    # forward into the next scene. This catches mid-script LLM truncations
    # that slip through _strip_incomplete_tail() (which only cleans the end).
    SCENE_BAD_FINALS = {
        # prepositions
        "at", "in", "on", "of", "for", "to", "by", "with", "from",
        "into", "onto", "over", "under", "than", "about", "through",
        "between", "among", "against", "during", "before", "after",
        "within", "without", "along", "across", "behind", "toward",
        # articles
        "a", "an", "the",
        # conjunctions / relative pronouns
        "and", "or", "but", "so", "yet", "nor", "as", "that", "which",
        "when", "while", "where", "whether", "what", "who", "if",
        # auxiliary verbs
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "will", "would", "should", "could",
        "may", "might", "shall", "must", "can", "do", "did", "does",
        # known dangling words seen in logs
        "no", "gun", "its", "their", "his", "her", "our", "your",
        "this", "these", "those", "such", "any", "some", "not",
        # hyphenated adjective fragments
        "high-profile", "well-known", "long-term", "short-term",
        "large-scale", "small-scale", "full-scale", "ever-present",
        "high-level", "top-level", "low-level",
    }

    def _scene_ends_incomplete(text: str) -> bool:
        """Return True if the scene text ends on a dangling/incomplete word."""
        clean = text.rstrip(".!?,;: ")
        if not clean:
            return False
        last_word = clean.split()[-1].lower()
        # Also flag if text has no terminal punctuation at all
        if not text.rstrip().endswith((".", "!", "?")):
            if last_word in SCENE_BAD_FINALS:
                return True
        return last_word in SCENE_BAD_FINALS

    fixed_scenes = []
    i = 0
    while i < len(scenes):
        scene = scenes[i]
        if _scene_ends_incomplete(scene["text"]) and i + 1 < len(scenes):
            # Merge this scene forward into the next
            next_scene = scenes[i + 1]
            merged_text = scene["text"].rstrip() + " " + next_scene["text"].lstrip()
            # Use the type of whichever scene has more specific typing
            merged_type = (scene["type"] if scene["type"] != "general"
                           else next_scene["type"])
            merged = {
                **next_scene,
                "text":    merged_text,
                "type":    merged_type,
                "keyword": scene["keyword"],  # keep first scene keyword
            }
            print(f"[MERGE] Incomplete scene {i} merged forward: '{scene['text'][-30:]}'")

            # ── Post-merge re-split ───────────────────────────────────────
            # If the merged scene is too long (> 14 words), pass it back
            # through _strict_chunk_sentence() to get clean sub-scenes.
            merged_words = merged["text"].split()
            if len(merged_words) > 14:
                print(f"[RESPLIT] Merged scene too long ({len(merged_words)} words) — resplitting")
                sub_chunks = _strict_chunk_sentence(merged["text"])
                if len(sub_chunks) > 1:
                    # Filter and rescue short sub-chunks — never discard content
                    valid_chunks   = []
                    rescued_text   = ""

                    for chunk in sub_chunks:
                        chunk = chunk.strip()
                        if not chunk:
                            continue
                        if len(chunk.split()) >= MIN_WORDS:
                            # Long enough — flush any rescued text first
                            if rescued_text:
                                chunk = rescued_text.strip() + " " + chunk
                                rescued_text = ""
                            valid_chunks.append(chunk)
                        else:
                            # Too short — rescue by merging into previous valid chunk
                            if valid_chunks:
                                valid_chunks[-1] = valid_chunks[-1].rstrip() + " " + chunk
                                print(f"[RESPLIT] Rescued short chunk into previous: '{chunk[:30]}'")
                            else:
                                # No previous chunk yet — buffer for next chunk (forward merge)
                                rescued_text += " " + chunk

                # Flush any remaining rescued text
                if rescued_text.strip():
                    if valid_chunks:
                        # Append to last valid chunk
                        valid_chunks[-1] = (
                            valid_chunks[-1].rstrip() + " " + rescued_text.strip()
                        )
                        print(f"[RESPLIT] Flushed {len(rescued_text.split())}w rescued text into last chunk")
                    else:
                        # No valid chunks at all — keep full merged text trimmed to MAX_WORDS
                        fallback = " ".join(merged["text"].split()[:MAX_WORDS])
                        valid_chunks = [fallback]
                        print(f"[RESPLIT] No valid chunks — using trimmed merged text ({MAX_WORDS}w)")

                # Final pass — if any valid chunk is still under MIN_WORDS,
                # merge it with the adjacent chunk (forward if first, backward otherwise)
                if len(valid_chunks) > 1:
                    final_chunks = []
                    skip_next = False
                    for ci, ck in enumerate(valid_chunks):
                        if skip_next:
                            skip_next = False
                            continue
                        if len(ck.split()) < MIN_WORDS and ci + 1 < len(valid_chunks):
                            # Merge forward into next chunk
                            merged_ck = ck.rstrip() + " " + valid_chunks[ci + 1].lstrip()
                            final_chunks.append(merged_ck)
                            skip_next = True
                            print(f"[RESPLIT] Short chunk ({len(ck.split())}w) merged forward")
                        else:
                            final_chunks.append(ck)
                    valid_chunks = final_chunks

                    for chunk in valid_chunks:
                        chunk = _clean_scene_text(chunk.strip())
                        if chunk:
                            fixed_scenes.append({
                                **merged,
                                "text": chunk,
                            })
                            print(f"[RESPLIT] Sub-scene ({len(chunk.split())}w): '{chunk[:50]}'")
                else:
                    # Splitter returned whole sentence — force midpoint split
                    mid = len(merged_words) // 2
                    left_text  = _clean_scene_text(" ".join(merged_words[:mid]))
                    right_text = _clean_scene_text(" ".join(merged_words[mid:]))

                    if len(left_text.split()) >= MIN_WORDS and len(right_text.split()) >= MIN_WORDS:
                        fixed_scenes.append({**merged, "text": left_text})
                        fixed_scenes.append({**merged, "text": right_text})
                        print(f"[RESPLIT] Force-split at midpoint: {len(left_text.split())} + {len(right_text.split())} words")
                    else:
                        # One half too short — rescue by keeping full merged text
                        # but trim to MAX_WORDS to prevent overflow
                        safe_text = " ".join(merged_words[:MAX_WORDS])
                        safe_text = _clean_scene_text(safe_text)
                        fixed_scenes.append({**merged, "text": safe_text})
                        print(f"[RESPLIT] Force-split failed — kept trimmed ({MAX_WORDS}w) merged text")
            else:
                fixed_scenes.append(merged)

            i += 2  # skip both — merged into one
        else:
            fixed_scenes.append(scene)
            i += 1

    scenes = fixed_scenes
    print(f"[POST-MERGE] Scene count after incomplete fix: {len(scenes)}")
    
    # STORY FLOW LOCK — preserve valid scene types, only add flow_role metadata
    if len(scenes) >= 3:
        print("[STORY LOCK] Validating Hook-Context-Event flow")
        # Add flow_role as a separate key — do NOT overwrite the valid scene type
        scenes[0]["flow_role"] = "hook"
        scenes[1]["flow_role"] = "context"
        scenes[2]["flow_role"] = "event"
        for i in range(3, len(scenes)):
            scenes[i]["flow_role"] = "body"
    else:
        print("[STORY LOCK] Fewer than 3 scenes — skipping flow lock")
    
    # Only split scenes that are genuinely too long (over 15 words)
    # Never split scenes that would produce fragments under MIN_WORDS (8 words)
    if len(scenes) < 8:
        print("[EXPAND] Splitting long scenes to reach minimum scene count")
        new_scenes = []
        for s in scenes:
            words = s["text"].split()
            if len(words) > 15:
                mid = len(words) // 2
                if mid >= MIN_WORDS and (len(words) - mid) >= MIN_WORDS:
                    new_scenes.append({**s, "text": " ".join(words[:mid])})
                    new_scenes.append({**s, "text": " ".join(words[mid:])})
                else:
                    new_scenes.append(s)
            else:
                new_scenes.append(s)
        scenes = new_scenes
    else:
        print(f"[SCENE COUNT OK] {len(scenes)} scenes — no forced expansion needed")
    if len(scenes) > 14:
        scenes = scenes[:14]
        
    print(f"[SCENE COUNT]: {len(scenes)}")
    print(f"[SCRIPT WORD COUNT]: {len(script.split())}")
    
    return scenes


if __name__ == "__main__":
    sample_script = (
        "The United Nations warned about rising sea levels in coastal cities. "
        "Scientists published new research on renewable energy storage. "
        "NASA announced a new mission to explore the surface of Mars."
    )
    for s in plan_scenes(sample_script):
        print(s)

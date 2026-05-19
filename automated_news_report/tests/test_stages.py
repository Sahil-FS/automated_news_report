import sys
import os
import json

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from news_fetcher import fetch_latest_article
    from script_generator import summarise, _spacy_fallback_script, nlp
    from scene_planner import plan_scenes, extract_context_entities
    from voice_generator import generate_audio
    from image_fetcher import fetch_image
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

def test_news_fetcher():
    print("\n--- Testing News Fetcher ---")
    try:
        article = fetch_latest_article()
        print(f"DONE! Fetched: {article['title']}")
        return article
    except Exception as e:
        print(f"FAILED: {e}")
        return None

def test_script_generator(article_text):
    print("\n--- Testing Script Generator (Ollama) ---")
    try:
        script = summarise(article_text)
        if script:
            print(f"DONE! Script preview: {script[:100]}...")
            return script
        else:
            print("FAILED: Script is empty.")
            return None
    except Exception as e:
        print(f"FAILED: {e}")
        return None

def test_scene_planner(script):
    print("\n--- Testing Scene Planner ---")
    try:
        scenes = plan_scenes(script)
        if scenes:
            print(f"DONE! Planned {len(scenes)} scenes.")
            return scenes
        else:
            print("FAILED: No scenes generated.")
            return None
    except Exception as e:
        print(f"FAILED: {e}")
        return None


def test_scene_planner_tripoli():
    print("\n--- Testing Scene Planner Tripoli disambiguation ---")
    try:
        sample_script = "Tripoli was mentioned in the context of Lebanon after clashes in the capital."
        entities = extract_context_entities(sample_script)
        if entities.get("country_context") == "Lebanon":
            print("DONE! Tripoli disambiguated correctly.")
            return True
        print(f"FAILED: country_context={entities.get('country_context')}")
        return False
    except Exception as e:
        print(f"FAILED: {e}")
        return False


def test_script_generator_short_fallback():
    print("\n--- Testing Short-Article Script Fallback ---")
    try:
        sample_text = (
            "TITLE: Israel launches deadly air strikes on Gaza City apartment building\n\n"
            "SUMMARY: At least seven Palestinians were killed when Israeli air strikes hit a residential building and a civilian vehicle in Gaza City Friday night. "
            "Israel says it was targeting the head of the armed wing of Hamas in Gaza. Al Jazeera has not independently verified Israel's claims."
        )
        doc = nlp(sample_text)
        script = _spacy_fallback_script(sample_text, doc, "tense")
        if script and not script.lower().startswith("developing story"):
            print(f"DONE! Short fallback produced actual article content: {script[:100]}...")
            return script
        print(f"FAILED: Too-generic fallback script: {script!r}")
        return None
    except Exception as e:
        print(f"FAILED: {e}")
        return None


def test_voice_generator(text):
    print("\n--- Testing Voice Generator (Piper) ---")
    try:
        # Create a temp output dir if it doesn't exist
        os.makedirs("output/audio", exist_ok=True)
        path = generate_audio(text, 999)
        if path and os.path.exists(path):
            print(f"DONE! Audio saved to {path}")
            return path
        else:
            print("FAILED: Audio file not created.")
            return None
    except Exception as e:
        print(f"FAILED: {e}")
        return None

def run_all_tests():
    print("STARTING Pipeline Stage Tests")
    
    # Load mock article for consistency
    mock_path = os.path.join(os.path.dirname(__file__), "mock_article.json")
    with open(mock_path, "r") as f:
        mock_article = json.load(f)
    
    article_text = f"TITLE: {mock_article['title']}\n\nSUMMARY: {mock_article['summary']}"
    
    # 1. News Fetcher (Live test)
    test_news_fetcher()
    
    # 2. Script Generator
    script = test_script_generator(article_text)
    if not script: return
    
    # 3. Scene Planner
    scenes = test_scene_planner(script)
    if not scenes: return

    # 3.5 Tripoli disambiguation regression
    if not test_scene_planner_tripoli():
        return

    # 3.6 Short-article script fallback regression
    if not test_script_generator_short_fallback():
        return
    
    # 4. Voice Generator
    test_voice_generator(scenes[0]['text'])

if __name__ == "__main__":
    run_all_tests()

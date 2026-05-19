
import sys
import os

# Add the project directory to sys.path
sys.path.append(r'c:\Users\Darshan\Desktop\AI\automated_news_report\automated_news_report')

from image_fetcher import fetch_ai_generated

def test_gen():
    test_scene = {
        "text": "A futuristic newsroom with holographic displays and AI reporters.",
        "type": "technology",
        "entities": {
            "location": "Global",
            "country_context": "Worldwide"
        }
    }
    
    # Create scratch directory if it doesn't exist
    os.makedirs('scratch', exist_ok=True)
    dest_path = 'scratch/test_ai_image.jpg'
    
    print(f"Testing AI Image Generation for: {test_scene['text']}")
    success = fetch_ai_generated(test_scene, dest_path)
    
    if success:
        print(f"SUCCESS: Image saved to {dest_path}")
        print(f"File size: {os.path.getsize(dest_path)} bytes")
    else:
        print("FAILED: Could not generate AI image.")

if __name__ == "__main__":
    test_gen()

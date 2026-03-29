import os

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://localhost:8188")
TEMPLATES_DIR = os.environ.get("COMFYCLAUDE_TEMPLATES_DIR", "./templates")
OUTPUT_DIR = os.environ.get("COMFYCLAUDE_OUTPUT_DIR", "./output")

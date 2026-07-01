import os
import glob
from google import genai
from google.genai import types
from django.conf import settings
from .models import FileNode, ProcessedNote, NoteAsset
from .utils import SuperNoteUtility

class AIService:
    @staticmethod
    def process_note_with_ai(file_node_id):
        file_node = FileNode.objects.get(id=file_node_id)
        input_path = os.path.join(settings.SUPERNOTE_SOURCE, file_node.path)
        
        # 1. Export note pages to images
        temp_img_dir = os.path.join(settings.ARCHIVE_DIR, "temp_images", str(file_node.id))
        SuperNoteUtility.convert_note_to_images(input_path, temp_img_dir)
        
        # 2. Get list of images
        images = sorted(glob.glob(os.path.join(temp_img_dir, "*.png")))
        if not images:
            return None

        # 3. Initialize Gemini Client
        client = genai.Client(api_key=settings.GOOGLE_GENAI_API_KEY)
        
        # 4. Prepare Prompt
        prompt = """
        You are an expert at converting handwritten notes into structured Markdown.
        Below are images of several pages from a SuperNote e-ink tablet.
        
        Please convert the handwriting into a high-quality Markdown document following these rules:
        1. Convert all handwriting to accurate text.
        2. Identify any workflows, cycles, or Directed Acyclic Graphs (DAGs) and render them using raw Mermaid.js syntax blocks.
        3. Identify any tables and render them as standard Markdown tables.
        4. If a part of the image looks like a specific graph or detailed drawing that cannot be easily converted to text/mermaid, please indicate where it is with a placeholder like '![[asset_placeholder_X.png]]'.
        5. Output ONLY the final Markdown content.
        """
        
        # 5. Call Gemini 2.5 Flash
        # We'll send all images in a single multi-modal prompt
        content_parts = [prompt]
        for img_path in images:
            with open(img_path, "rb") as f:
                content_parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png"))
        
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=content_parts
            )
            
            markdown_content = response.text
            
            # 6. Save to Database
            processed_note, created = ProcessedNote.objects.update_or_create(
                file_node=file_node,
                defaults={
                    'markdown_content': markdown_content,
                    'last_processed_hash': file_node.hash
                }
            )
            
            return processed_note
            
        except Exception as e:
            print(f"AI Processing failed: {e}")
            return None

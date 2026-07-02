import os
import glob
import logging
from google import genai
from google.genai import types
from django.conf import settings
from .models import FileNode, ProcessedNote, NoteAsset
from .utils import SuperNoteUtility

logger = logging.getLogger(__name__)


class AIService:
    @staticmethod
    def process_note_with_ai(file_node_id):
        file_node = FileNode.objects.get(id=file_node_id)
        input_path = os.path.join(settings.SUPERNOTE_SOURCE, file_node.path)
        
        # 1. Export note pages to images
        temp_img_dir = os.path.join(settings.ARCHIVE_DIR, "temp_images", str(file_node.id))
        converted = SuperNoteUtility.convert_note_to_images(input_path, temp_img_dir)
        if not converted:
            raise RuntimeError(f'Failed to render note pages to PNG images: {input_path}')

        # 2. Get list of images
        images = sorted(glob.glob(os.path.join(temp_img_dir, "*.png")))
        if not images:
            raise RuntimeError(f'No page images were generated for: {input_path}')

        api_key = settings.GOOGLE_GENAI_API_KEY
        if not api_key:
            raise ValueError('Gemini API key is not configured.')

        # 3. Initialize Gemini Client
        client = genai.Client(api_key=api_key)
        
        # 4. Prepare Prompt
        prompt = """
        You are converting handwritten Supernote pages into structured Markdown.
        Preserve the document structure as faithfully as possible.

        Output rules:
        1. Reconstruct heading levels using Markdown headings (`#`, `##`, `###`) when the page layout indicates section structure.
        2. Preserve bullet lists, numbered lists, emphasis, and paragraph breaks.
        3. Convert tables into valid Markdown tables.
        4. Convert workflows, diagrams, dependency chains, and DAG-like sketches into Mermaid code blocks when a Mermaid representation is clearer than a literal image.
        5. If a sketch, chart, or figure is better represented as an image, keep it as an embedded asset reference using Obsidian-style embeds such as `![[figure_name.png]]` and annotate it with a short caption in Markdown.
        6. Do not add commentary outside the Markdown document.
        7. Output only the final Markdown content.
        """
        
        # 5. Call Gemini 2.5 Flash
        # We'll send all images in a single multi-modal prompt
        content_parts = [prompt]
        for img_path in images:
            with open(img_path, "rb") as f:
                content_parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png"))
        
        try:
            response = client.models.generate_content(
                model=settings.GOOGLE_GENAI_MODEL,
                contents=content_parts
            )

            markdown_content = response.text
            if not markdown_content:
                raise RuntimeError('Gemini returned an empty Markdown response.')

            # 6. Save to Database
            processed_note, created = ProcessedNote.objects.update_or_create(
                file_node=file_node,
                defaults={
                    'markdown_content': markdown_content,
                    'last_processed_hash': file_node.hash
                }
            )

            return processed_note

        except Exception:
            logger.exception('AI processing failed for file_node_id=%s', file_node_id)
            raise

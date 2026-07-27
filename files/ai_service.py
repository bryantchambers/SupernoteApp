import glob
import logging
import os

from django.conf import settings
from google import genai
from google.genai import types

from .gemini_models import (
    PREFERRED_GEMINI_MODELS,
    is_model_not_found_error,
    select_generate_content_model,
)
from .models import FileNode, NoteAsset, ProcessedNote
from .utils import SuperNoteUtility

logger = logging.getLogger(__name__)

DEFAULT_AI_PROMPT = """
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
""".strip()


class AIService:
    @staticmethod
    def _render_note_images(file_node_id):
        file_node = FileNode.objects.get(id=file_node_id)
        input_path = os.path.join(settings.SUPERNOTE_SOURCE, file_node.path)

        temp_img_dir = os.path.join(settings.ARCHIVE_DIR, "temp_images", str(file_node.id))
        converted = SuperNoteUtility.convert_note_to_images(input_path, temp_img_dir)
        if not converted:
            raise RuntimeError(f'Failed to render note pages to PNG images: {input_path}')

        images = sorted(glob.glob(os.path.join(temp_img_dir, "*.png")))
        if not images:
            raise RuntimeError(f'No page images were generated for: {input_path}')

        return file_node, input_path, images

    @staticmethod
    def _build_markdown_from_images(images, prompt: str) -> tuple[str, list[str], str]:
        api_key = settings.GOOGLE_GENAI_API_KEY
        if not api_key:
            raise ValueError('Gemini API key is not configured.')

        client = genai.Client(api_key=api_key)
        resolved_model, available_models, using_fallback = select_generate_content_model(
            client,
            settings.GOOGLE_GENAI_MODEL,
        )
        if using_fallback and resolved_model != settings.GOOGLE_GENAI_MODEL:
            logger.warning(
                "Configured Gemini model %s is not available; using %s instead.",
                settings.GOOGLE_GENAI_MODEL,
                resolved_model,
            )

        content_parts = [prompt]
        for img_path in images:
            with open(img_path, "rb") as f:
                content_parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png"))

        def _generate_markdown(model_name: str) -> str:
            response = client.models.generate_content(
                model=model_name,
                contents=content_parts,
            )
            return response.text or ""

        try:
            markdown_content = _generate_markdown(resolved_model)
        except Exception as exc:
            if not is_model_not_found_error(exc):
                raise

            markdown_content = ""
            attempted_models = [resolved_model]
            for model_name in PREFERRED_GEMINI_MODELS:
                if model_name in attempted_models:
                    continue
                try:
                    logger.warning(
                        "Retrying Gemini AI processing with fallback model %s after model lookup failure.",
                        model_name,
                    )
                    markdown_content = _generate_markdown(model_name)
                    resolved_model = model_name
                    break
                except Exception as retry_exc:
                    attempted_models.append(model_name)
                    if not is_model_not_found_error(retry_exc):
                        raise

            if not markdown_content:
                raise RuntimeError(
                    "Gemini model lookup failed and no fallback model succeeded. "
                    f"Configured model: {settings.GOOGLE_GENAI_MODEL}. "
                    f"Available generateContent models: {', '.join(available_models) or 'unknown'}"
                ) from exc

        if not markdown_content:
            raise RuntimeError('Gemini returned an empty Markdown response.')

        return markdown_content, available_models, resolved_model

    @staticmethod
    def process_note_with_ai(file_node_id, prompt: str = None):
        file_node, _, images = AIService._render_note_images(file_node_id)
        markdown_content, _, _ = AIService._build_markdown_from_images(images, prompt or DEFAULT_AI_PROMPT)

        processed_note, created = ProcessedNote.objects.update_or_create(
            file_node=file_node,
            defaults={
                'markdown_content': markdown_content,
                'last_processed_hash': file_node.hash,
            },
        )

        return processed_note

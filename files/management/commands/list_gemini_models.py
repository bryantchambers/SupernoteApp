from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from google import genai

from files.gemini_models import list_generate_content_models


class Command(BaseCommand):
    help = "List Gemini models that support generateContent for the configured API key."

    def handle(self, *args, **options):
        api_key = getattr(settings, "GOOGLE_GENAI_API_KEY", "")
        if not api_key:
            raise CommandError("GOOGLE_GENAI_API_KEY is not configured.")

        client = genai.Client(api_key=api_key)
        models = list_generate_content_models(client)

        if not models:
            self.stdout.write("No generateContent-capable Gemini models were returned.")
            return

        self.stdout.write("generateContent-capable Gemini models:")
        for model_name in models:
            self.stdout.write(f"- {model_name}")

from django.core.management.base import BaseCommand

from files.models import PeriodicalRecipe
from files.periodical_service import fetch_due_periodicals, fetch_periodical, seed_curated_recipes
from files.services import perform_supernote_sync


class Command(BaseCommand):
    help = "Fetch due periodicals with Calibre recipes and optionally push the Supernote mirror."

    def add_arguments(self, parser):
        parser.add_argument("--seed", action="store_true", help="Create/update the curated periodical recipe catalog.")
        parser.add_argument("--due", action="store_true", help="Fetch enabled recipes that are due.")
        parser.add_argument("--slug", help="Fetch one recipe slug immediately.")
        parser.add_argument("--push", action="store_true", help="Push the Supernote mirror after successful fetches.")

    def handle(self, *args, **options):
        if options["seed"]:
            recipes = seed_curated_recipes()
            self.stdout.write(self.style.SUCCESS(f"Seeded {len(recipes)} periodical recipes."))

        fetched = []
        errors = []
        if options["slug"]:
            recipe = PeriodicalRecipe.objects.get(slug=options["slug"])
            fetched.append(fetch_periodical(recipe))
        elif options["due"]:
            result = fetch_due_periodicals()
            fetched = result["fetched"]
            errors = result["errors"]
            self.stdout.write(f"Pruned {result['pruned']} expired issues.")

        if fetched and options["push"]:
            perform_supernote_sync(direction="push", rescan=True)

        for issue in fetched:
            self.stdout.write(self.style.SUCCESS(f"Fetched {issue.title}"))

        for recipe, message in errors:
            self.stderr.write(self.style.ERROR(f"{recipe.title}: {message}"))

        if not any([options["seed"], options["due"], options["slug"]]):
            self.stdout.write("No action requested. Use --seed, --due, or --slug.")

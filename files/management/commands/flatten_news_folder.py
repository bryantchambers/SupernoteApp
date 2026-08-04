from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from files.models import PeriodicalIssue
from files.periodical_service import flatten_news_device_folder


class Command(BaseCommand):
    help = "Flatten Document/News periodical folders into a single root directory and update issue records."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show the files that would be moved without changing anything.")

    def handle(self, *args, **options):
        root = Path(settings.PERIODICAL_DEVICE_DIR)
        result = flatten_news_device_folder(root=root, dry_run=options["dry_run"])
        moves = result["moves"]

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run: would flatten {len(moves)} files under {root}."))
            for old_path, new_path in sorted(moves.items()):
                self.stdout.write(f"{old_path} -> {new_path}")
            return

        updated = 0
        for issue in PeriodicalIssue.objects.exclude(device_path=""):
            device_path = Path(issue.device_path)
            full_path = device_path if device_path.is_absolute() else Path(settings.SUPERNOTE_SOURCE) / device_path
            new_full_path = None

            if str(full_path) in moves:
                new_full_path = Path(moves[str(full_path)])
            elif full_path.exists() and full_path.parent == root:
                new_full_path = full_path
            elif full_path.exists() and full_path.parent != root:
                new_full_path = root / full_path.name
                if new_full_path.exists() and str(full_path) not in moves:
                    new_full_path = full_path

            if new_full_path is None:
                continue

            issue.device_path = new_full_path.relative_to(settings.SUPERNOTE_SOURCE).as_posix()
            issue.is_on_device = True
            issue.status = "on_device"
            issue.status_message = "Flattened into the Supernote News folder."
            issue.save(update_fields=["device_path", "is_on_device", "status", "status_message", "updated_at"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Flattened {len(moves)} files and updated {updated} issue records."))

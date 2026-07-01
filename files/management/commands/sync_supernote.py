from django.core.management.base import BaseCommand
from files.services import perform_supernote_sync, SyncInProgressError


class Command(BaseCommand):
    help = "Sync the live SuperNote mirror with OneDrive and rescan the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--direction",
            choices=["pull", "push"],
            default="pull",
            help="Pull from OneDrive into the local mirror or push local changes back.",
        )
        parser.add_argument(
            "--no-rescan",
            action="store_true",
            help="Skip the database rescan after sync.",
        )

    def handle(self, *args, **options):
        try:
            result = perform_supernote_sync(
                direction=options["direction"],
                rescan=not options["no_rescan"],
            )
        except SyncInProgressError as exc:
            self.stderr.write(self.style.WARNING(str(exc)))
            return
        except Exception as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            raise

        self.stdout.write(self.style.SUCCESS(f"Sync completed: {result['state'].status}"))

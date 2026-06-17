from django.core.management.base import BaseCommand
from files.services import crawl_supernote_directory

class Command(BaseCommand):
    help = 'Crawls the SuperNote source directory and updates the database.'

    def handle(self, *args, **options):
        self.stdout.write('Starting crawl...')
        crawl_supernote_directory()
        self.stdout.write(self.style.SUCCESS('Crawl completed successfully.'))

import sqlite3
import os
import io
import logging
from PIL import Image

logger = logging.getLogger(__name__)

class AtelierUtility:
    """Utility for handling Supernote Atelier (.spd) files."""

    @staticmethod
    def extract_thumbnail(spd_path, output_path):
        """Extract the high-quality thumbnail from the .spd SQLite database."""
        try:
            conn = sqlite3.connect(spd_path)
            cur = conn.cursor()
            cur.execute("SELECT CAST(value AS BLOB) FROM config WHERE name='thumbnail'")
            row = cur.fetchone()
            conn.close()
            
            if row and row[0]:
                with open(output_path, 'wb') as f:
                    f.write(row[0])
                return True
        except Exception as e:
            logger.error(f"Failed to extract thumbnail from {spd_path}: {e}")
        return False

    @staticmethod
    def reconstruct_drawing(spd_path, output_path):
        """
        Attempt to reconstruct the full drawing by tiling surface images.
        NOTE: This is experimental as the exact grid logic for .spd is proprietary.
        We default to the thumbnail for now as it's highly reliable and 600x800.
        """
        return AtelierUtility.extract_thumbnail(spd_path, output_path)

    @staticmethod
    def is_atelier_file(file_path):
        """Check if a file is a valid Supernote Atelier file."""
        return file_path.lower().endswith('.spd')

import subprocess
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class SuperNoteUtility:
    @staticmethod
    def convert_note(input_path, output_path, output_type='pdf'):
        """
        Convert a .note file to another format using supernote-tool.
        output_type can be 'pdf', 'png', 'svg', or 'txt'.
        """
        if not os.path.exists(input_path):
            logger.error(f"Input file not found: {input_path}")
            return False

        command = [
            "mamba", "run", "-n", "SuperNoteTools",
            "supernote-tool", "convert",
            "-a", # Convert all pages
            "-t", output_type,
            input_path,
            output_path
        ]

        if output_type == 'pdf':
            command.extend(["--pdf-type", "vector"])

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logger.info(f"Successfully converted {input_path} to {output_path}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Error converting {input_path}: {e.stderr}")
            return False

    @staticmethod
    def convert_note_to_images(input_path, output_dir):
        """
        Convert all pages of a .note file to PNG images in the specified directory.
        """
        if not os.path.exists(input_path):
            return False
        
        os.makedirs(output_dir, exist_ok=True)
        
        command = [
            "mamba", "run", "-n", "SuperNoteTools",
            "supernote-tool", "convert",
            "-a", 
            "-t", "png",
            input_path,
            output_dir
        ]
        
        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

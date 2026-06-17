from django.db import models

class ProcessedNote(models.Model):
    file_node = models.OneToOneField('files.FileNode', on_delete=models.CASCADE, related_name='processed_note')
    markdown_content = models.TextField()
    last_processed_hash = models.CharField(max_length=64)
    processed_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Processed: {self.file_node.name}"

class NoteAsset(models.Model):
    processed_note = models.ForeignKey(ProcessedNote, on_delete=models.CASCADE, related_name='assets')
    original_filename = models.CharField(max_length=255)
    storage_path = models.CharField(max_length=1024)
    asset_type = models.CharField(max_length=50) # 'graph', 'table_img', etc.
    
    def __str__(self):
        return self.original_filename

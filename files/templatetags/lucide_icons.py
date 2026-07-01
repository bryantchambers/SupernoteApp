from django import template
from django.utils.safestring import mark_safe

register = template.Library()

ICON_PATHS = {
    'circle-dot': '<circle cx="12" cy="12" r="3"></circle><circle cx="12" cy="12" r="9"></circle>',
    'file-text': '<path d="M14 2H7a2 2 0 00-2 2v16a2 2 0 002 2h10a2 2 0 002-2V7z"></path><path d="M14 2v5h5"></path><path d="M9 13h6"></path><path d="M9 17h6"></path>',
    'palette': '<circle cx="12" cy="12" r="9"></circle><path d="M7.5 12a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z"></path><path d="M13 8a1 1 0 11-2 0 1 1 0 012 0z"></path><path d="M17 11a1 1 0 11-2 0 1 1 0 012 0z"></path><path d="M15 16a1 1 0 11-2 0 1 1 0 012 0z"></path>',
    'paintbrush': '<path d="M14.5 5.5l4 4"></path><path d="M5 19l4.5-4.5"></path><path d="M9.5 14.5l6-6"></path><path d="M14.5 5.5a2.12 2.12 0 113 3L9.5 16.5H6l1.5-3.5 7-7z"></path>',
    'book-open-text': '<path d="M12 7v13"></path><path d="M6 4h6a3 3 0 013 3v13a3 3 0 00-3-3H6a2 2 0 00-2 2V6a2 2 0 012-2z"></path><path d="M18 4h-6a3 3 0 00-3 3"></path><path d="M8 10h4"></path><path d="M8 14h4"></path>',
    'menu': '<path d="M4 6h16"></path><path d="M4 12h16"></path><path d="M4 18h16"></path>',
    'search': '<circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path>',
    'house': '<path d="M3 11l9-8 9 8"></path><path d="M5 10v10h14V10"></path><path d="M9 20v-6h6v6"></path>',
    'chevron-right': '<path d="M9 18l6-6-6-6"></path>',
    'corner-up-left': '<path d="M9 14L4 9l5-5"></path><path d="M4 9h9a7 7 0 017 7v3"></path>',
    'folder': '<path d="M3 7a2 2 0 012-2h5l2 2h7a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"></path>',
    'file': '<path d="M14 2H7a2 2 0 00-2 2v16a2 2 0 002 2h10a2 2 0 002-2V7z"></path><path d="M14 2v5h5"></path>',
    'eye': '<path d="M2.5 12s3.5-7 9.5-7 9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7z"></path><circle cx="12" cy="12" r="3"></circle>',
    'image': '<rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="8" cy="8" r="1.5"></circle><path d="M21 15l-5-5L5 21"></path>',
    'folder-archive': '<path d="M3 7a2 2 0 012-2h5l2 2h7a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"></path><path d="M9 11h6"></path><path d="M9 14h6"></path>',
    'sparkles': '<path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7z"></path><path d="M5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8z"></path>',
    'file-down': '<path d="M14 2H7a2 2 0 00-2 2v16a2 2 0 002 2h10a2 2 0 002-2V7z"></path><path d="M14 2v5h5"></path><path d="M12 12v6"></path><path d="M9 15l3 3 3-3"></path>',
    'square-pen': '<rect x="3" y="3" width="18" height="18" rx="2"></rect><path d="M12 8l4 4-6 6H6v-4z"></path>',
    'x': '<path d="M18 6 6 18"></path><path d="M6 6l12 12"></path>',
    'minus': '<path d="M5 12h14"></path>',
    'plus': '<path d="M12 5v14"></path><path d="M5 12h14"></path>',
    'notebook-pen': '<path d="M7 2h8a2 2 0 012 2v16a2 2 0 01-2 2H7a2 2 0 01-2-2V4a2 2 0 012-2z"></path><path d="M9 8h6"></path><path d="M9 12h4"></path><path d="M14 16l4-4 2 2-4 4H14z"></path>',
    'refresh-cw': '<path d="M21 12a9 9 0 10-3.3 7"></path><path d="M21 3v6h-6"></path>',
    'circle-plus': '<circle cx="12" cy="12" r="9"></circle><path d="M12 8v8"></path><path d="M8 12h8"></path>',
}

@register.simple_tag
def icon(name, classes='h-5 w-5'):
    paths = ICON_PATHS.get(name)
    if not paths:
        return mark_safe(f'<svg class="{classes} text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"></svg>')
    return mark_safe(
        f'<svg class="{classes}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )

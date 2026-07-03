from django import template

register = template.Library()

@register.filter
def startswith(value, arg):
    return value.startswith(arg)


@register.filter
def get_item(value, key):
    if isinstance(value, dict):
        return value.get(key, '')
    return ''

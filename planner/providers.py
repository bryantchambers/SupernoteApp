"""Provider routing shared by the web, tablet and worker."""
from django.conf import settings


def writes_enabled(calendar):
    if calendar.connection.provider == 'exchange':
        return bool(settings.PLANNER_EXCHANGE_WRITES_ENABLED and calendar.connection.write_verified_at)
    return settings.PLANNER_GOOGLE_WRITES_ENABLED


def validate_edit(item, previous=None):
    if previous and previous.provider_read_only:
        raise ValueError('Exchange meetings and recurring entries are read only here. Change them in Outlook.')
    if previous and previous.remote_id and previous.calendar_id != item.calendar_id:
        raise ValueError('Moving a synced entry between calendars is not supported. Create a separate entry instead.')
    if item.calendar_id:
        if item.calendar.connection.owner_id != item.book.owner_id:
            raise ValueError('This calendar belongs to another account.')
        if not item.calendar.selected or not item.calendar.writable:
            raise ValueError('This calendar is read only or no longer selected.')
        if item.kind != item.calendar.kind:
            raise ValueError('Choose a matching calendar or task list.')
        if item.calendar.connection.provider == 'exchange' and (item.kind != 'event' or item.provider_read_only):
            raise ValueError('Exchange supports ordinary appointments only. Change meetings and recurring entries in Outlook.')


def sync_connection(connection):
    if connection.provider == 'google':
        from .google import sync_connection as sync
    elif connection.provider == 'exchange':
        from .exchange import sync_connection as sync
    else:
        raise ValueError('Unknown calendar provider.')
    return sync(connection)

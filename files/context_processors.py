from .services import get_sync_state


def sync_context(request):
    try:
        return {"sync_state": get_sync_state()}
    except Exception:
        return {"sync_state": None}

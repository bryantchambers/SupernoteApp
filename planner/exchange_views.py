"""Authenticated Exchange setup; passwords only accepted over HTTPS."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from . import exchange
from .forms import ExchangeForm
from .models import Calendar, Connection


@login_required
@never_cache
@sensitive_post_parameters('password', 'username')
@require_http_methods(['GET', 'POST'])
def setup(request):
    connection = Connection.objects.filter(owner=request.user, provider='exchange').first()
    initial = {}
    if connection and connection.credentials:
        try:
            values = exchange.credentials(connection)
            initial = {key: values[key] for key in ('mailbox', 'username')}
        except ValueError:
            pass
    form = ExchangeForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if not request.is_secure():
            form.add_error(None, 'Open the planner HTTPS address before entering a work credential.')
        elif initial and initial['mailbox'] != form.cleaned_data['mailbox']:
            form.add_error('mailbox', 'Replace the credential for the same mailbox. Switching mailboxes is not supported.')
        else:
            connection, _ = Connection.objects.get_or_create(owner=request.user, provider='exchange')
            try:
                with exchange.connection_lock(connection):
                    exchange.store_credentials(connection, form.cleaned_data)
                    try:
                        exchange.discover(connection)
                    except Exception as exc:
                        exchange.record_failure(connection, exc)
                        # A failed setup always requires an explicit retry.
                        connection.suspended = True
                        connection.save(update_fields=['suspended'])
                        raise exchange.ExchangeError('KU connection could not be verified. Check your login name/password and Exchange access. Automatic attempts are paused.') from None
                messages.success(request, 'KU Exchange connected. Select your work calendar below; writes remain gated until acceptance.')
                return redirect('/planner/#planner-settings')
            except exchange.ExchangeError as exc:
                form.add_error(None, str(exc))
    return render(request, 'planner/exchange_setup.html', {'form': form, 'exchange_connection': connection})


@login_required
@require_POST
def test(request):
    connection = get_object_or_404(Connection, owner=request.user, provider='exchange')
    try:
        with exchange.connection_lock(connection):
            try:
                exchange.discover(connection)
            except Exception as exc:
                exchange.record_failure(connection, exc)
                connection.suspended = True
                connection.save(update_fields=['suspended'])
                raise exchange.ExchangeError('KU connection check failed. Automatic attempts are paused; check or replace the credential.') from None
        messages.success(request, 'KU connection verified and calendar list refreshed.')
    except exchange.ExchangeError as exc:
        messages.error(request, str(exc))
    return redirect('/planner/#planner-settings')


@login_required
@require_POST
def disconnect(request):
    connection = get_object_or_404(Connection, owner=request.user, provider='exchange')
    try:
        with exchange.connection_lock(connection):
            connection.credentials, connection.suspended = '', True
            connection.message = 'Disconnected. Cached events and queued changes retained.'
            connection.save()
            Calendar.objects.filter(connection=connection).update(writable=False, is_default=False)
        messages.success(request, 'KU credential removed. Work sync stopped; cached entries remain visible.')
    except exchange.ExchangeError as exc:
        messages.error(request, str(exc))
    return redirect('/planner/#planner-settings')

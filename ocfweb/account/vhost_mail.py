import re
from contextlib import contextmanager

from django.conf import settings
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_POST
from ocflib.account.validators import validate_password
from ocflib.vhost.mail import crypt_password
from ocflib.vhost.mail import get_connection
from ocflib.vhost.mail import MailForwardingAddress
from ocflib.vhost.mail import vhosts_for_user

from ocfweb.auth import group_account_required
from ocfweb.auth import login_required
from ocfweb.component.errors import ResponseException
from ocfweb.component.session import logged_in_user


def _parse_addr(addr):
    """Safely parse an email, returning first component and domain."""
    m = re.match('([a-zA-Z0-9\-_\+\.]+)@([a-zA-Z0-9\-_\+\.]+)$', addr)
    if not m:
        raise ValueError('invalid address: {}'.format(addr))
    return m.group(1), m.group(2)


@login_required
@group_account_required
def vhost_mail(request):
    user = logged_in_user(request)
    vhosts = vhosts_for_user(user)

    with _txn() as c:
        return render(
            request,
            'account/vhost_mail/index.html',
            {
                'title': 'Mail Virtual Hosting',

                'c': c,
                'vhosts': sorted(vhosts),
            },
        )


@contextmanager
def _txn(**kwargs):
    with get_connection(
        user=settings.OCFMAIL_USER,
        password=settings.OCFMAIL_PASSWORD,
        db=settings.OCFMAIL_DB,
        autocommit=False,
        **kwargs
    ) as c:
        try:
            yield c
        except:
            c.connection.rollback()
            raise
        else:
            c.connection.commit()


def _get_vhost(request, user, domain):
    vhosts = vhosts_for_user(user)
    for vhost in vhosts:
        if vhost.domain == domain:
            return vhost
    else:
        messages.add_message(request, messages.ERROR, 'Invalid virtual host.')
        raise ResponseException(_redirect_back())


def _hash_password(request, name, password):
    if password is not None:
        try:
            validate_password(name, password, strength_check=True)
        except ValueError as ex:
            messages.add_message(request, messages.ERROR, ex.args[0])
            raise ResponseException(_redirect_back())
        return crypt_password(password)
    else:
        return None


def _find_addr(request, c, vhost, addr, raise_on_error=True):
    for addr_obj in vhost.get_forwarding_addresses(c):
        if addr_obj.address == addr:
            return addr_obj
    else:
        if raise_on_error:
            messages.add_message(request, messages.ERROR, 'That address does not exist.')
            raise ResponseException(_redirect_back())


def _redirect_back():
    return redirect(reverse('vhost_mail'))


@login_required
@group_account_required
@require_POST
def vhost_mail_add_address(request):
    addr_name = request.POST.get('name')
    addr_domain = request.POST.get('domain')
    addr = '{}@{}'.format(addr_name, addr_domain)
    forward_to = request.POST.get('forward_to')
    password = request.POST.get('password') or None
    user = logged_in_user(request)

    try:
        name, domain = _parse_addr(addr)
    except ValueError:
        messages.add_message(request, messages.ERROR, 'Invalid email address: {}'.format(addr))
        return _redirect_back()

    try:
        _parse_addr(forward_to)
    except ValueError:
        messages.add_message(request, messages.ERROR, 'Invalid forwarding address.')
        return _redirect_back()

    vhost = _get_vhost(request, user, domain)
    pw_hash = _hash_password(request, name, password)

    with _txn() as c:
        if _find_addr(request, c, vhost, addr, raise_on_error=False):
            messages.add_message(request, messages.ERROR, 'Address already exists.')
            return _redirect_back()

        vhost.add_forwarding_address(
            c,
            MailForwardingAddress(
                address=addr,
                crypt_password=pw_hash,
                forward_to=forward_to,
                last_updated=None,
            ),
        )

    messages.add_message(request, messages.SUCCESS, 'Address added successfully!')
    return _redirect_back()


@login_required
@group_account_required
@require_POST
def vhost_mail_remove_address(request):
    addr = request.POST.get('addr')
    user = logged_in_user(request)

    try:
        _, domain = _parse_addr(addr)
    except ValueError:
        messages.add_message(request, messages.ERROR, 'Invalid email address.')
        return _redirect_back()

    vhost = _get_vhost(request, user, domain)

    with _txn() as c:
        addr_obj = _find_addr(request, c, vhost, addr)
        vhost.remove_forwarding_address(c, addr_obj.address)

    messages.add_message(request, messages.SUCCESS, 'Address deleted successfully!')
    return _redirect_back()


@login_required
@group_account_required
@require_POST
def vhost_mail_update_password(request):
    addr = request.POST.get('addr')
    password = request.POST.get('password') or None
    user = logged_in_user(request)

    try:
        name, domain = _parse_addr(addr)
    except ValueError:
        messages.add_message(request, messages.ERROR, 'Invalid email address.')
        return _redirect_back()

    vhost = _get_vhost(request, user, domain)
    pw_hash = _hash_password(request, name, password)

    with _txn() as c:
        addr_obj = _find_addr(request, c, vhost, addr)
        vhost.remove_forwarding_address(c, addr_obj.address)
        vhost.add_forwarding_address(
            c,
            addr_obj._replace(
                crypt_password=pw_hash,
            ),
        )

    messages.add_message(request, messages.SUCCESS, 'Password changed successfully!')
    return _redirect_back()
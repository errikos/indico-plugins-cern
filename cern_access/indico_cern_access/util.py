from __future__ import unicode_literals

import hashlib
import hmac
import json
import random
import string

import requests
from flask import session
from pytz import timezone
from werkzeug.exceptions import Forbidden

from indico.core.notifications import make_email, send_email
from indico.modules.events.registration.models.forms import RegistrationForm
from indico.modules.events.registration.models.registrations import Registration
from indico.modules.events.registration.util import get_ticket_attachments
from indico.util.string import remove_accents, unicode_to_ascii
from indico.web.flask.templating import get_template_module

from indico_cern_access import _
from indico_cern_access.models.access_request_regforms import CERNAccessRequestRegForm
from indico_cern_access.models.access_requests import CERNAccessRequest, CERNAccessRequestState


def get_requested_forms(event):
    """Returns list of registration forms with requested access to CERN"""
    return (RegistrationForm.query.with_parent(event)
            .join(CERNAccessRequestRegForm)
            .filter(CERNAccessRequestRegForm.is_active)
            .all())


def get_requested_registrations(event, regform=None):
    """By default returns a list of requested registrations of an event

    :param regform: if specified, returns only registrations with that registration form
    """
    query = (Registration.query.with_parent(event)
             .join(CERNAccessRequest)
             .filter(CERNAccessRequest.is_active))
    if regform:
        query = query.filter(Registration.registration_form_id == regform.id)
    return query.all()


def send_adams_post_request(event, registrations, update=False):
    """ Sends POST request to ADaMS API

    :param update: if True, send request updating already stored data
    """
    from indico_cern_access.plugin import CERNAccessPlugin
    data = {registration.id: build_access_request_data(registration, event, update=update)
            for registration in registrations}
    headers = {'content-type': 'application/json'}
    auth = (CERNAccessPlugin.settings.get('login'), CERNAccessPlugin.settings.get('password'))
    json_data = json.dumps(data.values())
    url = CERNAccessPlugin.settings.get('adams_url')
    try:
        r = requests.post(url, data=json_data, headers=headers, auth=auth)
        r.raise_for_status()
    except requests.exceptions.RequestException:
        CERNAccessPlugin.logger.exception('Request to ADAMS failed (%r)', json_data)
        raise AdamsError(_('Sending request to ADAMS failed'))
    return (CERNAccessRequestState.accepted, data)


def send_adams_delete_request(registrations):
    """Sends DELETE request to ADaMS API"""
    from indico_cern_access.plugin import CERNAccessPlugin

    data = [generate_access_id(registration.id) for registration in registrations]
    headers = {'Content-Type': 'application/json'}
    auth = (CERNAccessPlugin.settings.get('login'), CERNAccessPlugin.settings.get('password'))
    data = json.dumps(data)
    url = CERNAccessPlugin.settings.get('adams_url')
    try:
        r = requests.delete(url, data=data, headers=headers, auth=auth)
        r.raise_for_status()
    except requests.exceptions.RequestException:
        CERNAccessPlugin.logger.exception('Request to ADAMS failed (%r)', data)
        raise AdamsError('Sending request to ADAMS failed')


def generate_access_id(registration_id):
    """Generates an id in format required by ADaMS API"""
    return 'in{}'.format(registration_id)


def build_access_request_data(registration, event, update=False):
    """Returns a dictionary with data required by ADaMS API"""
    from indico_cern_access.plugin import CERNAccessPlugin

    tz = timezone('Europe/Zurich')
    data = {}
    if update:
        reservation_code = registration.cern_access_request.reservation_code
    else:
        reservation_code = get_random_reservation_code()
    data.update({'$id': generate_access_id(registration.id),
                 '$rc': reservation_code,
                 '$gn': unicode_to_ascii(remove_accents(event.title)),
                 '$fn': unicode_to_ascii(remove_accents(registration.first_name)),
                 '$ln': unicode_to_ascii(remove_accents(registration.last_name)),
                 '$sd': event.start_dt.astimezone(tz).strftime('%Y-%m-%dT%H:%M'),
                 '$ed': event.end_dt.astimezone(tz).strftime('%Y-%m-%dT%H:%M')})
    checksum = ';;'.join('{}:{}'.format(key, value) for key, value in sorted(data.viewitems()))
    signature = hmac.new(str(CERNAccessPlugin.settings.get('secret_key')), checksum, hashlib.sha256)
    data['$si'] = signature.hexdigest()
    return data


def update_access_request(req):
    """Adds, updates and deletes CERN access requests from registration forms"""

    event = req.event
    existing_forms = get_requested_forms(event)
    requested_forms = req.data['regforms']

    # Pull out ids of existing and requested forms to easily
    # check which ones should be added/deleted afterwards
    existing_forms_ids = {regform.id for regform in existing_forms}
    requested_forms_ids = {int(id) for id in requested_forms}
    event_regforms = {regform.id: regform for regform in event.registration_forms}

    # add requests
    for regform_id in requested_forms_ids - existing_forms_ids:
        regform = event_regforms[regform_id]
        create_access_request_regform(regform, state=CERNAccessRequestState.accepted)
        enable_ticketing(regform)

    # delete requests
    for regform_id in existing_forms_ids - requested_forms_ids:
        regform = event_regforms[regform_id]
        registrations = get_requested_registrations(event, regform=regform)
        send_adams_delete_request(registrations)
        regform.cern_access_request.request_state = CERNAccessRequestState.withdrawn
        remove_access_template(regform)
        withdraw_access_requests(registrations)
        notify_access_withdrawn(registrations)


def remove_access_template(regform):
    from indico_cern_access.plugin import CERNAccessPlugin
    access_tpl = CERNAccessPlugin.settings.get('access_ticket_template_id')
    if regform.ticket_template == access_tpl:
        regform.ticket_template = None


def add_access_requests(registrations, data, state):
    """Adds CERN access requests for registrations"""
    for registration in registrations:
        create_access_request(registration, state, data[registration.id]['$rc'])


def update_access_requests(registrations, state):
    """Updates already requested registrations"""
    for registration in registrations:
        registration.cern_access_request.request_state = state


def withdraw_access_requests(registrations):
    """Withdraws CERN access requests for registrations"""
    for registration in registrations:
        registration.cern_access_request.request_state = CERNAccessRequestState.withdrawn


def withdraw_event_access_request(req):
    """Withdraws all CERN access requests of an event"""
    requested_forms = get_requested_forms(req.event)
    requested_registrations = get_requested_registrations(req.event)
    send_adams_delete_request(requested_registrations)
    for regform in requested_forms:
        regform.cern_access_request.request_state = CERNAccessRequestState.withdrawn
        remove_access_template(regform)
    withdraw_access_requests(requested_registrations)
    notify_access_withdrawn(requested_registrations)


def get_random_reservation_code():
    """Generates random reservation code for data required by ADaMS API"""
    return 'I' + ''.join(random.sample(string.ascii_uppercase.replace('O', '') + string.digits, 6))


def create_access_request(registration, state, reservation_code):
    """Creates CERN access request object for registration"""
    if registration.cern_access_request:
        registration.cern_access_request.request_state = state
        registration.cern_access_request.reservation_code = reservation_code
    else:
        registration.cern_access_request = CERNAccessRequest(request_state=state,
                                                             reservation_code=reservation_code)


def create_access_request_regform(regform, state):
    """Creates CERN access request object for registration form"""
    from indico_cern_access.plugin import CERNAccessPlugin
    access_tpl = CERNAccessPlugin.settings.get('access_ticket_template_id')
    if state == CERNAccessRequestState.accepted and access_tpl:
        regform.ticket_template = access_tpl
    if regform.cern_access_request:
        regform.cern_access_request.request_state = state
    else:
        regform.cern_access_request = CERNAccessRequestRegForm(request_state=state)


def is_authorized_user(user):
    """Checks if user is authorized to request access to CERN"""
    from indico_cern_access.plugin import CERNAccessPlugin
    return CERNAccessPlugin.settings.acls.contains_user('authorized_users', user)


def notify_access_withdrawn(registrations):
    """Notifies participants when access to CERN has been withdrawn"""
    for registration in registrations:
        template = get_template_module('cern_access:request_withdrawn_email.html', registration=registration)
        from_address = registration.registration_form.sender_address
        email = make_email(to_list=registration.email, from_address=from_address,
                           template=template, html=True)
        send_email(email, event=registration.registration_form.event, module='Registration', user=session.user)


def send_tickets(registrations):
    """Sends tickets to access CERN site to registered users"""
    for registration in registrations:
        template = get_template_module('cern_access:ticket_email.html', registration=registration)
        from_address = registration.registration_form.sender_address
        attachments = get_ticket_attachments(registration)
        email = make_email(to_list=registration.email, from_address=from_address,
                           template=template, html=True, attachments=attachments)
        send_email(email, event=registration.registration_form.event, module='Registration',
                   user=session.user)


def enable_ticketing(regform):
    """Enables ticketing module automatically"""
    if not regform.tickets_enabled:
        regform.tickets_enabled = True
        regform.tickets_on_email = True
        regform.ticket_on_event_page = True
        regform.ticket_on_summary_page = True


def is_category_blacklisted(category):
    from indico_cern_access.plugin import CERNAccessPlugin
    return any(category.id == int(cat['id']) for cat in CERNAccessPlugin.settings.get('excluded_categories'))


def grant_access(registrations, regform):
    event = regform.event
    new_registrations = [reg for reg in registrations if
                         not (reg.cern_access_request
                              and reg.cern_access_request.is_active
                              and reg.cern_access_request.request_state == CERNAccessRequestState.accepted)]
    state, data = send_adams_post_request(event, new_registrations)
    add_access_requests(new_registrations, data, state)
    if regform.ticket_on_email:
        send_tickets(new_registrations)


def revoke_access(registrations):
    send_adams_delete_request(registrations)
    requested_registrations = [reg for reg in registrations if
                               reg.cern_access_request
                               and reg.cern_access_request.is_active
                               and reg.cern_access_request.request_state == CERNAccessRequestState.accepted]
    withdraw_access_requests(requested_registrations)
    notify_access_withdrawn(requested_registrations)


def check_access(req):
    user_authorized = is_authorized_user(session.user)
    category_blacklisted = is_category_blacklisted(req.event.category)
    if not user_authorized or category_blacklisted:
        raise Forbidden()


class AdamsError(Exception):
    pass
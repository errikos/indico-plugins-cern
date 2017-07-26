from __future__ import unicode_literals

from flask import session
from indico.core.db import db
from werkzeug.exceptions import Forbidden

from indico.modules.events.requests import RequestDefinitionBase

from indico_cern_access import _
from indico_cern_access.forms import CERNAccessForm
from indico_cern_access.util import update_access_request, withdraw_event_access_request, is_authorized_user


class CERNAccessRequestDefinition(RequestDefinitionBase):
    name = 'cern_access'
    title = _('CERN access')
    form = CERNAccessForm

    @classmethod
    def render_form(cls, event, **kwargs):
        kwargs['can_send_request'] = is_authorized_user(session.user)
        return super(CERNAccessRequestDefinition, cls).render_form(event, **kwargs)

    @classmethod
    def can_be_managed(cls, user):
        return False

    @classmethod
    def send(cls, req, data):
        with db.session.no_autoflush:
            is_authorized = is_authorized_user(session.user)
        if not is_authorized:
            raise Forbidden()
        super(CERNAccessRequestDefinition, cls).send(req, data)
        req.state = update_access_request(req)

    @classmethod
    def withdraw(cls, req, notify_event_managers=False):
        with db.session.no_autoflush:
            is_authorized = is_authorized_user(session.user)
        if not is_authorized:
            raise Forbidden()
        super(CERNAccessRequestDefinition, cls).withdraw(req, notify_event_managers)
        withdraw_event_access_request(req)

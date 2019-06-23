# -*- coding: utf-8 -*-
from collective.wsevents.subscribers import CallbackDataManager
from collective.wsevents.subscribers import encode_message
from collective.wsevents.subscribers import get_allowed_roles_and_users_guard
from collective.wsevents.subscribers import publish_message
from OFS.SimpleItem import SimpleItem
from plone.app.contentrules import PloneMessageFactory as _
from plone.contentrules.rule.interfaces import IExecutable
from plone.contentrules.rule.interfaces import IRuleElementData
from plone.stringinterp.interfaces import IStringInterpolator
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from transaction import get as get_transaction
from z3c.form.interfaces import IValidator
from z3c.form.util import getSpecification
from zope import schema
from zope.component import adapter
from zope.interface import implementer
from zope.interface import Interface
from zope.interface import Invalid
from zope.schema import ValidationError

import json
import logging
import os
import six


try:
    from plone.app.contentrules.browser.formhelper import (
        ContentRuleFormWrapper,
    )  # noqa: E501
    from plone.app.contentrules.actions import ActionAddForm
    from plone.app.contentrules.actions import ActionEditForm

    PLONE_4 = False
except ImportError:
    from plone.app.contentrules.browser.formhelper import (
        AddForm as ActionAddForm,
    )  # noqa: E501
    from plone.app.contentrules.browser.formhelper import (
        EditForm as ActionEditForm,
    )  # noqa: E501
    from zope.formlib import form

    PLONE_4 = True


logger = logging.getLogger("collective.wsevents")


def validate_payload(value):
    try:
        if value is not None:
            json.loads(value)
    except (ValueError, TypeError) as e:

        class JSONValidationError(ValidationError):
            __doc__ = str(e)

        raise JSONValidationError()
    return True


class IWebSocketAction(Interface):
    """Definition of the configuration available for a websocket action
    """

    payload = schema.Text(
        title=_(u"JSON Payload"),
        description=_(u"The message you want to publish in JSON"),
        required=False,
        constraint=validate_payload,
        default="""\
{ 
  "text": "Hello ${title}!",
  "destination": "${url}"
}
""",
    )


@implementer(IValidator)
@adapter(None, None, None, getSpecification(IWebSocketAction["payload"]), None)
class PayloadValidator(object):
    def __init__(self, context, request, form, field, widget):
        self.field = field

    def validate(self, value):
        try:
            if value is not None:
                json.loads(value)
        except (ValueError, TypeError) as e:
            raise Invalid(e)


@implementer(IWebSocketAction, IRuleElementData)
class WebSocketAction(SimpleItem):
    """
    The implementation of the action defined before
    """

    payload = u""

    element = "plone.actions.WebSocket"

    @property
    def summary(self):
        return _(u"${payload}", mapping=dict(payload=self.payload))


def interpolate(value, interpolator):
    """Recursively interpolate supported values"""
    if isinstance(value, six.text_type):
        return interpolator(value).strip()
    elif isinstance(value, list):
        return [interpolate(v, interpolator) for v in value]
    elif isinstance(value, dict):
        return dict([(k, interpolate(v, interpolator)) for k, v in value.items()])
    return value


@implementer(IExecutable)
@adapter(Interface, IWebSocketAction, Interface)
class WebSocketActionExecutor(object):
    """The executor for this action.
    """

    timeout = 120

    def __init__(self, context, element, event):
        self.context = context
        self.element = element
        self.event = event

    def __call__(self):
        obj = self.event.object
        interpolator = IStringInterpolator(obj)
        payload = interpolate(json.loads(self.element.payload), interpolator)
        topic = "/".join(obj.getPhysicalPath())

        guards_context = get_allowed_roles_and_users_guard(self.context)
        guards_event = get_allowed_roles_and_users_guard(obj)
        if (
            "Anonymous" in guards_event["allowedRolesAndUsers"]["tokens"]
            and "Anonymous" not in guards_context["allowedRolesAndUsers"]["tokens"]
        ):
            guards = guards_context
        else:
            guards = guards_event

        message = {"guards": guards, "payload": {"notifications": [payload]}}
        dm = CallbackDataManager(publish_message, encode_message(message), topic)
        get_transaction().join(dm)


class WebSocketAddForm(ActionAddForm):
    """
    An add form for the websocket action
    """

    schema = IWebSocketAction
    label = _(u"Add WebSocket Action")
    description = _(u"A websocket action can publish an " u"interpolated JSON payload.")
    form_name = _(u"Configure element")
    Type = WebSocketAction

    if PLONE_4:
        form_fields = form.FormFields(IWebSocketAction)
        template = ViewPageTemplateFile(os.path.join("templates", "websocket_p4.pt"))

        def create(self, data):
            a = WebSocketAction()
            form.applyChanges(a, self.form_fields, data)
            return a

    else:
        template = ViewPageTemplateFile(os.path.join("templates", "websocket.pt"))


if PLONE_4:

    class WebSocketAddFormView(WebSocketAddForm):
        pass


else:

    class WebSocketAddFormView(ContentRuleFormWrapper):
        form = WebSocketAddForm


class WebSocketEditForm(ActionEditForm):
    """
    An edit form for the websocket action
    """

    schema = IWebSocketAction
    label = _(u"Edit WebSocket Action")
    description = _(u"A websocket action can publish an " u"interpolated JSON payload.")
    form_name = _(u"Configure element")

    if PLONE_4:
        form_fields = form.FormFields(IWebSocketAction)
        template = ViewPageTemplateFile(os.path.join("templates", "websocket_p4.pt"))
    else:
        template = ViewPageTemplateFile(os.path.join("templates", "websocket.pt"))


if PLONE_4:

    class WebSocketEditFormView(WebSocketEditForm):
        pass


else:

    class WebSocketEditFormView(ContentRuleFormWrapper):
        form = WebSocketEditForm

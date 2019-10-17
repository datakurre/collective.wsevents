# -*- coding: utf-8 -*-
from Acquisition import aq_parent
from Products.CMFCore.utils import _getAuthenticatedUser
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.CatalogTool import allowedRolesAndUsers
from Products.Five import BrowserView
from transaction import get as get_transaction
from transaction.interfaces import ISavepoint
from transaction.interfaces import ISavepointDataManager
from zope.annotation import IAnnotations
from zope.component.hooks import getSite
from zope.globalrequest import getRequest
from zope.interface import implementer
from ZServer.TwistedHTTPServer import publish

import json
import logging
import time
import twisted.internet


logger = logging.getLogger("collective.wsevents")


def encode_message(message):
    # Assert message payload as encoded json
    if not isinstance(message["payload"], bytes):
        # noinspection PyBroadException
        try:
            if not isinstance(message["payload"], str):
                message["payload"] = json.dumps(message["payload"])
            message["payload"] = message["payload"].encode("utf-8")
        except:  # noqa
            logger.exception("Message discarded:")
            return
    return message


class NotificationGuard(BrowserView):
    def __call__(self):
        user = _getAuthenticatedUser(self)
        pc = getToolByName(self.context, "portal_catalog")
        self.request.response.setHeader("Content-Type", "application/json")
        # noinspection PyProtectedMember
        return json.dumps(
            {
                "allowedRolesAndUsers": {
                    "tokens": list(pc._listAllowedRolesAndUsers(user)),
                    "expires": time.time() + 60,
                }
            }
        )


def publish_message(message, topic):
    try:
        twisted.internet.reactor.callFromThread(publish, message, topic)
    except AttributeError:
        logger.exception("Unexpected error:")


def get_allowed_roles_and_users(context, additional_roles_and_users=()):
    return set(allowedRolesAndUsers.callable(context)) | set(additional_roles_and_users)


def get_allowed_roles_and_users_guard(context, additional_roles_and_users=()):
    return {
        "allowedRolesAndUsers": {
            "method": "/".join(getSite().getPhysicalPath()) + "/@@wsevents-guard",
            "tokens": get_allowed_roles_and_users(context, additional_roles_and_users),
        }
    } if getSite() else {}


def publish_object_created(ob, event):
    parent = aq_parent(ob)
    topic = "/".join(ob.getPhysicalPath())
    if parent is not None:
        message = {
            "guards": get_allowed_roles_and_users_guard(ob),
            "payload": {
                "created": [
                    {"@id": ob.absolute_url(), "parent": {"@id": parent.absolute_url()}}
                ]
            },
        }
    else:
        message = {
            "guards": get_allowed_roles_and_users_guard(ob),
            "payload": {"created": [{"@id": ob.absolute_url()}]},
        }
    dm = CallbackDataManager(publish_message, encode_message(message), topic)
    get_transaction().join(dm)


def publish_before_action(ob, event):
    request = getRequest()
    annotations = IAnnotations(request)
    key = "/".join(ob.getPhysicalPath())
    if "collective.wsevents" not in annotations:
        annotations["collective.wsevents"] = {}
    allowed = get_allowed_roles_and_users(ob)
    annotations["collective.wsevents"][key] = allowed


def publish_after_action(ob, event):
    request = getRequest()
    annotations = IAnnotations(request)
    key = "/".join(ob.getPhysicalPath())
    allowed = annotations["collective.wsevents"].get(key) or ()
    guard = get_allowed_roles_and_users_guard(ob, allowed)
    publish_object_modified(ob, event, guard)


def publish_object_modified(ob, event, guards=None):
    topic = "/".join(ob.getPhysicalPath())
    parent = aq_parent(ob)
    if parent is not None:
        message = {
            "guards": guards or get_allowed_roles_and_users_guard(ob),
            "payload": {
                "modified": [
                    {"@id": ob.absolute_url(), "parent": {"@id": parent.absolute_url()}}
                ]
            },
        }
    else:
        message = {
            "guards": guards or get_allowed_roles_and_users_guard(ob),
            "payload": {"modified": [{"@id": ob.absolute_url()}]},
        }
    dm = CallbackDataManager(publish_message, encode_message(message), topic)
    get_transaction().join(dm)


def publish_object_moved(ob, event):
    topic = "/".join(ob.getPhysicalPath())
    if event.oldParent is not None and event.newParent is not None:
        new_id = ob.absolute_url()
        old_id = new_id.replace(
            event.newParent.absolute_url() + "/" + event.newName,
            event.oldParent.absolute_url() + "/" + event.oldName,
        )
        parent = aq_parent(ob)
        parent_new_id = parent.absolute_url()
        parent_old_id = parent_new_id.replace(
            event.newParent.absolute_url() + "/" + event.newName,
            event.oldParent.absolute_url() + "/" + event.oldName,
        )
        message = {
            "guards": get_allowed_roles_and_users_guard(ob.__of__(event.oldParent)),
            "payload": {"removed": [{"@id": old_id, "parent": {"@id": parent_old_id}}]},
        }
        dm = CallbackDataManager(publish_message, encode_message(message), topic)
        get_transaction().join(dm)

        message = {
            "guards": get_allowed_roles_and_users_guard(ob),
            "payload": {"created": [{"@id": new_id, "parent": {"@id": parent_new_id}}]},
        }
        dm = CallbackDataManager(publish_message, encode_message(message), topic)
        get_transaction().join(dm)


def publish_object_removed(ob, event):
    topic = "/".join(ob.getPhysicalPath())
    parent = aq_parent(ob)
    if parent is not None:
        message = {
            "guards": get_allowed_roles_and_users_guard(ob),
            "payload": {
                "removed": [
                    {"@id": ob.absolute_url(), "parent": {"@id": parent.absolute_url()}}
                ]
            },
        }
    else:
        message = {
            "guards": get_allowed_roles_and_users_guard(ob),
            "payload": {"removed": [{"@id": ob.absolute_url()}]},
        }
    dm = CallbackDataManager(publish_message, encode_message(message), topic)
    get_transaction().join(dm)


def publish_object_commented(ob, event):
    topic = "/".join(ob.getPhysicalPath())
    parent = aq_parent(ob)
    comment = event.comment
    author = comment.author_username

    if author:
        portal_membership = getToolByName(ob, "portal_membership")
        member = portal_membership.getMemberById(author)
        if member is not None:
            author = member.getProperty("fullname") or author
    else:
        author = comment.author_name

    guards_object = get_allowed_roles_and_users_guard(ob)
    guards_comment = get_allowed_roles_and_users_guard(comment)
    if (
        "Anonymous" in guards_comment["allowedRolesAndUsers"]["tokens"]
        and "Anonymous" not in guards_object["allowedRolesAndUsers"]["tokens"]
    ):
        guards = guards_object
    else:
        guards = guards_comment

    if parent is not None:
        message = {
            "guards": guards,
            "payload": {
                "commented": [
                    {
                        "@id": ob.absolute_url(),
                        "parent": {"@id": parent.absolute_url()},
                        "title": ob.title,
                        "text": comment.text,
                        "author": author,
                    }
                ]
            },
        }
    else:
        message = {
            "guards": guards,
            "payload": {
                "commented": [
                    {
                        "@id": ob.absolute_url(),
                        "title": ob.title,
                        "text": comment.text,
                        "author": author,
                    }
                ]
            },
        }
    dm = CallbackDataManager(publish_message, encode_message(message), topic)
    get_transaction().join(dm)


@implementer(ISavepoint)
class DummySavepoint:

    valid = property(lambda self: self.transaction is not None)

    def __init__(self, datamanager):
        self.datamanager = datamanager

    def rollback(self):
        pass


@implementer(ISavepointDataManager)
class CallbackDataManager(object):

    _COUNTER = 0

    def __init__(self, callable, *args, **kwargs):
        self.callable = callable
        self.args = args
        self.kwargs = kwargs

        self.sort_key = "~collective.wsevents.{counter:d}".format(
            counter=CallbackDataManager._COUNTER
        )
        CallbackDataManager._COUNTER += 1

    def commit(self, t):
        pass

    def sortKey(self):
        return self.sort_key

    def abort(self, t):
        pass

    def tpc_begin(self, t):
        pass

    def tpc_vote(self, t):
        pass

    def tpc_finish(self, t):
        try:
            self.callable(*self.args, **self.kwargs)
        except Exception:  # noqa
            logger.exception("Callback failed: ")

    def tpc_abort(self, t):
        pass

    def savepoint(self):
        return DummySavepoint(self)

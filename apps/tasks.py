from eve.utils import ParsedRequest
from eve.versioning import resolve_document_version
from apps.archive.common import insert_into_versions
from superdesk.resource import Resource
from superdesk import InvalidStateTransitionError, SuperdeskError
from superdesk.notification import push_notification
from superdesk.utc import utcnow
from apps.archive.common import on_create_item, item_url
from superdesk.services import BaseService
from apps.content import metadata_schema
import superdesk
from superdesk.activity import add_activity, ACTIVITY_CREATE, ACTIVITY_UPDATE
from apps.archive.archive import get_subject
from superdesk.workflow import is_workflow_state_transition_valid
from copy import copy
from eve.utils import config
from apps.archive.archive import SOURCE as ARCHIVE
from superdesk import get_resource_service


task_statuses = ['todo', 'in-progress', 'done']


def init_app(app):
    endpoint_name = 'tasks'
    service = TasksService(TaskResource.datasource['source'], backend=superdesk.get_backend())
    TaskResource(endpoint_name, app=app, service=service)


def send_to(doc, desk_id=None, stage_id=None):
    """Send item to given desk and stage.

    :param doc: item to be sent
    :param desk: id of desk where item should be sent
    :param stage: optional stage within the desk
    """
    task = doc.get('task', {})
    task.setdefault('desk', desk_id)
    task.setdefault('stage', stage_id)
    if desk_id and not stage_id:
        desk = superdesk.get_resource_service('desks').find_one(req=None, _id=desk_id)
        if not desk:
            raise SuperdeskError('Invalid desk identifier %s' % desk_id, 400)
        task['stage'] = desk.get('incoming_stage')
    if task['stage']:
        stage = get_resource_service('stages').find_one(req=None, _id=task['stage'])
        if not stage:
            raise SuperdeskError('Invalid stage identifier %s' % task['stage'], 400)
        if stage.get('task_status'):
            doc['task']['status'] = stage['task_status']
    doc['task'] = task


class TaskResource(Resource):
    datasource = {
        'source': 'archive',
        'default_sort': [('_updated', -1)],
        'filter': {'task': {'$exists': True}},
        'elastic_filter': {'exists': {'field': 'task'}}  # eve-elastic specific filter
    }
    item_url = item_url
    schema = {
        'slugline': metadata_schema['slugline'],
        'description_text': metadata_schema['description'],
        'type': metadata_schema['type'],
        'planning_item': Resource.rel('planning', True, type='string'),
        'task': {
            'type': 'dict',
            'schema': {
                'status': {
                    'type': 'string',
                    'allowed': task_statuses,
                    'default': 'todo'
                },
                'due_date': {'type': 'datetime'},
                'started_at': {'type': 'datetime'},
                'finished_at': {'type': 'datetime'},
                'user': Resource.rel('users', True),
                'desk': Resource.rel('desks', True),
                'stage': Resource.rel('stages', True)
            }
        }
    }
    privileges = {'POST': 'tasks', 'PATCH': 'tasks', 'DELETE': 'tasks'}


class TasksService(BaseService):

    def get(self, req, lookup):
        if req is None:
            req = ParsedRequest()
        return self.backend.get('tasks', req=req, lookup=lookup)

    def update_times(self, doc):
        task = doc.get('task', {})
        status = task.get('status', None)
        if status == 'in-progress':
            task.setdefault('started_at', utcnow())

        if status == 'done':
            task.setdefault('finished_at', utcnow())

    def __is_assigned_to_a_desk(self, doc):
        """
        Returns True if the 'doc' is being submitted to a desk. False otherwise.
        """

        return doc.get('task') and doc['task'].get('desk')

    def __is_content_moved_from_desk(self, doc):
        """
        Returns True if the 'doc' is being moved from a desk. False otherwise.
        """
        return doc.get('task', {}).get('desk', None) is None

    def __is_content_assigned_to_new_desk(self, original, updates):
        """
        Checks if the content is assigned to a new desk.
        :return: True if the content is being moved to a new desk. False otherwise.
        """
        return original.get('task', {}).get('desk', '') != str(updates.get('task', {}).get('desk', ''))

    def __update_state(self, updates, original):
            if self.__is_content_assigned_to_new_desk(original, updates):
                # check if the preconditions for the action are in place
                original_state = original[config.CONTENT_STATE]
                if not is_workflow_state_transition_valid('move', original_state):
                    raise InvalidStateTransitionError()

                updates[config.CONTENT_STATE] = 'draft' if self.__is_content_moved_from_desk(updates) else 'submitted'
                resolve_document_version(updates, ARCHIVE, 'PATCH', original)

    def update_stage(self, doc):
        task = doc.get('task', {})
        desk_id = task.get('desk', None)
        stage_id = task.get('stage', None)
        send_to(doc, desk_id, stage_id)

    def on_create(self, docs):
        on_create_item(docs)
        for doc in docs:
            resolve_document_version(doc, ARCHIVE, 'POST')
            self.update_times(doc)
            self.update_stage(doc)

    def on_created(self, docs):
        push_notification(self.datasource, created=1)
        for doc in docs:
            insert_into_versions(doc['_id'])
            if self.__is_assigned_to_a_desk(doc):
                add_activity(ACTIVITY_CREATE, 'added new task {{ subject }} of type {{ type }}', item=doc,
                             subject=get_subject(doc), type=doc['type'])

    def on_update(self, updates, original):
        self.update_times(updates)
        if self.__is_assigned_to_a_desk(updates):
            self.__update_state(updates, original)
        new_stage_id = updates.get('task', {}).get('stage', '')
        old_stage_id = original.get('task', {}).get('stage', '')
        if new_stage_id and new_stage_id != old_stage_id:
            new_stage = get_resource_service('stages').find_one(req=None, _id=new_stage_id)
            if not new_stage:
                raise SuperdeskError('Invalid stage identifier %s' % new_stage, 400)
            if new_stage.get('task_status'):
                updates['task']['status'] = new_stage['task_status']

    def on_updated(self, updates, original):
        new_stage = updates.get('task', {}).get('stage', '')
        old_stage = original.get('task', {}).get('stage', '')
        if new_stage != old_stage:
            push_notification('task:stage', new_stage=str(new_stage), old_stage=str(old_stage))
        else:
            push_notification(self.datasource, updated=1)
        updated = copy(original)
        updated.update(updates)

        if self.__is_assigned_to_a_desk(updated):

            if self.__is_content_assigned_to_new_desk(original, updates):
                insert_into_versions(original['_id'])

            add_activity(ACTIVITY_UPDATE, 'updated task {{ subject }} for item {{ type }}',
                         item=updated, subject=get_subject(updated), type=updated['type'])

    def on_deleted(self, doc):
        push_notification(self.datasource, deleted=1)

    def assign_user(self, item_id, user):
        return self.patch(item_id, {'task': user})

superdesk.privilege(name='tasks',
                    label='Tasks Management',
                    description='User can manage tasks.')

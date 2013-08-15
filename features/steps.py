import base64

from lettuce import *
from flask import json

from app import app

def create_user(data):
    with app.test_request_context():
        world.user = data
        app.data.driver.db.users.insert(data)
        world.user.pop('_id', None)
        return world.user

def get_auth_token():
    response = world.app.post('/auth', data=json.dumps(world.user), headers=[('Content-Type', 'application/json')])
    data = json.loads(response.get_data())
    return data.get('token')

@before.all
def setup_all():
    app.config['TESTING'] = True
    world.app = app.test_client()

@before.each_scenario
def setup(scenario):
    world.headers = []

@after.each_scenario
def teardown(scenario):
    with app.test_request_context():
        app.data.driver.db.users.drop()
        app.data.driver.db.items.drop()

@step('I have no credentials')
def have_no_credentials(step):
    world.credentials = {}

@step('I have valid credentials')
def have_user_credentials(step):
    world.credentials = {
        'username': 'test',
        'password': 'test',
    }

    create_user(world.credentials)

@step('I have bad username')
def have_non_existing_username(step):
    world.credentials = {
        'username': 'nonexistingone',
    }

@step('I have bad password')
def have_bad_password(step):
    world.credentials = {
        'username': 'test',
        'password': 'test',
    }

    create_user(world.credentials)
    world.credentials['password'] = 'notest'

@step('I send auth request')
def send_auth_request(step):
    world.credentials.pop('_id', None)
    world.response = world.app.post('/auth', data=json.dumps(world.credentials), headers=[('Content-Type', 'application/json')])

@step('I get status code (\d+)')
def get_response_with_code(step, expected_code):
    expected_code = int(expected_code)
    assert world.response.status_code == expected_code, world.response.get_data() 

@step('I get "([_a-z]+)" in data')
def get_something_in_data(step, expected_key):
    expected_key = str(expected_key)
    assert expected_key in world.response.get_data(), \
        "Got %s" % world.response.get_data()

@step('I get valid auth_token')
def get_valid_auth_token(step):
    data = json.loads(world.response.get_data())
    assert 'token' in data, \
        "Got %s" % data.get('auth_token')

@step('I have no token')
def have_no_token(step):
    world.headers = []

@step('I have auth token')
def get_token(step):
    create_user({'username': 'test', 'password': 'test'})
    token = get_auth_token()
    world.headers = []
    world.headers.append(('Authorization', 'Basic %s' % (base64.b64encode(token + ':'))))

@step('I get "([-a-z0-9_/]+)"')
def get_url(step, url):
    world.response = world.app.get(url, headers=world.headers)

@step('I get empty list')
def get_empty_list(step):
    data = json.loads(world.response.get_data())
    assert len(data.get('_items')) == 0, \
        "Got %s" % world.response.get_data()

@step('I post item')
def post_item(step):
    data = {
        'item': {
            'guid': 'test',
            'headline': 'test',
        },
    }
    world.headers.append(('Content-Type', 'application/json'))
    world.response = world.app.post('/items/', headers=world.headers, data=json.dumps(data))

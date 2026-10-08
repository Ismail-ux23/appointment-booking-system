import importlib
from datetime import date, time, timedelta

import pytest


@pytest.fixture(scope='session')
def application(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        path = tmp_path_factory.mktemp('booking') / 'test.db'
        patch.setenv('DATABASE_URL', f'sqlite:///{path}')
        patch.setenv('SECRET_KEY', 'tests-only-secret')
        module = importlib.import_module('app')
        module.app.config['TESTING'] = True
        yield module
        with module.app.app_context():
            module.db.session.remove()
            module.db.engine.dispose()


@pytest.fixture
def setup(application):
    with application.app.app_context():
        application.db.drop_all()
        application.db.create_all()
        owner = application.User(email='provider@example.com', role='provider')
        learner = application.User(email='client@example.com', role='client')
        other = application.User(email='other@example.com', role='client')
        for user in [owner, learner, other]:
            user.set_password('a long test password')
        application.db.session.add_all([owner, learner, other])
        application.db.session.flush()
        provider = application.Provider(user_id=owner.id, business_name='Test Business')
        application.db.session.add(provider)
        application.db.session.flush()
        service = application.Service(provider_id=provider.id, name='Consultation', duration_minutes=30, price=10)
        day = date.today() + timedelta(days=1)
        application.db.session.add_all([service, application.Availability(
            provider_id=provider.id, day_of_week=day.weekday(), start_time=time(9), end_time=time(11),
        )])
        application.db.session.commit()
        yield {'owner':owner.id, 'client':learner.id, 'other':other.id,
               'provider':provider.id, 'service':service.id, 'day':day}
        application.db.session.remove()


@pytest.fixture
def client(application, setup):
    client = application.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = setup['client']
        session['role'] = 'client'
    return client


@pytest.fixture
def provider_client(application, setup):
    client = application.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = setup['owner']
        session['role'] = 'provider'
    return client

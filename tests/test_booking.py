from datetime import datetime, time, timedelta

import pytest


def book(client, setup, start):
    return client.post(f"/book/{setup['provider']}", data={
        'service_id':setup['service'], 'slot_start':start,
    })


@pytest.mark.parametrize('slot', ['08:00', '09:15', '11:00'])
def test_book_rejects_unoffered_times(client, application, setup, slot):
    response = book(client, setup, f"{setup['day'].isoformat()}T{slot}:00")
    assert response.status_code == 302
    assert application.Appointment.query.count() == 0
    assert application.AppointmentLog.query.count() == 0


@pytest.mark.parametrize('value', ['invalid', 'offset', 'past', 'too_far'])
def test_book_rejects_invalid_dates_and_offsets(client, application, setup, value):
    start = datetime.combine(setup['day'], time(9))
    values = {'invalid':'not-a-date', 'offset':start.isoformat()+'+05:00',
              'past':(start-timedelta(days=2)).isoformat(),
              'too_far':(start+timedelta(days=61)).isoformat()}
    assert book(client, setup, values[value]).status_code == 302
    assert application.Appointment.query.count() == 0


def test_valid_booking_and_sequential_conflict(client, application, setup):
    start = datetime.combine(setup['day'], time(9)).isoformat()
    assert book(client, setup, start).status_code == 302
    assert application.Appointment.query.count() == 1
    assert application.AppointmentLog.query.one().action == 'created'
    assert book(client, setup, start).status_code == 302
    assert application.Appointment.query.count() == 1
    assert application.AppointmentLog.query.count() == 1


def test_inactive_service_cannot_be_browsed_or_booked(client, application, setup):
    service = application.db.session.get(application.Service, setup['service'])
    service.is_active = False
    application.db.session.commit()
    response = client.get(f"/providers/{setup['provider']}?service_id={setup['service']}&date={setup['day']}")
    assert response.status_code == 200
    assert b'class="slot-btn"' not in response.data
    book(client, setup, datetime.combine(setup['day'], time(9)).isoformat())
    assert application.Appointment.query.count() == 0


@pytest.mark.parametrize('duration', ['0', '-5', '1441', 'bad'])
def test_invalid_service_durations_are_rejected(provider_client, application, duration):
    provider_client.post('/provider/services', data={
        'name':'Invalid service', 'duration_minutes':duration, 'price':'10',
    })
    assert application.Service.query.count() == 1


@pytest.mark.parametrize('price', ['-1', 'nan', 'inf', 'bad'])
def test_invalid_prices_are_rejected(provider_client, application, price):
    provider_client.post('/provider/services', data={
        'name':'Invalid service', 'duration_minutes':'30', 'price':price,
    })
    assert application.Service.query.count() == 1


@pytest.mark.parametrize('day', ['-1', '7', 'bad', ''])
def test_invalid_availability_days_are_rejected(provider_client, application, day):
    provider_client.post('/provider/availability', data={
        'day_of_week':day, 'start_time':'09:00', 'end_time':'10:00',
    })
    assert application.Availability.query.count() == 1


def test_slot_generation_handles_legacy_zero_duration(application, setup):
    provider = application.db.session.get(application.Provider, setup['provider'])
    service = application.db.session.get(application.Service, setup['service'])
    service.duration_minutes = 0
    assert application.get_available_slots(provider, service, setup['day']) == []


def test_overlapping_availability_does_not_duplicate_slots(application, setup):
    application.db.session.add(application.Availability(
        provider_id=setup['provider'], day_of_week=setup['day'].weekday(), start_time=time(9), end_time=time(11),
    ))
    application.db.session.commit()
    provider = application.db.session.get(application.Provider, setup['provider'])
    service = application.db.session.get(application.Service, setup['service'])
    slots = application.get_available_slots(provider, service, setup['day'])
    assert len(slots) == 4
    assert len(set(slots)) == 4


@pytest.mark.parametrize('slot', ['08:00', '09:15', '11:00'])
def test_reschedule_rejects_unoffered_times(client, application, setup, slot):
    original = datetime.combine(setup['day'], time(9))
    book(client, setup, original.isoformat())
    appointment = application.Appointment.query.one()
    client.post(f'/appointments/{appointment.id}/reschedule', data={
        'slot_start':f"{setup['day']}T{slot}:00",
    })
    application.db.session.expire_all()
    assert application.Appointment.query.one().start_time == original
    assert application.AppointmentLog.query.count() == 1


def test_reschedule_lists_own_slot_and_saves_valid_move(client, application, setup):
    book(client, setup, datetime.combine(setup['day'], time(9)).isoformat())
    appointment = application.Appointment.query.one()
    html = client.get(f'/appointments/{appointment.id}/reschedule?date={setup["day"]}').data
    assert f'{setup["day"]}T09:00:00'.encode() in html
    new_start = datetime.combine(setup['day'], time(9, 30))
    client.post(f'/appointments/{appointment.id}/reschedule', data={'slot_start':new_start.isoformat()})
    application.db.session.expire_all()
    assert application.Appointment.query.one().start_time == new_start
    assert application.AppointmentLog.query.count() == 2


def test_other_client_cannot_reschedule(client, application, setup):
    original = datetime.combine(setup['day'], time(9))
    book(client, setup, original.isoformat())
    appointment = application.Appointment.query.one()
    with client.session_transaction() as session:
        session['user_id'] = setup['other']
    client.post(f'/appointments/{appointment.id}/reschedule', data={
        'slot_start':datetime.combine(setup['day'], time(9, 30)).isoformat(),
    })
    application.db.session.expire_all()
    assert application.Appointment.query.one().start_time == original
    assert application.AppointmentLog.query.count() == 1

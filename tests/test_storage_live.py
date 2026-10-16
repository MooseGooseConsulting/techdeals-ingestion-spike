"""Run only against an explicitly supplied isolated PostgreSQL test database."""
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from techdeals_ingestion.contracts import (
    CaptureInput, EvidenceRef, Offer, ParseBatchResult, Price, UnitResult, WorkPackage,
)
from techdeals_ingestion.evidence import EvidenceIndex
from techdeals_ingestion.storage import Store


@pytest.fixture
def store():
    dsn = os.environ.get('TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('TEST_DATABASE_URL not supplied; requires an explicit isolated database')
    value = Store(dsn)
    value.migrate()
    return value


def test_retention_partial_publication_and_reuse(store, tmp_path):
    token = uuid4().hex
    path = tmp_path / 'raw'
    path.write_text('Server $42')
    def make(cid, acquisition, dated=False):
        capture = CaptureInput(capture_id=cid, acquisition_key=acquisition, path=path,
            media_type='text/plain', source_namespace='test',
            retrieved_at=datetime.now(timezone.utc) if dated else None,
            metadata={'identity_bindings': {'$': {'confirmed': True, 'native_id': token}}})
        return WorkPackage(package_id=cid, captures=[capture])
    package = make(token, token)
    index = EvidenceIndex(package.captures)
    store.retain_package(package)  # survives any later inference failure
    ref = EvidenceRef(capture_id=token, locator='$')
    result = ParseBatchResult(batch_id=token, units=[UnitResult(unit_id=token,
        input_refs=[ref], status='complete', offers=[Offer(local_id='offer', title='Server',
        evidence=[ref], prices=[Price(local_id='price', amount=Decimal('42'), currency=None,
        kind='ask', scope_refs=['offer'], evidence=[ref])])])])
    report = store.publish(package, result, index, token, 'a', token, parser_fingerprint='v1')
    assert report['published_units'] == 1
    assert len(store.query('undated_evidence', experiment_id=token, arm='a')) == 1
    assert not store.query('current_listings', experiment_id=token, arm='a')
    assert store.publish(package, result, index, token, 'a', token,
                         parser_fingerprint='v1') == report
    newer = make(token+'new', token+'new', dated=True)
    reused = store.reuse(newer, EvidenceIndex(newer.captures), token, 'a', 'v1')
    assert reused is not None
    assert reused.units[0].offers[0].evidence[0].capture_id == token+'new'
    store.publish(newer, reused, EvidenceIndex(newer.captures), token, 'a', token+'new',
                  parser_fingerprint='v1')
    assert len(store.query('current_listings', experiment_id=token, arm='a')) == 1
    assert store.query('current_prices', experiment_id=token, arm='a')[0]['amount'] == Decimal('42')
    assert len(store.query('listing_sightings', experiment_id=token, arm='a')) == 2
    assert store.reuse(newer, EvidenceIndex(newer.captures), token, 'b', 'v1') is None


def test_history_revisions_partial_failures_and_foreign_keys(store, tmp_path):
    import psycopg
    token = uuid4().hex
    path = tmp_path / 'listing'
    sightings = []
    for ordinal, (day, amount) in enumerate([(3, '10'), (1, '10'), (2, '20'), (4, '10')]):
        cid = f'{token}-{ordinal}'
        path.write_text(f'Native {token} ask {amount}')
        capture = CaptureInput(capture_id=cid, acquisition_key=cid, path=path,
            media_type='text/plain', source_namespace='test',
            retrieved_at=datetime(2026, 1, day, tzinfo=timezone.utc),
            metadata={'identity_bindings': {'$': {'confirmed': True, 'native_id': token}}})
        package = WorkPackage(package_id=cid, captures=[capture])
        ref = EvidenceRef(capture_id=cid, locator='$')
        result = ParseBatchResult(batch_id=cid, units=[UnitResult(unit_id=cid,
            input_refs=[ref], status='complete', offers=[Offer(local_id='offer', evidence=[ref],
            prices=[Price(local_id='price', amount=Decimal(amount), kind='ask',
                          scope_refs=['offer'], evidence=[ref])])])])
        store.publish(package, result, EvidenceIndex([capture]), token, 'a', cid,
                      parser_fingerprint='v1')
        sightings.append(store.query('listing_sightings', experiment_id=token, arm='a')[-1])
    assert len(store.query('listing_changes', experiment_id=token, arm='a')) == 3
    assert store.query('current_prices', experiment_id=token, arm='a')[0]['amount'] == Decimal('10')
    with store.connect() as conn:
        rows = conn.execute('SELECT result_id,observation_id FROM interpretations WHERE experiment_id=%s',
                            (token,)).fetchall()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute('INSERT INTO accepted VALUES (%s,%s,%s,%s)',
                             (rows[0]['observation_id'], token, 'invalid', rows[1]['result_id']))
    # A revision selects a new interpretation of the same retained acquisition.
    result.units[0].offers[0].title = 'revised interpretation'
    store.publish(package, result, EvidenceIndex([capture]), token, 'a', cid+'revision',
                  parser_fingerprint='v2')
    assert len(store.query('listing_sightings', experiment_id=token, arm='a')) == 4
    assert store.query('current_listings', experiment_id=token, arm='a')[0]['title'] == 'revised interpretation'

    # One invalid unit cannot suppress an independently valid sibling's partial evidence.
    second_path = tmp_path / 'second'
    second_path.write_text('Other entry')
    bad = capture.model_copy(update={'capture_id': token+'bad', 'acquisition_key': token+'bad',
                                     'path': second_path})
    combined = WorkPackage(package_id=token+'partial', captures=[capture, bad])
    bad_ref = EvidenceRef(capture_id=bad.capture_id, locator='$')
    malformed = UnitResult(unit_id=bad.capture_id, input_refs=[bad_ref], status='partial',
        offers=[Offer(local_id='bad', prices=[Price(local_id='bad-price', kind='ask',
                                                   scope_refs=['nonexistent'])])])
    combined_result = ParseBatchResult(batch_id=token+'partial', units=[result.units[0], malformed])
    report = store.publish(combined, combined_result, EvidenceIndex(combined.captures), token,
                           'partial', token+'partial', parser_fingerprint='v2')
    assert report['published_units'] == 1
    assert len(report['errors']) == 1
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM captures WHERE capture_id=%s',
                            (bad.capture_id,)).fetchone()['n'] == 1


def test_strong_constraints_savepoints_and_preparation_failures(store, tmp_path, monkeypatch):
    import psycopg
    token = uuid4().hex
    path = tmp_path / 'raw'
    path.write_text('entry')
    captures = [CaptureInput(capture_id=token+str(i), acquisition_key=token+str(i),
                path=path, media_type='text/plain') for i in range(2)]
    package = WorkPackage(package_id=token, captures=captures)
    units = [UnitResult(unit_id=c.capture_id,
             input_refs=[EvidenceRef(capture_id=c.capture_id, locator='$')], status='complete',
             offers=[Offer(local_id='offer', title=str(i))]) for i, c in enumerate(captures)]
    original = store._listing
    def fail_second(conn, offer, capture_map):
        if offer.title == '1':
            conn.execute('SELECT 1/0')  # Actual database failure, after the first unit succeeds.
        return original(conn, offer, capture_map)
    monkeypatch.setattr(store, '_listing', fail_second)
    report = store.publish(package, ParseBatchResult(batch_id=token, units=units),
                           EvidenceIndex(captures), token, 'a', token)
    assert report['published_units'] == 1 and len(report['errors']) == 1
    assert len(store.query('ingestion_failures', experiment_id=token, arm='a')) == 1
    with store.connect() as conn:
        row = conn.execute('SELECT * FROM interpretations WHERE experiment_id=%s', (token,)).fetchone()
        for experiment, arm in [(token, 'other'), (token+'other', 'a')]:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    conn.execute('INSERT INTO accepted VALUES (%s,%s,%s,%s)',
                                 (row['observation_id'], experiment, arm, row['result_id']))
    alias = captures[0].model_copy(update={'capture_id': token+'alias'})
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.retain_package(WorkPackage(package_id='alias', captures=[alias]))
    broken = captures[0].model_copy(update={'capture_id': token+'broken',
        'acquisition_key': token+'broken', 'media_type': 'application/json'})
    failure_package = WorkPackage(package_id=token+'broken', captures=[broken])
    first = store.record_failure(failure_package, token, 'prep', token+'prep', 'invalid JSON')
    assert store.record_failure(failure_package, token, 'prep', token+'prep', 'invalid JSON') == first
    assert len(store.query('ingestion_failures', experiment_id=token, arm='prep')) == 1
    with pytest.raises(ValueError, match='collision'):
        store.record_failure(failure_package, token, 'prep', token+'prep', 'different reason')


def test_equal_time_conflicts_and_selected_reuse(store, tmp_path):
    token = uuid4().hex
    date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path = tmp_path / 'raw'
    path.write_text('identical meaning')
    def publish(suffix, amount='10', status='complete'):
        cid = token+suffix
        c = CaptureInput(capture_id=cid, acquisition_key=cid, path=path,
            media_type='text/plain', source_namespace='test', retrieved_at=date,
            metadata={'identity_bindings': {'$': {'confirmed': True, 'native_id': token}}})
        p = WorkPackage(package_id=cid, captures=[c])
        r = EvidenceRef(capture_id=cid, locator='$')
        unit = UnitResult(unit_id=cid, input_refs=[r], status=status, offers=[Offer(
            local_id='offer', evidence=[r], prices=[Price(local_id='price', amount=Decimal(amount),
            kind='ask', scope_refs=['offer'], evidence=[r])])])
        store.publish(p, ParseBatchResult(batch_id=cid, units=[unit]), EvidenceIndex([c]),
                      token, 'a', cid, parser_fingerprint='v1')
        return p, unit
    p, unit = publish('1')
    publish('2')
    assert all(not row['equal_time_conflict'] for row in store.query('current_listings', experiment_id=token))
    publish('3', amount='20')
    assert all(row['equal_time_conflict'] for row in store.query('current_listings', experiment_id=token))
    assert not store.query('listing_changes', experiment_id=token)
    # Replacing a selection by partial makes that interpretation ineligible for reuse.
    for row in store.query('listing_sightings', experiment_id=token):
        with store.connect() as conn:
            conn.execute('DELETE FROM accepted WHERE result_id=%s', (row['result_id'],))
    unit.status = 'partial'
    store.publish(p, ParseBatchResult(batch_id=token+'partial', units=[unit]),
                  EvidenceIndex(p.captures), token, 'a', token+'partial', parser_fingerprint='v1')
    assert store.reuse(p, EvidenceIndex(p.captures), token, 'a', 'v1') is None

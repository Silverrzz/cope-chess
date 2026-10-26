from __future__ import annotations

from contextlib import ExitStack,contextmanager

from cope.application.errors import ApplicationError

from .authority_store import AuthorityStore


def control_row(connection):
    row = connection.execute('SELECT *,current_database() AS database FROM execution_control_state WHERE singleton FOR SHARE').fetchone()
    if row is None:
        raise ApplicationError('execution_control_unavailable','The canonical control registry is unavailable.',503)
    return row


def matching_record(row, record):
    return (row['control_incarnation']==record.incarnation and row['installation_id']==record.installation_id
        and row['journal_record']==record.id and row['journal_digest']==record.digest() and row['database']==record.target_database)


@contextmanager
def control_authority(connection, *, allow_held=False,allow_unconfigured=False,expected_incarnation=None,store=None):
    store = store or AuthorityStore.configured()
    if store is None:
        row = control_row(connection)
        if allow_unconfigured and row['control_incarnation'] is None and not row['credential_reset_pending']:
            yield row
            return
        raise ApplicationError('execution_authority_unavailable','Canonical authority needs its external bootstrap store.',503)
    with ExitStack() as stack:
        try:
            records = stack.enter_context(store.read())
        except (OSError,ValueError,ImportError) as error:
            raise ApplicationError('execution_authority_unavailable','The external control authority is unavailable or invalid.',503) from error
        row = control_row(connection)
        if not records or not matching_record(row,records[-1]):
            raise ApplicationError('execution_authority_mismatch','The database and external control authority require recovery synchronization.',503)
        if row['credential_reset_pending']:
            raise ApplicationError('execution_recovery_incomplete','Recovery must invalidate restored credentials before canonical admission resumes.',503)
        if expected_incarnation is not None and str(row['control_incarnation'])!=str(expected_incarnation):
            raise ApplicationError('execution_incarnation_changed','This execution authority belongs to a superseded control incarnation.',409)
        if row['recovery_hold'] and not allow_held:
            raise ApplicationError('execution_recovery_hold','New dispatch remains held until controller, target and capacity reconciliation finish.',503)
        yield row


def require_current_admission(connection, job, control):
    held = connection.execute('SELECT 1 FROM execution_recovery_subjects WHERE incarnation=%s AND job_id=%s',(control['control_incarnation'],job['id'])).fetchone()
    if job['admitted_incarnation']!=control['control_incarnation'] or held is not None:
        raise ApplicationError('execution_recovery_review_required','This work predates the current control authority and needs an explicit new decision.',409)


def authority_status(connection, *, store=None):
    store = store or AuthorityStore.configured()
    external = None
    status = 'unconfigured'
    if store is not None:
        try:
            with store.read() as records:
                row = control_row(connection)
                external = records[-1] if records else None
                status = 'mismatch' if external is None or not matching_record(row,external) else 'recovery_incomplete' if row['credential_reset_pending'] else 'held'
        except (OSError,ValueError,ImportError):
            row = control_row(connection)
            status = 'unavailable'
    else:
        row = control_row(connection)
        if row['control_incarnation'] is not None:
            status = 'unavailable'
    count = connection.execute('''SELECT count(*) AS count FROM (SELECT subject.job_id FROM execution_recovery_subjects subject
        JOIN execution_jobs job ON job.id=subject.job_id WHERE subject.incarnation=%s AND job.status NOT IN ('cancelled','succeeded','failed') LIMIT 1001) sample''',
        (row['control_incarnation'],)).fetchone()['count']
    roles = {role['role']:role['recovery_locked'] for role in connection.execute('SELECT role,recovery_locked FROM application_bootstrap_roles').fetchall()}
    return {'status':status,'configured':store is not None,'database_incarnation':str(row['control_incarnation']) if row['control_incarnation'] else None,
        'external_incarnation':str(external.incarnation) if external else None,'generation':row['generation'],'recovery_hold':row['recovery_hold'],
        'credential_reset_pending':row['credential_reset_pending'],'held_work':min(count,1000),'held_work_more':count>1000,
        'bootstrap_admin_locked':roles['admin'],'bootstrap_manager_locked':roles['manager'],
        'journal_digest':row['journal_digest'],'new_dispatch':'disabled'}

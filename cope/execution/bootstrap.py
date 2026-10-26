from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import psycopg
import sqlite3

from cope.application.errors import ApplicationError
from cope.application.identity import bootstrap_authority_lock,bootstrap_token_digest
from cope.db import connect_database

from .authority_store import AuthorityStore,AuthorityStoreError
from .control_authority import authority_status,matching_record
from .control_authority import control_authority


def sync_registry(connection, records):
    raw = connection.native_connection()
    latest = records[-1]
    current = raw.execute('SELECT *,current_database() AS database FROM execution_control_state WHERE singleton FOR UPDATE').fetchone()
    if current is None or current['database']!=latest.target_database or 'journal_digest' not in current:
        raise AuthorityStoreError('The authority store belongs to a different database target')
    if matching_record(current,latest):
        connection.commit()
        return
    outstanding = raw.execute("SELECT count(*) AS count FROM (SELECT id FROM execution_jobs WHERE status NOT IN ('cancelled','succeeded','failed') LIMIT 10001) pending").fetchone()['count']
    if outstanding>10000:
        raise AuthorityStoreError('Recovery requires a bounded work inventory before synchronization')
    for record in records:
        existing = raw.execute('SELECT installation_id,established_at FROM execution_incarnations WHERE id=%s',(record.incarnation,)).fetchone()
        if existing is None:
            raw.execute("INSERT INTO application_objects(id,type_key) VALUES (%s,'execution_incarnation')",(record.incarnation,))
            raw.execute('INSERT INTO execution_incarnations(id,installation_id,established_at) VALUES (%s,%s,%s)',(record.incarnation,record.installation_id,record.created_at))
        elif existing['installation_id']!=record.installation_id or existing['established_at']!=record.created_at:
            raise AuthorityStoreError('A control incarnation has conflicting retained provenance')
        installed = raw.execute('SELECT digest,body FROM execution_bootstrap_records WHERE id=%s',(record.id,)).fetchone()
        if installed is None:
            raw.execute('''INSERT INTO execution_bootstrap_records(id,installation_id,sequence,digest,incarnation,action,body,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)''',
                (record.id,record.installation_id,record.sequence,record.digest(),record.incarnation,record.action,record.model_dump_json(),record.created_at))
        elif installed['digest']!=record.digest() or installed['body']!=record.model_dump(mode='json'):
            raise AuthorityStoreError('A bootstrap receipt has conflicting retained provenance')
    raw.execute('''UPDATE execution_control_state SET control_incarnation=%s,installation_id=%s,journal_record=%s,journal_digest=%s,
        generation=generation+1,recovery_hold=TRUE,credential_reset_pending=%s,established_at=%s WHERE singleton''',
        (latest.incarnation,latest.installation_id,latest.id,latest.digest(),latest.action=='restore',latest.created_at))
    raw.execute('''INSERT INTO execution_recovery_subjects(incarnation,job_id,observed_status)
        SELECT %s,id,status FROM execution_jobs WHERE status NOT IN ('cancelled','succeeded','failed') ON CONFLICT DO NOTHING''',(latest.incarnation,))
    raw.execute("INSERT INTO application_outbox(object_id,kind,payload) VALUES (%s,'execution.control.held',jsonb_build_object('recovery_hold',TRUE))",(latest.incarnation,))
    connection.commit()


def reset_restored_credentials(connection, record):
    raw = connection.native_connection()
    current = raw.execute('SELECT *,current_database() AS database FROM execution_control_state WHERE singleton').fetchone()
    if not matching_record(current,record):
        raise AuthorityStoreError('Credential recovery requires the current external control incarnation')
    if not current['credential_reset_pending']:
        connection.commit()
        return
    bootstrap_authority_lock(raw,exclusive=True)
    raw.execute('UPDATE control_tokens SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL')
    raw.execute('''UPDATE application_bootstrap_roles SET generation=generation+1,
        recovery_locked=TRUE,sessions_valid_after=GREATEST(sessions_valid_after,floor(extract(epoch FROM clock_timestamp()))::bigint+1)''')
    raw.execute('UPDATE execution_control_state SET credential_reset_pending=FALSE,generation=generation+1 WHERE singleton AND journal_record=%s',(record.id,))
    connection.commit()


def transition(connection, store, action, *, database,reason=None,evidence=None,reader_gid=None):
    if action not in {'initialize','prepare-restore','synchronize'}:
        raise AuthorityStoreError('Unknown control bootstrap transition')
    if reader_gid is not None:
        store.initialize_directory(reader_gid)
    with store.writer():
        with store.exclusive():
            records = store.records()
            raw = connection.native_connection()
            exists = raw.execute("SELECT to_regclass('execution_control_state') AS present,current_database() AS database").fetchone()
            state = raw.execute('SELECT *,current_database() AS database FROM execution_control_state WHERE singleton FOR UPDATE').fetchone() if exists['present'] else {'database':exists['database'],'control_incarnation':None}
            if state is None or state['database']!=database:
                raise AuthorityStoreError('The selected bootstrap database does not match this connection')
            if records and records[-1].target_database!=database:
                raise AuthorityStoreError('This authority storage is bound to another database target')
            if action=='initialize':
                if 'journal_digest' not in state:
                    raise AuthorityStoreError('Apply the compatible control-recovery migrations before initialization')
                if records:
                    if not matching_record(state,records[-1]):
                        raise AuthorityStoreError('Existing authority needs explicit recovery synchronization')
                elif state['control_incarnation'] is not None:
                    raise AuthorityStoreError('An initialized database requires explicit restore recovery when its external store is missing')
                else:
                    records.append(store.append(records,database=database,action='initialize',reason=reason,evidence=None,previous_incarnation=None))
            elif action=='prepare-restore':
                records.append(store.append(records,database=database,action='restore',reason=reason,evidence=evidence,previous_incarnation=state['control_incarnation']))
            elif not records:
                raise AuthorityStoreError('Recovery synchronization requires an established external journal')
            if action!='prepare-restore':
                sync_registry(connection,records)
            else:
                connection.commit()
        if action!='prepare-restore':
            reset_restored_credentials(connection,records[-1])
        else:
            return {'status':'mismatch','recovery_phase':'sql_restore_pending','external_incarnation':str(records[-1].incarnation),'new_dispatch':'disabled'}
    return authority_status(connection.native_connection(),store=store)


def recover_admin(connection, store, secret_file):
    path = Path(secret_file).absolute()
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or parent.st_mode & 0o077 or path.is_relative_to(store.path.absolute()):
        raise AuthorityStoreError('Recovery keys require a separate owner-only directory')
    with store.writer():
        with control_authority(connection.native_connection(),allow_held=True,store=store) as control:
            if not path.exists():
                descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(descriptor,'wb') as stream:
                    stream.write((secrets.token_urlsafe(64)+'\n').encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                store._sync_directory(path.parent)
            descriptor = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(descriptor,'rb') as stream:
                mode = os.fstat(stream.fileno())
                if not stat.S_ISREG(mode.st_mode) or mode.st_uid!=os.geteuid() or mode.st_mode & 0o077:
                    raise AuthorityStoreError('Recovery key permissions are invalid')
                value = stream.read(258)
                token = value.decode('ascii').strip()
                if value!=(token+'\n').encode() or mode.st_size>129:
                    raise AuthorityStoreError('Recovery key encoding is invalid')
            if not 64<=len(token)<=128 or any(char not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for char in token):
                raise AuthorityStoreError('Recovery key format is invalid')
            raw = connection.native_connection()
            bootstrap_authority_lock(raw,exclusive=True)
            digest = bootstrap_token_digest(token)
            existing = raw.execute('SELECT token_digest FROM execution_credential_recoveries WHERE incarnation=%s',(control['control_incarnation'],)).fetchone()
            if existing is not None:
                if existing['token_digest']!=digest:
                    raise AuthorityStoreError('This incarnation already has a different recovered bootstrap key')
                connection.commit()
                return
            old = raw.execute('SELECT token FROM admin_access_tokens FOR SHARE').fetchall()
            if any(bootstrap_token_digest(row['token'])==digest for row in old if row['token']):
                raise AuthorityStoreError('A restored credential cannot become the recovery key')
            raw.execute('''INSERT INTO admin_access_tokens(role,token,rotated_at) VALUES ('admin',%s,clock_timestamp()::text)
                ON CONFLICT(role) DO UPDATE SET token=EXCLUDED.token,rotated_at=EXCLUDED.rotated_at''',(token,))
            raw.execute('INSERT INTO execution_credential_recoveries(incarnation,token_digest) VALUES (%s,%s)',(control['control_incarnation'],digest))
            raw.execute("INSERT INTO application_outbox(object_id,kind,payload) VALUES (%s,'execution.credentials.recovered',jsonb_build_object('bootstrap_admin_recovered',TRUE))",(control['control_incarnation'],))
        connection.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=('inspect','initialize','prepare-restore','synchronize','recover-admin'))
    parser.add_argument('--database',required=True)
    parser.add_argument('--directory')
    parser.add_argument('--reader-gid',type=int)
    parser.add_argument('--reason')
    parser.add_argument('--fence-evidence-digest')
    parser.add_argument('--output-secret-file',type=Path)
    args = parser.parse_args()
    if args.directory:
        os.environ['COPE_EXECUTION_AUTHORITY_DIR']=args.directory
    connection = connect_database()
    try:
        database = connection.native_connection().execute('SELECT current_database() AS name').fetchone()['name']
        connection.commit()
        if database!=args.database:
            raise AuthorityStoreError('The selected bootstrap database does not match this connection')
        store = AuthorityStore.configured()
        if args.action=='inspect':
            result = authority_status(connection.native_connection())
        elif store is None:
            raise AuthorityStoreError('Configure an external control-authority directory')
        elif args.action=='recover-admin':
            if args.output_secret_file is None:
                raise AuthorityStoreError('Choose a protected output file for the new recovery key')
            recover_admin(connection,store,args.output_secret_file)
            result = {'status':'held','bootstrap_admin_recovered':True,'new_dispatch':'disabled'}
        else:
            result = transition(connection,store,args.action,database=args.database,reason=args.reason,evidence=args.fence_evidence_digest,reader_gid=args.reader_gid)
        connection.commit()
        print(json.dumps(result,sort_keys=True))
        return 0
    except (OSError,ValueError,ApplicationError,psycopg.Error,sqlite3.Error):
        connection.rollback()
        print(json.dumps({'status':'unavailable','code':'execution_bootstrap_recovery_required','new_dispatch':'disabled'}))
        return 2
    finally:
        connection.close()


if __name__=='__main__':
    raise SystemExit(main())

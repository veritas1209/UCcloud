"""Standard-library guest runner. Input arrives on SSH stdin, never shell arguments."""
import base64
import fcntl
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
from pathlib import Path


def key_install(value):
    home = Path.home()
    directory = home / '.ssh'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError('Invalid SSH directory')
    os.chmod(directory, 0o700)
    # Directory flock serializes concurrent grants without creating a lock file.
    lock_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        destination = directory / 'authorized_keys'
        fd = os.open(destination, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 1024 * 1024:
                raise ValueError('Invalid authorized_keys')
            existing = stream.read()
            # Only replace previously installed UCcloud keys for this exact owner.
            suffix = (' uccloud-agent-' + value['owner']).encode()
            lines = [line for line in existing.splitlines() if not line.endswith(suffix)]
            lines.append(value['line'].encode())
            stream.seek(0)
            stream.write(b'\n'.join(lines) + b'\n')
            stream.truncate()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
    finally:
        os.close(lock_fd)
    return {'ok': True}


def parts(path, home=None):
    home = str(home or Path.home())
    if path == '~':
        path = '.'
    elif path.startswith('~/'):
        path = path[2:]
    elif path.startswith('/'):
        if path == home:
            path = '.'
        elif path.startswith(home + '/'):
            path = path[len(home) + 1:]
        else:
            raise ValueError('Only the VM home directory is available')
    values = [part for part in path.split('/') if part not in ('', '.')]
    if any(part in ('..', '.ssh', '.uccloud-agent-jobs') or '\x00' in part for part in values):
        raise ValueError('Invalid path')
    return values


def parent(path, create=False):
    values = parts(path)
    fd = os.open(Path.home(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in values[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, values[-1] if values else '.'
    except BaseException:
        os.close(fd)
        raise


def files(value):
    fd, name = parent(value['path'], value.get('parents', False))
    try:
        operation = value['operation']
        if operation == 'list':
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                # Bound work even for directories with millions of entries.
                result = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.name == '.ssh':
                            continue
                        info = entry.stat(follow_symlinks=False)
                        result.append({'name': entry.name, 'type': 'symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'file', 'size': info.st_size})
                        if len(result) >= 200:
                            return {'entries': result, 'truncated': True}
                return {'entries': result, 'truncated': False}
            finally:
                os.close(directory)
        flags = os.O_NOFOLLOW | os.O_NONBLOCK
        flags |= os.O_RDONLY if operation == 'read' else os.O_RDWR | os.O_CREAT
        file_fd = os.open(name, flags, mode=0o600, dir_fd=fd)
        with os.fdopen(file_fd, 'rb' if operation == 'read' else 'r+b') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Only regular files are available')
            offset = value.get('offset', 0)
            if operation == 'read':
                stream.seek(offset)
                data = stream.read(value.get('length', 65536))
                return {'data_base64': base64.b64encode(data).decode(), 'offset': offset, 'next_offset': offset + len(data), 'size': info.st_size, 'eof': offset + len(data) >= info.st_size}
            data = base64.b64decode(value['data_base64'], validate=True)
            if offset > info.st_size:
                raise ValueError('Sparse writes are not allowed')
            if value.get('truncate'):
                if offset != 0:
                    raise ValueError('Truncate requires offset zero')
                stream.truncate(0)
            stream.seek(offset)
            stream.write(data)
            stream.flush()
            return {'written': len(data), 'next_offset': stream.tell(), 'size': os.fstat(stream.fileno()).st_size}
    finally:
        os.close(fd)


def job_home(value):
    if value.get('sudo'):
        if os.getuid()!=0 or not re.fullmatch(r'[a-z_][a-z0-9_-]{1,31}',value.get('vm_username','')):
            raise ValueError('Invalid administrator context')
        account=pwd.getpwnam(value['vm_username'])
        if account.pw_uid==0:
            raise ValueError('Expected ordinary VM account')
        return Path(account.pw_dir)
    return Path.home()


def job_directory(value=None, create=True):
    if value and value.get('sudo'):
        job_home(value)
        if not re.fullmatch(r'[a-f0-9-]{36}',value.get('grant_id','')):
            raise ValueError('Invalid connection')
        directory=Path('/run/uccloud-agent-jobs')
        for component in (directory,directory/value['grant_id']):
            if create:
                component.mkdir(mode=0o700,exist_ok=True)
            info=component.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:
                raise ValueError('Invalid privileged job directory')
        return directory/value['grant_id']
    directory=Path.home()/'.uccloud-agent-jobs'
    if create:
        directory.mkdir(mode=0o700,exist_ok=True)
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Invalid job directory')
    return directory


def start_time(pid):
    return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]


def kill_group(pid):
    denied=False
    try:
        os.killpg(pid,signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        denied=True
    if os.getuid()!=0:
        # Attached sudo children can have root UID; honor the VM user's sudo policy.
        result=subprocess.run(['sudo','-n','/bin/kill','-KILL','--','-'+str(pid)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
        if denied and result.returncode:
            raise ValueError('Cannot cancel the privileged VM process')


def cancel_job(value):
    job_id=value['job_id']
    if not re.fullmatch(r'[a-f0-9]{64}',job_id):
        raise ValueError('Invalid job')
    try:
        fd=os.open(job_directory(value,create=False)/job_id,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        return {'stopped':True}
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_size>1024:
            raise ValueError('Invalid job record')
        record=json.loads(stream.read(1024))
    pid=record['pid']
    try:
        if type(pid)!=int or pid<=1 or os.getpgid(pid)!=pid or start_time(pid)!=record['start_time']:
            raise ValueError('Job process changed')
        kill_group(pid)
    except (FileNotFoundError,ProcessLookupError):
        pass
    return {'stopped':True}


def execute(value):
    home=job_home(value)
    cwd = home.joinpath(*parts(value.get('cwd', '.'),home)).resolve(strict=True)
    if not cwd.is_relative_to(home.resolve()) or not cwd.is_dir():
        raise ValueError('Invalid working directory')
    job_id=value.get('job_id')
    record=None
    if job_id:
        if not re.fullmatch(r'[a-f0-9]{64}',job_id):
            raise ValueError('Invalid job')
        record=job_directory(value)/job_id
        if record.exists():
            raise ValueError('Job record already exists')
    process = subprocess.Popen(['/bin/bash', '-lc', value['command']], cwd=cwd, stdin=subprocess.DEVNULL, start_new_session=True)
    if record:
        try:
            with os.fdopen(os.open(record,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w') as output:
                json.dump({'pid':process.pid,'start_time':start_time(process.pid)},output)
        except FileNotFoundError:
            pass # Very short commands may exit before /proc is inspected.
        except BaseException:
            kill_group(process.pid);process.wait();raise
    def stop(signum, frame):
        try:
            kill_group(process.pid)
        except ProcessLookupError:
            pass
        process.wait()
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        code = process.wait(timeout=value.get('timeout_seconds',1800))
    except subprocess.TimeoutExpired:
        stop(signal.SIGTERM,None)
    finally:
        if record:
            record.unlink(missing_ok=True)
            if value.get('sudo'):
                try:
                    record.parent.rmdir()
                except OSError:
                    pass # Concurrent jobs may still use this root-owned namespace.
    raise SystemExit(code if code >= 0 else 128 - code)


def main():
    if '--sudo-runner' in sys.argv:
        value=None
        # Passwordless sudo may leave the password line on stdin. Never echo it.
        for _ in range(2):
            try:
                candidate=json.loads(sys.stdin.readline(32768))
                if isinstance(candidate,dict) and candidate.get('protocol')=='uccloud-agent-v1' and candidate.get('sudo') is True:
                    value=candidate
                    break
            except (ValueError,TypeError):
                pass
        if value is None or os.getuid()!=0 or value.get('operation') not in ('sudo_check','exec','cancel'):
            raise SystemExit(1)
        job_home(value)
        if value['operation']=='sudo_check':
            print(json.dumps({'uid':os.getuid()}))
            return
    else:
        value = json.loads(sys.stdin.readline(32768))
        if value.get('sudo'):
            raise SystemExit(1)
    if value['operation'] == 'exec':
        execute(value)
    if value['operation'] == 'context':
        print(json.dumps({'home': str(Path.home()), 'user': os.environ.get('USER'), 'python': sys.version.split()[0]}))
        return
    try:
        result = key_install(value) if value['operation'] == 'install_key' else cancel_job(value) if value['operation']=='cancel' else files(value)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError):
        print(json.dumps({'error': '파일 경로·권한을 확인해 주세요. 바로가기와 특수 파일은 사용할 수 없습니다.'}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()

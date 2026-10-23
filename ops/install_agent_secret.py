"""Root-only master key creation; no credential is printed or overwritten."""
import os
import stat
from pathlib import Path
from cryptography.fernet import Fernet

if os.getuid()!=0:
    raise SystemExit('Root required')
path=Path('/etc/cloud-portal/agent-master.key')
try:
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
except FileExistsError:
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:
        raise SystemExit('Unexpected master key permissions; no changes')
    Fernet(path.read_bytes().strip())
else:
    with os.fdopen(fd,'wb') as output:
        output.write(Fernet.generate_key()+b'\n');output.flush();os.fsync(output.fileno())
print('Agent master credential ready (root-only; not displayed)')

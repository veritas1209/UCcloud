"""Real AsyncSSH/SFTP loopback test; no production VM or credentials."""
import asyncio
import base64
from pathlib import Path
import tempfile
import asyncssh
from backend.guest_files import Uploads, valid_name, FILE_LIMIT, CHUNK_LIMIT

for name in ('../a', 'a/b', 'a\\b', '.', '..', '', 'a\n', '.uccloud-upload-x', 'a'*256):
    assert not valid_name(name), name
assert valid_name('보고서 <script>.txt')

class TestServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False

async def run():
    with tempfile.TemporaryDirectory(prefix='uccloud-sftp-test-') as directory:
        key = asyncssh.generate_private_key('ssh-ed25519')
        server = await asyncssh.create_server(TestServer, '127.0.0.1', 0,
            server_host_keys=[key], sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=directory))
        try:
            port = server.get_port()
            known_hosts = f'[127.0.0.1]:{port} ' + key.export_public_key().decode()
            async with asyncssh.connect('127.0.0.1', port=port, username='test', known_hosts=known_hosts.encode()) as connection:
                uploads = Uploads(connection)
                async def reject(message):
                    try:
                        await uploads.handle(message)
                    except (ValueError, asyncssh.SFTPError):
                        await uploads.cancel()
                    else:
                        raise AssertionError('Unsafe upload accepted')
                await reject({'type':'upload.begin','name':'../outside','size':1})
                await reject({'type':'upload.begin','name':'bad','size':True})
                await reject({'type':'upload.begin','name':'bad','size':FILE_LIMIT+1})
                payload = bytes(range(256))*300
                await uploads.handle({'type':'upload.begin','name':'한글.bin','size':len(payload)})
                for offset in range(0,len(payload),CHUNK_LIMIT):
                    await uploads.handle({'type':'upload.chunk','data':base64.b64encode(payload[offset:offset+CHUNK_LIMIT]).decode()})
                done = await uploads.handle({'type':'upload.finish'})
                assert done['path']=='~/uploads/한글.bin'
                saved = Path(directory)/'uploads'/'한글.bin'
                assert saved.read_bytes()==payload and saved.stat().st_mode & 0o777 == 0o600
                await reject({'type':'upload.begin','name':'한글.bin','size':0})
                assert saved.read_bytes()==payload
                await uploads.handle({'type':'upload.begin','name':'partial','size':5})
                await reject({'type':'upload.chunk','data':'!!!'})
                assert not (Path(directory)/'uploads'/'partial').exists()
                assert not list((Path(directory)/'uploads').glob('.uccloud-upload-*'))
                await uploads.handle({'type':'upload.begin','name':'empty','size':0})
                await uploads.handle({'type':'upload.finish'})
                assert (Path(directory)/'uploads'/'empty').read_bytes()==b''
                await uploads.handle({'type':'upload.begin','name':'race','size':0})
                (Path(directory)/'uploads'/'race').write_text('original')
                await reject({'type':'upload.finish'})
                assert (Path(directory)/'uploads'/'race').read_text()=='original'
                await uploads.handle({'type':'upload.begin','name':'cancelled','size':10})
                await uploads.close()
                assert not list((Path(directory)/'uploads').glob('.uccloud-upload-*'))
        finally:
            server.close()
            await server.wait_closed()
asyncio.run(run())
print('Real SFTP upload, chunking, bounds, no-overwrite and cancellation passed')

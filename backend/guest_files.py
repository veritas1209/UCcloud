"""Bounded SFTP uploads over the authenticated VM connection."""
import base64
import binascii
import posixpath
import stat
from uuid import uuid4

import asyncssh

FILE_LIMIT = 100 * 1024 * 1024
SESSION_LIMIT = 500 * 1024 * 1024
CHUNK_LIMIT = 32768


def valid_name(value):
    return (isinstance(value, str) and value not in ('', '.', '..')
            and not value.startswith('.uccloud-upload-')
            and len(value.encode('utf-8')) <= 255
            and not any(c in value for c in '/\\')
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


class Uploads:
    def __init__(self, connection):
        self.connection = connection
        self.sftp = None
        self.active = None
        self.total = 0
        self.count = 0

    async def directory(self):
        if self.sftp is None:
            self.sftp = await self.connection.start_sftp_client()
        home = await self.sftp.realpath('.')
        directory = posixpath.join(home, 'uploads')
        try:
            await self.sftp.mkdir(directory, attrs=asyncssh.SFTPAttrs(permissions=0o700))
        except asyncssh.SFTPError:
            pass
        info = await self.sftp.lstat(directory)
        if info.permissions is None or not stat.S_ISDIR(info.permissions):
            raise ValueError('uploads 폴더를 확인해 주세요. 바로가기에는 파일을 올릴 수 없습니다.')
        if await self.sftp.realpath(directory) != directory:
            raise ValueError('uploads 폴더를 확인해 주세요.')
        return directory

    async def handle(self, message):
        op = message.get('type')
        if op == 'upload.begin':
            if self.active:
                raise ValueError('현재 파일 전송이 끝날 때까지 기다려 주세요.')
            name, size = message.get('name'), message.get('size')
            if not valid_name(name) or type(size) is not int or not 0 <= size <= FILE_LIMIT:
                raise ValueError('파일 이름을 확인해 주세요. 파일 하나는 100MB까지 올릴 수 있습니다.')
            if self.total + size > SESSION_LIMIT:
                raise ValueError('한 번 접속해 올릴 수 있는 용량은 500MB입니다. 다시 접속해 주세요.')
            if self.count >= 200:
                raise ValueError('한 번 접속해 파일 200개까지 올릴 수 있습니다. 파일이 많으면 압축해서 올려 주세요.')
            directory = await self.directory()
            destination = posixpath.join(directory, name)
            try:
                await self.sftp.lstat(destination)
            except asyncssh.SFTPNoSuchFile:
                pass
            else:
                raise ValueError('같은 이름의 파일이 있습니다. 파일 이름을 바꾼 뒤 다시 올려 주세요.')
            temp = posixpath.join(directory, '.uccloud-upload-' + uuid4().hex)
            stream = await self.sftp.open(temp, 'xb', attrs=asyncssh.SFTPAttrs(permissions=0o600))
            self.active = dict(temp=temp, destination=destination, stream=stream, size=size, received=0, name=name)
            self.total += size
            self.count += 1
            return {'state':'started', 'received':0}
        if op == 'upload.cancel':
            await self.cancel()
            return {'state':'cancelled'}
        item = self.active
        if not item:
            raise ValueError('파일을 다시 선택해 주세요.')
        if op == 'upload.chunk':
            raw = message.get('data')
            if not isinstance(raw, str) or len(raw) > 43692:
                raise ValueError('파일 전송 내용을 확인해 주세요.')
            try:
                chunk = base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError('파일 전송 내용을 확인해 주세요.') from None
            if not 0 < len(chunk) <= CHUNK_LIMIT or item['received'] + len(chunk) > item['size']:
                raise ValueError('파일 크기가 맞지 않습니다. 다시 올려 주세요.')
            await item['stream'].write(chunk)
            item['received'] += len(chunk)
            return {'state':'progress', 'received':item['received']}
        if op == 'upload.finish':
            if item['received'] != item['size']:
                raise ValueError('파일 전송이 아직 끝나지 않았습니다.')
            await item['stream'].close()
            item['stream'] = None
            # Atomic hardlink publication never overwrites a file or destination symlink.
            await self.sftp.link(item['temp'], item['destination'])
            result = {'state':'complete', 'name':item['name'], 'path':'~/uploads/' + item['name'], 'size':item['size']}
            await self.sftp.remove(item['temp'])
            self.active = None
            return result
        raise ValueError('파일 전송 요청을 확인해 주세요.')

    async def cancel(self):
        item, self.active = self.active, None
        if item:
            try:
                if item['stream']:
                    await item['stream'].close()
            finally:
                try:
                    await self.sftp.remove(item['temp'])
                except asyncssh.SFTPError:
                    pass

    async def close(self):
        try:
            await self.cancel()
        finally:
            if self.sftp:
                self.sftp.exit()
                await self.sftp.wait_closed()

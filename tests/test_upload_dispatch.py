"""Upload errors stay separate from shell I/O; revoked sessions cannot upload."""
import asyncio
import json
from backend.terminal import _receive_input

class Done(Exception):
    pass

class Socket:
    def __init__(self):
        self.messages = iter([json.dumps({'type':'upload.begin', 'request_id':'11111111-1111-4111-8111-111111111111'})])
        self.sent = []
    async def receive_text(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise Done()
    async def send_json(self, message):
        self.sent.append(message)

class Files:
    def __init__(self):
        self.called = 0
        self.cancelled = 0
    async def handle(self, message):
        self.called += 1
        raise ValueError('같은 이름의 파일이 있습니다.')
    async def cancel(self):
        self.cancelled += 1

async def run():
    for allowed in (True, False):
        socket, files = Socket(), Files()
        try:
            await _receive_input(socket, None, files, lambda:allowed)
        except Done:
            pass
        assert files.called == int(allowed)
        assert files.cancelled == 1
        assert len(socket.sent) == 1
        assert socket.sent[0]['type'] == 'upload' and socket.sent[0]['state'] == 'error'
        assert ('같은 이름' if allowed else '로그인이 만료') in socket.sent[0]['message']
asyncio.run(run())
print('Upload/session guards and isolated error feedback passed')

"""Guest status failures must not leave a fictitious installation running."""
import asyncio
import json
from types import SimpleNamespace
from backend import terminal


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, value):
        self.messages.append(value)


class SSH:
    def __init__(self, payload):
        self.payload = payload

    async def run(self, *args, **kwargs):
        if isinstance(self.payload, Exception):
            raise self.payload
        return SimpleNamespace(stdout=self.payload)


async def check():
    original_sleep = terminal.asyncio.sleep
    async def no_wait(seconds):
        pass
    terminal.asyncio.sleep = no_wait
    try:
        for payload in ('', 'not-json', '[]', '{"state":"invalid","message":"x"}',
                        '{"state":"ready","message":42}', OSError('private-error')):
            socket = Socket()
            await terminal._watch_software(socket, SSH(payload))
            assert len(socket.messages) == 1
            assert socket.messages[0]['state'] == 'unknown'
            assert 'started_at' not in socket.messages[0]
            assert 'private-error' not in json.dumps(socket.messages)

        socket = Socket()
        await terminal._watch_software(socket, SSH(json.dumps({
            'state': 'ready', 'message': '완료', 'started_at': 1780000000,
            'updated_at': 1780000125, 'password': 'must-not-forward'})))
        assert socket.messages == [dict(type='software', state='ready', message='완료',
                                       started_at=1780000000, updated_at=1780000125)]

        socket = Socket()
        await terminal._watch_software(socket, SSH(json.dumps({
            'state': 'installing', 'message': '설치 중', 'started_at': 1780000000})))
        assert [message['state'] for message in socket.messages] == ['installing', 'unknown']
    finally:
        terminal.asyncio.sleep = original_sleep


asyncio.run(check())
print('Actual ready state, absent/invalid/unreadable status and exhausted polling passed')

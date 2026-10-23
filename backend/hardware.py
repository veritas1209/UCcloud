"""Unprivileged client; no iLO secrets, arbitrary hosts or root shell access."""
import json
import socket

SOCKET = '/run/uccloud-fans/control.sock'
MESSAGES = {
    'invalid': '서버와 최소 속도를 확인해 주세요.',
    'busy': '다른 설정을 처리 중입니다. 잠시 후 다시 확인해 주세요.',
    'conflict': '설정이 바뀌었습니다. 새로고침 후 다시 선택해 주세요.',
    'warning': '온도 또는 팬 경고가 있어 설정 변경을 보류했습니다.',
    'unsupported': '이 서버에서 해당 설정을 사용할 수 없습니다.',
    'permission': '서버 관리 연결 권한을 확인해야 합니다.',
    'configuration': '팬 관리 연결이 아직 설정되지 않았습니다.',
    'certificate': '서버 인증서가 바뀌었습니다. 운영 확인이 필요합니다.',
    'uncertain': '변경 결과를 확인하지 못했습니다. 새로고침으로 현재 설정을 확인하세요.',
    'unavailable': '서버 관리 연결에 실패했습니다. 잠시 후 새로고침해 주세요.',
}

class HardwareError(Exception):
    def __init__(self, code):
        self.code = code if code in MESSAGES else 'unavailable'
        super().__init__(MESSAGES[self.code])

def request(payload):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(60)
            client.connect(SOCKET)
            client.sendall((json.dumps(payload)+'\n').encode())
            with client.makefile('rb') as stream:
                raw = stream.readline(65537)
            if len(raw) > 65536 or not raw.endswith(b'\n'):
                raise HardwareError('unavailable')
            result = json.loads(raw)
    except HardwareError:
        raise
    except Exception:
        raise HardwareError('uncertain' if payload.get('op') == 'set' else 'unavailable') from None
    if not isinstance(result, dict):
        raise HardwareError('unavailable')
    if 'error' in result:
        raise HardwareError(result['error'])
    return result

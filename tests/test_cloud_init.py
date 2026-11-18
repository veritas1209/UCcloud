"""Exercise the real installer payload, decoding and Nova's encoded size limit."""
import base64
import gzip
import json
import secrets
import unittest

from backend.cloud_init import MAX_USER_DATA_LENGTH, encode_cloud_config
from backend.software import SOFTWARE, software_files


class CloudInitTests(unittest.TestCase):
    def test_full_catalog_round_trip_fits_nova(self):
        selections = [dict(id=i['id'], version=i['versions'][0]) for i in SOFTWARE]
        config = {'write_files': software_files('student', selections),
                  'runcmd': [['systemctl', 'start', '--no-block', 'uccloud-software-install.service']]}
        encoded = encode_cloud_config(config)
        self.assertLessEqual(len(encoded), MAX_USER_DATA_LENGTH)
        raw = gzip.decompress(base64.b64decode(encoded))
        self.assertTrue(raw.startswith(b'#cloud-config\n'))
        self.assertEqual(json.loads(raw.split(b'\n', 1)[1]), config)

    def test_unicode_and_credentials_preserved(self):
        config = {'message': '설치 완료', 'credentials': "한글$'\\password"}
        decoded = gzip.decompress(base64.b64decode(encode_cloud_config(config)))
        self.assertEqual(json.loads(decoded.split(b'\n', 1)[1]), config)

    def test_incompressible_oversize_rejected_before_api(self):
        with self.assertRaisesRegex(ValueError, '초기 설정 데이터가 너무 큽니다'):
            encode_cloud_config({'content': secrets.token_hex(100000)})


if __name__ == '__main__':
    unittest.main()

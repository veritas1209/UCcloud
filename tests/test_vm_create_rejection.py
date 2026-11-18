"""Nova rejections must not leak the echoed initialization data into responses/logs."""
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openstack.exceptions import BadRequestException
from backend import openstack_service as cloud


class CreateRejectionTests(unittest.TestCase):
    def test_safe_rejection_messages(self):
        for field, expected in [('user_data', '초기 설정'), ('flavorRef', '운영체제와 사양')]:
            conn = MagicMock()
            conn.image.get_image.return_value = SimpleNamespace(id='image', name=next(iter(cloud.ALLOWED_IMAGES)), status='active')
            conn.compute.get_flavor.return_value = SimpleNamespace(id='flavor', name=next(iter(cloud.ALLOWED_FLAVORS)), ram=8192, vcpus=2)
            conn.network.get_network.return_value = SimpleNamespace(id='network', project_id='project')
            conn.compute.create_server.side_effect = BadRequestException(f'{field}: private-bootstrap-value')
            quota = {k: {'limit': -1, 'in_use': 0} for k in ['instances', 'cores', 'ram']}
            with patch.object(cloud, 'team_connection', return_value=conn), patch.object(cloud, 'quota', return_value=quota), patch.object(cloud, '_vm_user_data', return_value='bootstrap'):
                with self.assertRaises(ValueError) as caught:
                    cloud.create_server('team', 'project', 'student', 'network', 'test', 'image', 'flavor', 'student', 'test-password')
            self.assertNotIn('private-bootstrap-value', str(caught.exception))
            formatted = ''.join(traceback.format_exception(caught.exception))
            self.assertNotIn('private-bootstrap-value', formatted)
            self.assertIn(expected, str(caught.exception))


if __name__ == '__main__':
    unittest.main()

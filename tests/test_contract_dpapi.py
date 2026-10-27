"""test_contract_dpapi.py — WP7 DPAPI 密钥加固单测。"""

import os
import shutil
import sys
import tempfile
import unittest

_plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_scripts_dir = os.path.join(_plugin_root, "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import contract


class TestBrokerKeyDPAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cc-dpapi-test-")
        self.fake_key_file = os.path.join(self.temp_dir, "broker.key")
        self.orig_rel = contract._BROKER_KEY_REL
        self.orig_env = os.environ.get(contract._BROKER_KEY_ENV)
        contract._BROKER_KEY_REL = self.fake_key_file
        if contract._BROKER_KEY_ENV in os.environ:
            del os.environ[contract._BROKER_KEY_ENV]

    def tearDown(self):
        contract._BROKER_KEY_REL = self.orig_rel
        if self.orig_env is not None:
            os.environ[contract._BROKER_KEY_ENV] = self.orig_env
        else:
            os.environ.pop(contract._BROKER_KEY_ENV, None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dpapi_roundtrip_helpers(self):
        """测试 DPAPI protect / unprotect 往返"""
        if sys.platform != "win32":
            self.skipTest("Windows-only DPAPI test")
        raw = b"my-super-secret-key-12345"
        protected = contract._dpapi_protect(raw)
        self.assertNotEqual(protected, raw)
        unprotected = contract._dpapi_unprotect(protected)
        self.assertEqual(unprotected, raw)

    def test_broker_key_generation_creates_dpapi(self):
        """测试生成新密钥时创建 .dpapi 和明文文件"""
        if sys.platform != "win32":
            self.skipTest("Windows-only DPAPI test")
        key = contract.broker_key()
        self.assertTrue(isinstance(key, bytes))
        self.assertTrue(len(key) > 0)
        dpapi_file = self.fake_key_file + ".dpapi"
        self.assertTrue(os.path.isfile(dpapi_file))
        self.assertTrue(os.path.isfile(self.fake_key_file))

    def test_broker_key_lazy_migration(self):
        """测试已存在明文密钥时的惰性迁移"""
        if sys.platform != "win32":
            self.skipTest("Windows-only DPAPI test")
        plaintext = b"legacy-plaintext-key-abc"
        with open(self.fake_key_file, "wb") as f:
            f.write(plaintext)
        dpapi_file = self.fake_key_file + ".dpapi"
        self.assertFalse(os.path.isfile(dpapi_file))

        # 读取触发惰性迁移
        key = contract.broker_key()
        self.assertEqual(key, plaintext)
        self.assertTrue(os.path.isfile(dpapi_file))
        # 验证 dpapi 文件可正常解出相同内容
        with open(dpapi_file, "rb") as f:
            enc = f.read().strip()
        self.assertEqual(contract._dpapi_unprotect(enc).strip(), plaintext)


if __name__ == "__main__":
    unittest.main()

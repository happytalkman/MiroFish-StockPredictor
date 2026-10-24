import logging
import os
import shutil
import tempfile
import unittest

os.environ['WEAIDSTOCK_TESTING'] = '1'
logging.disable(logging.CRITICAL)      # 테스트 출력은 결과만 (실패 원인은 assert 메시지로)

from app.config import Config  # noqa: E402


class TempUploadCase(unittest.TestCase):
    """테스트마다 빈 임시 uploads 폴더를 씀 (실제 데이터 보호)"""

    def setUp(self):
        self._orig_upload = Config.UPLOAD_FOLDER
        self.tmp = tempfile.mkdtemp(prefix='weaid_test_')
        Config.UPLOAD_FOLDER = self.tmp
        try:
            from app.services import prediction_ledger
            prediction_ledger._PARAMS['at'] = 0.0
            prediction_ledger._PARAMS['data'] = None
        except Exception:
            pass
        try:
            from app.services import ontology_store
            ontology_store._GRAPH.update(mtime=0, g=None)
        except Exception:
            pass

    def tearDown(self):
        Config.UPLOAD_FOLDER = self._orig_upload
        shutil.rmtree(self.tmp, ignore_errors=True)

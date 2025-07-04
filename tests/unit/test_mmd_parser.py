
from tests.common.test_base import TestBase


class TestMmdParser(TestBase):

    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

    def _create_dummy_pmd_file(self, magic=b'Pmd', version=1.0, model_name='TestModel', comment='TestComment'):
        pass

    def _create_dummy_pmx_file(self, magic=b'PMX ', version=2.0, global_flags=0, model_name_jp='TestModelJP', model_name_en='TestModelEN', comment_jp='TestCommentJP', comment_en='TestCommentEN'):
        pass

    def _create_dummy_vmd_file(self, magic=b'Vocaloid Motion Data file', version=2.0, model_name='TestModel'):
        pass

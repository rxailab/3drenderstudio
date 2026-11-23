"""The transport split: ai_transport stays free of domain modules, and ai_client shares its very objects."""
import ast
import unittest
from pathlib import Path

import ai_client
import ai_transport

ROOT = Path(__file__).resolve().parents[1]


class TransportSplitTests(unittest.TestCase):
    def test_transport_imports_no_project_domain_module(self):
        # Function-level imports count too: they are what tie modules into one import cycle.
        names = set()
        for node in ast.walk(ast.parse((ROOT / 'ai_transport.py').read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import): names |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module: names.add(node.module.split('.')[0])
        project = {path.stem for path in ROOT.glob('*.py')}
        self.assertEqual(names & project, {'ai_model_policy', 'studio_store'})

    def test_client_reexports_the_same_objects(self):
        # tasks.py tags threads with ai_client.for_project and log_usage reads ai_transport.PROJECT: one variable, not two.
        for name in ('PROJECT', 'CALLER', 'for_project', 'in_project', 'tagged', 'tally', 'calling', 'log_usage', '_post', '_payload',
                     '_response', '_json', '_image', '_check_schema', 'SchemaValidationError', 'NoRedirect', 'endpoint', 'TOOL_NAME'):
            self.assertIs(getattr(ai_client, name), getattr(ai_transport, name), name)
        self.assertTrue(issubclass(ai_client.ResponseContractError, ai_transport.SchemaValidationError))
        with ai_client.for_project('p9'):
            self.assertEqual(ai_transport.PROJECT.get(), 'p9')

    def test_usage_dir_is_read_live_through_the_client(self):
        # listing.py sums a project's spend from ai_client.USAGE_DIR, so it must follow the setting log_usage uses.
        saved = ai_transport.USAGE_DIR
        try:
            ai_transport.USAGE_DIR = '/somewhere/else'
            self.assertEqual(ai_client.USAGE_DIR, '/somewhere/else')
        finally:
            ai_transport.USAGE_DIR = saved


if __name__ == '__main__':
    unittest.main()

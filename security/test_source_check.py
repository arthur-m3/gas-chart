"""Security regressions use only the standard library and never run payloads."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CHECK = Path(__file__).with_name('check_source.py')

class SourceCheckTests(unittest.TestCase):
    def run_check(self, files, env=None):
        with tempfile.TemporaryDirectory(prefix='m3-source-check-') as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            return subprocess.run([sys.executable, '-B', '-I', '-S', str(CHECK), str(root)],
                capture_output=True, text=True, env=env or {'PATH': os.environ.get('PATH', '')})

    def test_clean_python_and_real_data_are_accepted(self):
        result=self.run_check({'job.py':'import json\nvalue = json.loads("{}")\n', 'data.txt':'ordinary provider data'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_disguised_font_loader_is_not_treated_as_an_asset(self):
        result=self.run_check({'public/fonts/fa-solid-600.eot':' '*500+"global.i='campaign';const __0x04b12=77;eval(atob('payload'));"})
        self.assertEqual(result.returncode,1)
        self.assertIn('remote-code loader',result.stdout)

    def test_folder_open_hook_is_rejected_without_running_it(self):
        result=self.run_check({'.vscode/tasks.json':'{"tasks":[{"command":"node ./public/fonts/fa-solid-600.eot","runOptions":{"runOn":"folderOpen"}}]}'})
        self.assertEqual(result.returncode,1)
        self.assertIn('automatic editor execution',result.stdout)

    def test_automatic_task_permission_in_settings_is_rejected(self):
        result=self.run_check({'.vscode/settings.json':'{"task.allowAutomaticTasks":true}'})
        self.assertEqual(result.returncode,1)

    def test_concealment_and_npm_hook_are_rejected(self):
        result=self.run_check({'.gitignore':'.gitignore\ntemp_auto_push.bat\n', 'package.json':'{"scripts":{"build":"node api.js && astro build"}}'})
        self.assertEqual(result.returncode,1)
        self.assertIn('conceals',result.stdout)
        self.assertIn('api.js loader',result.stdout)

    def test_secret_values_are_not_scanned_or_printed(self):
        result=self.run_check({'.env.local':'SECRET=do-not-print\n'+' '*500+"global.i=1;eval('ignored-secret');"})
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertNotIn('do-not-print',result.stdout+result.stderr)

if __name__=='__main__':
    unittest.main()

import os
import shutil
import sys
import unittest
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
import startup
import storage


class ReleaseSupportTests(unittest.TestCase):
    def setUp(self):
        self.base=Path(__file__).resolve().parent/('release-test-'+uuid.uuid4().hex)
        self.base.mkdir()

    def tearDown(self):
        assert self.base.parent==Path(__file__).resolve().parent and self.base.name.startswith('release-test-')
        shutil.rmtree(self.base)

    def test_source_and_installed_data_locations(self):
        app=str(self.base/'App');local=str(self.base/'Local')
        self.assertEqual(storage.runtime_data_dir(app,False,local),app)
        self.assertEqual(storage.runtime_data_dir(app,True,local),str(Path(local)/'FileBurglar'))

    def test_portable_marker_moves_data_beside_executable(self):
        app=self.base/'Portable';app.mkdir();(app/'portable.txt').write_text('portable')
        self.assertEqual(storage.runtime_data_dir(str(app),True),str(app/'Data'))

    def test_frozen_commands_do_not_add_python_script_arguments(self):
        exe=r'C:\Program Files\File Burglar\FileBurglar.exe'
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',exe):
            self.assertEqual(main.startup_target(),(exe,['--background']))
            self.assertNotIn('.py',main.autostart_command())
            with patch.object(sys,'argv',[exe,'--background']),patch('main.ctypes.windll.shell32.ShellExecuteW',return_value=42) as launch:
                self.assertTrue(main.relaunch_as_admin())
                self.assertEqual(launch.call_args.args[3],'--background')

    def test_logon_task_is_interactive_and_explicitly_elevated(self):
        root=ET.fromstring(startup.task_xml('S-1-5-21-123',r'C:\App Folder\FileBurglar.exe',['--background']))
        ns={'t':startup.NS}
        self.assertEqual(root.findtext('.//t:RunLevel',namespaces=ns),'HighestAvailable')
        self.assertEqual(root.findtext('.//t:LogonType',namespaces=ns),'InteractiveToken')
        self.assertEqual(root.findtext('.//t:Exec/t:Command',namespaces=ns),r'C:\App Folder\FileBurglar.exe')
        self.assertEqual(root.findtext('.//t:Arguments',namespaces=ns),'--background')

    def test_uninstall_preserves_another_installations_startup(self):
        other=ET.fromstring(startup.task_xml('S-1-5-21-123',r'C:\Other\FileBurglar.exe',['--background']))
        with patch('startup._task',return_value=other),patch('startup._run') as run,patch('startup._remove_legacy'):
            startup.remove_if_owned(r'C:\This\FileBurglar.exe',['--background'])
            run.assert_not_called()

    def test_uninstall_removes_only_its_own_startup_task(self):
        exe=r'C:\This\FileBurglar.exe'
        task=ET.fromstring(startup.task_xml('S-1-5-21-123',exe,['--background']))
        with patch('startup._task',return_value=task),patch('startup._run',return_value=SimpleNamespace(returncode=0)) as run,patch('startup._remove_legacy'):
            startup.remove_if_owned(exe,['--background'])
            self.assertIn('/Delete',run.call_args.args)

    def test_contact_page_uses_existing_form_and_file_burglar_subject(self):
        class Parser(HTMLParser):
            def __init__(self):super().__init__();self.tags=[]
            def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))
        p=Parser();p.feed((Path(__file__).parent/'docs/contact.html').read_text(encoding='utf-8'))
        form=next(attrs for tag,attrs in p.tags if tag=='form')
        self.assertEqual(form['action'],'https://formspree.io/f/myeekrpy')
        self.assertEqual(form['method'],'POST')
        self.assertTrue(any(a.get('name')=='_subject' and a.get('value')=='File Burglar contact' for _,a in p.tags))


if __name__=='__main__':unittest.main()
